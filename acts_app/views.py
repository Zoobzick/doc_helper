# acts_app/views.py
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from acts_app.forms import ActAttachmentFormSet, ActForm, ActMaterialFormSet, ActProjectsForm
from acts_app.models import Act, ActStatus
from acts_app.services.appendix_builder import AppendixBuilder, AppendixBuilderError


def _first_project_id_from_cleaned_projects(projects) -> int | None:
    try:
        first = projects.order_by("id").first()
        return first.id if first else None
    except Exception:
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
            qs = qs.filter(number__icontains=q)

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

            # то, что будет вставляться в строку материала в акте
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

        material_fs = ActMaterialFormSet(prefix="mat")
        attach_fs = ActAttachmentFormSet(prefix="att", act_number="")

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
        attach_fs = ActAttachmentFormSet(
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
