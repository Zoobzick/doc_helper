# acts_app/views.py
from __future__ import annotations

from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from acts_app.forms import (
    ActAttachmentFormSet,
    ActAttachmentCreateFormSet,
    ActForm,
    ActMaterialFormSet,
    ActProjectsForm,
)
from acts_app.models import Act, ActStatus, AttachmentType
from acts_app.services.appendix_builder import AppendixBuilder, AppendixBuilderError

try:
    # Для поиска по проекту в строке q (шифр/код/название проекта).
    from projects_app.models import Project
except Exception:  # pragma: no cover
    Project = None


def _first_project_id_from_cleaned_projects(projects) -> int | None:
    try:
        first = projects.order_by("id").first()
        return first.id if first else None
    except Exception:
        return None


def _fmt_date(d) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


def _parse_search_date(raw: str):
    """Парсим дату из строки поиска q.

    Поддерживаем:
      - 02.02.2026
      - 02-02-2026
      - 2026-02-02
      - 02/02/2026

    Возвращает date или None.
    """
    s = (raw or "").strip()
    if not s:
        return None

    for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    return None


class ActListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "acts_app.view_act"
    model = Act
    template_name = "acts_app/act_list.html"
    context_object_name = "acts"
    paginate_by = 20

    def get_queryset(self):
        qs = Act.objects.prefetch_related("projects").order_by("-act_date", "number")

        q = (self.request.GET.get("q") or "").strip()
        if q:
            # filters: общий фильтр поиска
            # - номер акта: number__icontains
            # - дата акта: act_date == parsed_date
            # - год: act_date__year == YYYY
            # - проект: projects__<field>__icontains (подбираем поле динамически)
            filters = Q(number__icontains=q)

            parsed_date = _parse_search_date(q)
            if parsed_date:
                filters |= Q(act_date=parsed_date)

            if q.isdigit() and len(q) == 4:
                filters |= Q(act_date__year=int(q))

            # Поиск по проекту (шифр/код/название).
            # Важно: не хардкодим одно поле, чтобы не словить FieldError.
            if Project is not None:
                project_fields = {
                    f.name
                    for f in Project._meta.get_fields()
                    if getattr(f, "concrete", False)
                }

                proj_q = Q()
                # candidates: вероятные названия поля шифра/кода/названия
                for fname in ("full_code", "code", "cipher", "number", "name", "title", "short_name"):
                    if fname in project_fields:
                        proj_q |= Q(**{f"projects__{fname}__icontains": q})

                if proj_q:
                    filters |= proj_q

            qs = qs.filter(filters).distinct()

        project_id = (self.request.GET.get("project") or "").strip()
        if project_id.isdigit():
            qs = qs.filter(projects__id=int(project_id)).distinct()

        status = (self.request.GET.get("status") or "").strip()
        if status in {ActStatus.DRAFT, ActStatus.FINAL}:
            qs = qs.filter(status=status)

        return qs


class ActDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "acts_app.view_act"
    model = Act
    template_name = "acts_app/act_detail.html"
    context_object_name = "act"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    def get_queryset(self):
        return Act.objects.prefetch_related("projects", "materials", "attachments", "appendix_lines")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        act: Act = ctx["act"]

        appendix_rows: list[dict] = []
        for line in act.appendix_lines.all():
            label = (line.label or "").strip()
            sheets = int(line.sheets_count or 0)

            row: dict = {
                "label": label,
                "sheets": sheets,
                "children": [],
            }

            # --- 1) Реестр материалов: раскрываем состав ---
            src = getattr(line, "source_attachment", None)
            if src is not None and getattr(src, "type", None) == AttachmentType.MATERIALS_REGISTRY:
                children = []
                for mi in act.materials.all():
                    if mi.passport_id and mi.passport:
                        doc_name = (getattr(mi.passport, "document_name", "") or "").strip()
                        doc_date = getattr(mi.passport, "document_date", None)
                        material_name = ""
                        material = getattr(mi.passport, "material", None)
                        if material is not None:
                            material_name = (getattr(material, "name", "") or "").strip()

                        child_label = f"{doc_name} от {_fmt_date(doc_date)}, {material_name}".strip().strip(",")
                    else:
                        # ручной ввод
                        doc_name = (mi.note or "").strip()  # note = Наименование документа
                        doc_no = (mi.manual_doc_no or "").strip()
                        doc_date = mi.manual_doc_date
                        material_name = (mi.manual_name or "").strip()

                        left = doc_name or doc_no or "—"
                        child_label = f"{left} от {_fmt_date(doc_date)}, {material_name}".strip().strip(",")

                    children.append(
                        {
                            "label": child_label or "—",
                            "sheets": int(mi.sheets_count or 0),
                        }
                    )

                row["children"] = children
                appendix_rows.append(row)
                continue

            # --- 2) Реестр документов соответствия (П-4): раскрываем состав ---
            if src is not None and hasattr(AttachmentType, "DOCS_REGISTRY"):
                if getattr(src, "type", None) == AttachmentType.DOCS_REGISTRY:
                    exclude_types = [
                        AttachmentType.EXEC_SCHEME,
                        AttachmentType.MATERIALS_REGISTRY,
                        AttachmentType.DOCS_REGISTRY,
                    ]
                    children = []
                    for a in act.attachments.exclude(type__in=exclude_types).order_by("created_at"):
                        title = (a.title or "").strip() or "—"
                        doc_no = (a.doc_no or "").strip()
                        doc_date = a.doc_date
                        parts = [title]
                        if doc_no:
                            parts.append(f"№{doc_no}")
                        parts.append(f"от {_fmt_date(doc_date)}")
                        children.append({"label": " ".join(parts), "sheets": int(a.sheets_count or 0)})
                    row["children"] = children
                    appendix_rows.append(row)
                    continue

            # --- 3) Старое поведение: "Материалы ..." разворачиваем в список строк (как было у тебя) ---
            if label == "Материалы (паспорта/сертификаты качества)":
                for mi in act.materials.all():
                    if mi.passport_id and mi.passport:
                        doc_name = (getattr(mi.passport, "document_name", "") or "").strip()
                        doc_date = getattr(mi.passport, "document_date", None)
                        material_name = ""
                        material = getattr(mi.passport, "material", None)
                        if material is not None:
                            material_name = (getattr(material, "name", "") or "").strip()

                        date_str = _fmt_date(doc_date)
                        expanded_label = f"{doc_name} от {date_str}, {material_name}".strip().strip(",")
                    else:
                        doc_name = (mi.note or "").strip()  # note = Наименование документа
                        doc_no = (mi.manual_doc_no or "").strip()
                        doc_date = mi.manual_doc_date
                        material_name = (mi.manual_name or "").strip()
                        left = doc_name or doc_no or "—"
                        date_str = _fmt_date(doc_date)
                        expanded_label = f"{left} от {date_str}, {material_name}".strip().strip(",")

                    appendix_rows.append(
                        {
                            "label": expanded_label or "—",
                            "sheets": int(mi.sheets_count),
                            "children": [],
                        }
                    )
                continue

            # --- 4) Обычная строка ---
            appendix_rows.append(row)

        ctx["appendix_rows"] = [{"pos": i + 1, **r} for i, r in enumerate(appendix_rows)]
        return ctx


class PassportsDatatableView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Server-side DataTables JSON
    GET params:
      draw, start, length, search[value]
    """
    permission_required = "acts_app.add_act"

    def get(self, request: HttpRequest) -> JsonResponse:
        from passports_app.models import Passport  # noqa: WPS433

        draw = int(request.GET.get("draw") or 1)
        start = int(request.GET.get("start") or 0)
        length = int(request.GET.get("length") or 10)
        length = min(max(length, 5), 50)

        search = (request.GET.get("search[value]") or "").strip()

        base_qs = Passport.objects.select_related("material").only(
            "id",
            "document_name",
            "document_number",
            "document_date",
            "material__name",
        )

        records_total = base_qs.count()

        qs = base_qs
        if search:
            qs = qs.filter(
                Q(material__name__icontains=search)
                | Q(document_name__icontains=search)
                | Q(document_number__icontains=search)
            )

        records_filtered = qs.count()

        qs = qs.order_by("-id")[start:start + length]

        data = []
        for p in qs:
            material = p.material.name if p.material_id and p.material else "—"
            doc_name = (p.document_name or "").strip() or "—"
            doc_no = (p.document_number or "").strip() or "—"
            doc_date = p.document_date.strftime("%d.%m.%Y") if p.document_date else "—"
            label = f"{material} — {doc_name} №{doc_no} от {doc_date}"

            data.append(
                {
                    "id": p.id,
                    "material": material,
                    "doc_name": doc_name,
                    "doc_no": doc_no,
                    "doc_date": doc_date,
                    "label": label,
                }
            )

        return JsonResponse(
            {
                "draw": draw,
                "recordsTotal": records_total,
                "recordsFiltered": records_filtered,
                "data": data,
            }
        )


class ActCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.add_act"
    template_name = "acts_app/act_form.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        projects_form = ActProjectsForm()
        act_form = ActForm()

        # ActMaterialFormSet.extra = 0 -> на create показываем 1 пустую строку через initial
        material_fs = ActMaterialFormSet(prefix="mat", initial=[{}])
        attach_fs = ActAttachmentCreateFormSet(prefix="att", act_number="")

        return render(
            request,
            self.template_name,
            {
                "projects_form": projects_form,
                "form": act_form,
                "material_formset": material_fs,
                "attachment_formset": attach_fs,
                "mode": "create",
            },
        )

    @transaction.atomic
    def post(self, request: HttpRequest) -> HttpResponse:
        projects_form = ActProjectsForm(request.POST)
        act_form = ActForm(request.POST)

        project_id_hint = None
        if projects_form.is_valid():
            project_id_hint = _first_project_id_from_cleaned_projects(projects_form.cleaned_data["projects"])

        material_fs = ActMaterialFormSet(
            request.POST,
            prefix="mat",
            form_kwargs={"project_id": project_id_hint},
        )

        act_number_hint = (request.POST.get("number") or "").strip()
        attach_fs = ActAttachmentCreateFormSet(
            request.POST,
            request.FILES,
            prefix="att",
            act_number=act_number_hint,
        )

        if not (projects_form.is_valid() and act_form.is_valid() and material_fs.is_valid() and attach_fs.is_valid()):
            return render(
                request,
                self.template_name,
                {
                    "projects_form": projects_form,
                    "form": act_form,
                    "material_formset": material_fs,
                    "attachment_formset": attach_fs,
                    "mode": "create",
                },
            )

        act = act_form.save()
        act.projects.set(projects_form.cleaned_data["projects"])

        material_fs.instance = act
        attach_fs.instance = act

        material_fs.save()
        attach_fs.save()

        try:
            AppendixBuilder(act).rebuild()
            messages.success(request, "Акт сохранён. Приложения пересобраны.")
        except AppendixBuilderError as e:
            messages.warning(request, f"Акт сохранён, но приложения не пересобраны: {e}")

        return redirect("acts_app:act_detail", uuid=str(act.uuid))


class ActUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"
    template_name = "acts_app/act_form.html"

    def get_object(self, uuid: str) -> Act:
        return get_object_or_404(Act, uuid=uuid)

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = self.get_object(uuid)

        projects_form = ActProjectsForm(initial={"projects": act.projects.all()})
        act_form = ActForm(instance=act)

        first_project = act.projects.order_by("id").first()
        project_id_hint = first_project.id if first_project else None

        material_fs = ActMaterialFormSet(
            instance=act,
            prefix="mat",
            form_kwargs={"project_id": project_id_hint},
        )

        attach_fs = ActAttachmentFormSet(instance=act, prefix="att", act_number=act.number)

        return render(
            request,
            self.template_name,
            {
                "act": act,
                "projects_form": projects_form,
                "form": act_form,
                "material_formset": material_fs,
                "attachment_formset": attach_fs,
                "mode": "update",
            },
        )

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = self.get_object(uuid)

        projects_form = ActProjectsForm(request.POST)
        act_form = ActForm(request.POST, instance=act)

        project_id_hint = None
        if projects_form.is_valid():
            project_id_hint = _first_project_id_from_cleaned_projects(projects_form.cleaned_data["projects"])

        material_fs = ActMaterialFormSet(
            request.POST,
            instance=act,
            prefix="mat",
            form_kwargs={"project_id": project_id_hint},
        )

        act_number_hint = (request.POST.get("number") or act.number or "").strip()
        attach_fs = ActAttachmentFormSet(
            request.POST,
            request.FILES,
            instance=act,
            prefix="att",
            act_number=act_number_hint,
        )

        if not (projects_form.is_valid() and act_form.is_valid() and material_fs.is_valid() and attach_fs.is_valid()):
            return render(
                request,
                self.template_name,
                {
                    "act": act,
                    "projects_form": projects_form,
                    "form": act_form,
                    "material_formset": material_fs,
                    "attachment_formset": attach_fs,
                    "mode": "update",
                },
            )

        act = act_form.save()
        act.projects.set(projects_form.cleaned_data["projects"])

        material_fs.save()
        attach_fs.save()

        try:
            AppendixBuilder(act).rebuild()
            messages.success(request, "Изменения сохранены. Приложения пересобраны.")
        except AppendixBuilderError as e:
            messages.warning(request, f"Изменения сохранены, но приложения не пересобраны: {e}")

        return redirect("acts_app:act_detail", uuid=str(act.uuid))


class ActRebuildAppendixView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)
        try:
            AppendixBuilder(act).rebuild()
            messages.success(request, "Приложения пересобраны.")
        except AppendixBuilderError as e:
            messages.error(request, f"Не удалось пересобрать приложения: {e}")
        return redirect("acts_app:act_detail", uuid=str(act.uuid))


class PassportsLabelsView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Для /edit/: отдать подписи для паспортов по списку id.
    GET ?ids=1,2,3
    """
    permission_required = "acts_app.change_act"

    def get(self, request: HttpRequest) -> JsonResponse:
        ids_raw = (request.GET.get("ids") or "").strip()
        if not ids_raw:
            return JsonResponse({"labels": {}})

        try:
            ids = [int(x) for x in ids_raw.split(",") if x.strip().isdigit()]
        except Exception:
            ids = []

        if not ids:
            return JsonResponse({"labels": {}})

        from passports_app.models import Passport  # noqa: WPS433

        qs = (
            Passport.objects
            .select_related("material")
            .filter(id__in=ids)
            .only("id", "document_name", "document_number", "document_date", "material__name")
        )

        labels = {}
        for p in qs:
            material = p.material.name if p.material_id and p.material else "—"
            doc_name = (p.document_name or "").strip() or "—"
            doc_no = (p.document_number or "").strip() or "—"
            doc_date = p.document_date.strftime("%d.%m.%Y") if p.document_date else "—"
            labels[str(p.id)] = f"{material} — {doc_name} №{doc_no} от {doc_date}"

        return JsonResponse({"labels": labels})
