from __future__ import annotations

from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Q, Max
from django.http import HttpRequest, HttpResponse, JsonResponse, FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView

from acts_app.forms import (
    ActAttachmentFormSet,
    ActAttachmentCreateFormSet,
    ActForm,
    ActMaterialFormSet,
    ActProjectsForm,
)
from acts_app.models import (
    Act,
    ActStatus,
    AttachmentType,
    ActParty,
    ActApprovalItem,
)
from acts_app.services.act_docx_generator import generate_act_docx, DocxRenderError, get_act_docx_paths
from acts_app.services.appendix_builder import AppendixBuilder, AppendixBuilderError
from acts_app.services.date_format import fmt_date_range_g
from acts_app.services.material_resolver import resolve_material_fields
from acts_app.services.signatories import (
    resolve_act_parties,
    resolve_party,
    get_candidates_for_party,
    choose_authorization_for_party,
    reset_choices_for_act_on_date_change,
    validate_before_finalize,
    freeze_signatories_to_snapshots,
)
from directive_app.models import ActRole

try:
    from projects_app.models import Project
except Exception:  # pragma: no cover
    Project = None


# -------------------------
# helpers
# -------------------------

def _parse_int_list(values: list[str]) -> list[int]:
    out: list[int] = []
    for v in values:
        s = (v or "").strip()
        if s.isdigit():
            out.append(int(s))
    return out


def _parse_int_list_from_post(request: HttpRequest, name: str) -> list[int]:
    return _parse_int_list(request.POST.getlist(name))


def _fmt_date(d) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


def _parse_search_date(raw: str):
    s = (raw or "").strip()
    if not s:
        return None

    for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_iso_date(raw: str):
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _organizations_qs():
    from orgs_app.models import Organization  # noqa: WPS433
    return Organization.objects.filter(is_active=True).order_by("short_name")


def _first_project_id_from_cleaned_projects(projects) -> int | None:
    try:
        first = projects.order_by("id").first()
        return first.id if first else None
    except Exception:
        return None


def _project_label(p) -> str:
    def _get_first_attr(obj, names: tuple[str, ...]) -> str:
        for n in names:
            if hasattr(obj, n):
                val = (getattr(obj, n) or "").strip()
                if val:
                    return val
        return ""

    code = _get_first_attr(p, ("full_code", "code", "cipher", "number"))
    name = _get_first_attr(p, ("name", "title", "short_name"))

    if code and name:
        return f"{code} — {name}"
    return code or name or str(p)


def _set_projects_queryset_for_form(projects_form: ActProjectsForm, selected_ids: list[int]) -> None:
    if Project is None:
        return
    projects_form.fields["projects"].queryset = Project.objects.filter(id__in=selected_ids).order_by("id")


def _selected_projects_for_render(selected_ids: list[int]) -> list[dict]:
    if Project is None or not selected_ids:
        return []
    qs = Project.objects.filter(id__in=selected_ids).order_by("id")
    return [{"id": p.id, "label": _project_label(p)} for p in qs]


def _approval_auto_label(a) -> str:
    parts = []
    if getattr(a, "project", None):
        parts.append(a.project.full_code)

    if hasattr(a, "get_status_display"):
        parts.append(a.get_status_display())

    if getattr(a, "created_at", None):
        parts.append(a.created_at.strftime("%d.%m.%Y"))

    desc = (getattr(a, "description", "") or "").strip()
    if desc:
        parts.append(desc)

    return " — ".join(parts)


def _approval_label_short(a) -> str:
    proj = getattr(a, "project", None)
    proj_code = (getattr(proj, "full_code", "") or "").strip() if proj else ""
    proj_part = proj_code or "Общее"

    status_part = ""
    if hasattr(a, "get_status_display"):
        status_part = (a.get_status_display() or "").strip()

    dt = getattr(a, "created_at", None)
    date_part = dt.strftime("%d.%m.%Y") if dt else "—"

    desc = (getattr(a, "description", "") or "").strip()
    if len(desc) > 60:
        desc = desc[:60].rstrip() + "…"

    parts = [proj_part]
    if status_part:
        parts.append(status_part)
    parts.append(date_part)
    if desc:
        parts.append(desc)

    return " — ".join(parts)


DEFAULT_PARTY_ROLES = [
    ActRole.TECH_CUSTOMER_CONTROL,
    ActRole.BUILDER_REP,
    ActRole.BUILDER_CONTROL,
    ActRole.DESIGN_REP,
    ActRole.CONTRACTOR_REP,
]


def _get_last_act_for_user_or_global(*, user, exclude_act_id: int | None = None) -> Act | None:
    qs = Act.objects.all().order_by("-created_at", "-id")

    if exclude_act_id is not None:
        qs = qs.exclude(id=exclude_act_id)

    if hasattr(Act, "created_by_id"):
        qs = qs.filter(created_by=user)

    return qs.first()


@transaction.atomic
def ensure_default_parties_for_act(*, act: Act, user) -> None:
    if act.parties.exists():
        return

    prev = _get_last_act_for_user_or_global(user=user, exclude_act_id=act.id)

    prev_parties = []
    if prev:
        prev_parties = list(prev.parties.select_related("organization").order_by("position", "id"))

    prev_by_role: dict[str, ActParty] = {}
    prev_other: list[ActParty] = []

    for p in prev_parties:
        if p.role == ActRole.OTHER_REP:
            prev_other.append(p)
        else:
            prev_by_role[p.role] = p

    pos = 1
    to_create: list[ActParty] = []

    for role in DEFAULT_PARTY_ROLES:
        prev_p = prev_by_role.get(role)
        to_create.append(
            ActParty(
                act=act,
                role=role,
                organization=getattr(prev_p, "organization", None),
                is_enabled=getattr(prev_p, "is_enabled", True),
                position=pos,
                chosen_authorization=None,
            )
        )
        pos += 1

    if prev_other:
        for op in prev_other:
            to_create.append(
                ActParty(
                    act=act,
                    role=ActRole.OTHER_REP,
                    organization=op.organization,
                    is_enabled=op.is_enabled,
                    position=pos,
                    chosen_authorization=None,
                )
            )
            pos += 1
    else:
        to_create.append(
            ActParty(
                act=act,
                role=ActRole.OTHER_REP,
                organization=None,
                is_enabled=True,
                position=pos,
                chosen_authorization=None,
            )
        )

    ActParty.objects.bulk_create(to_create)


def _act_parties_context(act: Act) -> dict:
    return {
        "act": act,
        "resolved_parties": resolve_act_parties(act),
        "organizations": list(_organizations_qs()),
        "preview_date": None,
    }


def _act_parties_context_for_date(act: Act, date_override) -> dict:
    parties = list(act.parties.select_related("organization", "chosen_authorization").order_by("position", "id"))
    resolved = [resolve_party(p, date_override) for p in parties]
    return {
        "act": act,
        "resolved_parties": resolved,
        "organizations": list(_organizations_qs()),
        "preview_date": date_override,
    }


def _approval_items_from_post(request: HttpRequest) -> list[dict]:
    items = []
    for aid in _parse_int_list_from_post(request, "approvals"):
        items.append(
            {
                "id": int(aid),
                "label": (request.POST.get(f"approval_label_{aid}", "") or "").strip(),
                "sheets": int(request.POST.get(f"approval_sheets_{aid}", 1)),
            }
        )
    return items


def _save_approval_items(*, act: Act, request: HttpRequest) -> None:
    approval_ids = _parse_int_list_from_post(request, "approvals")

    ActApprovalItem.objects.filter(act=act).delete()

    pos = 1
    for approval_id in approval_ids:
        ActApprovalItem.objects.create(
            act=act,
            approval_id=approval_id,
            position=pos,
            sheets_count=int(request.POST.get(f"approval_sheets_{approval_id}", 1)),
            label_override=(request.POST.get(f"approval_label_{approval_id}", "") or "").strip(),
        )
        pos += 1

    if hasattr(act, "approvals"):
        act.approvals.clear()


# -------------------------
# NEW: projects ajax search
# -------------------------

class ProjectsSearchView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = ("acts_app.add_act", "acts_app.change_act")

    def has_permission(self):
        user = self.request.user
        return user.has_perm("acts_app.add_act") or user.has_perm("acts_app.change_act")

    def get(self, request: HttpRequest) -> JsonResponse:
        if Project is None:
            return JsonResponse({"results": []})

        q = (request.GET.get("q") or "").strip()

        project_fields = {f.name for f in Project._meta.get_fields() if getattr(f, "concrete", False)}

        def project_label(p) -> str:
            code = ""
            for name in ("full_code", "code", "cipher", "number"):
                if name in project_fields:
                    code = (getattr(p, name, "") or "").strip()
                    if code:
                        break

            title = ""
            for name in ("name", "title", "short_name"):
                if name in project_fields:
                    title = (getattr(p, name, "") or "").strip()
                    if title:
                        break

            if code and title:
                return f"{code} — {title}"
            return code or title or str(p)

        if not q:
            act_fields = {f.name for f in Act._meta.get_fields() if getattr(f, "concrete", False)}
            if "updated_at" in act_fields:
                dt_field = "updated_at"
            elif "created_at" in act_fields:
                dt_field = "created_at"
            elif "act_date" in act_fields:
                dt_field = "act_date"
            else:
                dt_field = "id"

            qs = (
                     Project.objects.filter(acts__isnull=False)
                     .annotate(last_used=Max(f"acts__{dt_field}"))
                     .order_by("-last_used", "-id")
                     .distinct()
                 )[:5]

            return JsonResponse({"results": [{"id": p.id, "label": project_label(p)} for p in qs]})

        qs = Project.objects.all()
        cond = Q()
        for fname in ("full_code", "code", "cipher", "number", "name", "title", "short_name"):
            if fname in project_fields:
                cond |= Q(**{f"{fname}__icontains": q})

        if cond:
            qs = qs.filter(cond)

        qs = qs.order_by("id")[:30]
        return JsonResponse({"results": [{"id": p.id, "label": project_label(p)} for p in qs]})


class ApprovalsDatatableView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.add_act"

    def get(self, request: HttpRequest) -> JsonResponse:
        from approvals_app.models import Approval  # noqa: WPS433

        draw = int(request.GET.get("draw") or 1)
        start = int(request.GET.get("start") or 0)
        length = int(request.GET.get("length") or 10)
        length = min(max(length, 5), 50)

        search = (request.GET.get("search[value]") or "").strip()

        base_qs = Approval.objects.select_related("project").only(
            "id",
            "status",
            "created_at",
            "description",
            "project__full_code",
        )

        records_total = base_qs.count()

        qs = base_qs
        if search:
            qs = qs.filter(
                Q(project__full_code__icontains=search)
                | Q(description__icontains=search)
                | Q(status__icontains=search)
            )

        records_filtered = qs.count()
        qs = qs.order_by("-created_at", "-id")[start:start + length]

        data = []
        for a in qs:
            proj = a.project.full_code if a.project_id and a.project else "Общее"
            status = a.get_status_display() if hasattr(a, "get_status_display") else (a.status or "")
            created = a.created_at.strftime("%d.%m.%Y") if a.created_at else "—"
            desc_full = (a.description or "").strip()  # полный текст, без обрезки
            desc_short = desc_full or "—"

            data.append(
                {
                    "id": a.id,
                    "project": proj,
                    "status": status,
                    "created_at": created,
                    "description": desc_short,  # в таблицу (можно оставить коротко)
                    "description_full": desc_full,  # ✅ то, что будем подставлять в textarea
                    "label": _approval_label_short(a),
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


# -------------------------
# list/detail
# -------------------------

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
            filters = Q(number__icontains=q)

            parsed_date = _parse_search_date(q)
            if parsed_date:
                filters |= Q(act_date=parsed_date)

            if q.isdigit() and len(q) == 4:
                filters |= Q(act_date__year=int(q))

            if Project is not None:
                project_fields = {f.name for f in Project._meta.get_fields() if getattr(f, "concrete", False)}

                proj_q = Q()
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
        return Act.objects.prefetch_related(
            "projects",
            "materials",
            "materials__passport",
            "materials__passport__material",
            "attachments",
            "appendix_lines",
            "parties",
            "signatory_snapshots",
            "approval_items",
            "approval_items__approval",
            "approval_items__approval__project",
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        act: Act = ctx["act"]

        appendix_rows: list[dict] = []
        for line in act.appendix_lines.all():
            label = (line.label or "").strip()
            sheets = int(line.sheets_count or 0)

            row: dict = {"label": label, "sheets": sheets, "children": []}

            src = getattr(line, "source_attachment", None)

            # ✅ MATERIALS_REGISTRY: дети строим через resolve_material_fields(mi)
            if src is not None and getattr(src, "type", None) == AttachmentType.MATERIALS_REGISTRY:
                children = []
                for mi in act.materials.all().order_by("position", "id"):
                    data = resolve_material_fields(mi)

                    parts = [data["document_name"]]

                    if data["document_no"]:
                        parts.append(f"№{data['document_no']}")

                    if data["document_date_str"] and data["document_date_str"] != "—":
                        parts.append(f"от {data['document_date_str']}")

                    base = " ".join(parts).strip()
                    mat = (data["material_name"] or "").strip()

                    child_label = f"{base}, {mat}".strip().strip(",")

                    children.append({"label": child_label or "—", "sheets": int(mi.sheets_count or 0)})

                row["children"] = children
                appendix_rows.append(row)
                continue

            # DOCS_REGISTRY
            if src is not None and hasattr(AttachmentType, "DOCS_REGISTRY"):
                if getattr(src, "type", None) == AttachmentType.DOCS_REGISTRY:
                    exclude_types = [
                        AttachmentType.EXEC_SCHEME,
                        AttachmentType.MATERIALS_REGISTRY,
                        AttachmentType.DOCS_REGISTRY,
                        getattr(AttachmentType, "APPROVALS_REGISTRY", "APPROVALS_REGISTRY"),
                    ]
                    children = []
                    for a in act.attachments.exclude(type__in=exclude_types).order_by("created_at"):
                        title = (a.title or "").strip() or "—"
                        doc_no = (a.doc_no or "").strip()
                        parts = [title]
                        if doc_no:
                            parts.append(f"№{doc_no}")

                        date_str = fmt_date_range_g(a.doc_date, getattr(a, "doc_date_to", None))
                        if date_str:
                            parts.append(f"от {date_str}")

                        children.append({"label": " ".join(parts), "sheets": int(a.sheets_count or 0)})

                    row["children"] = children
                    appendix_rows.append(row)
                    continue

            # APPROVALS_REGISTRY
            if src is not None and hasattr(AttachmentType, "APPROVALS_REGISTRY"):
                if getattr(src, "type", None) == AttachmentType.APPROVALS_REGISTRY:
                    children = []
                    for it in act.approval_items.all().order_by("position", "id"):
                        label = (it.label_override or "").strip()
                        if not label and getattr(it, "approval", None) is not None:
                            label = (getattr(it.approval, "description", "") or "").strip()

                        children.append(
                            {
                                "label": label or "—",
                                "sheets": int(it.sheets_count or 0),
                            }
                        )

                    row["children"] = children
                    appendix_rows.append(row)
                    continue

            appendix_rows.append(row)

        ctx["appendix_rows"] = [{"pos": i + 1, **r} for i, r in enumerate(appendix_rows)]

        if act.status == ActStatus.FINAL and act.signatory_snapshots.exists():
            ctx["signatories_source"] = "snapshot"
            ctx["signatories"] = list(act.signatory_snapshots.order_by("position", "id"))
        else:
            ctx["signatories_source"] = "resolved"
            ctx["resolved_parties"] = resolve_act_parties(act)

        return ctx


# -------------------------
# passports ajax
# -------------------------

class PassportsDatatableView(LoginRequiredMixin, PermissionRequiredMixin, View):
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


# -------------------------
# create/update
# -------------------------

class ActCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.add_act"
    template_name = "acts_app/act_form.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        projects_form = ActProjectsForm()
        _set_projects_queryset_for_form(projects_form, selected_ids=[])

        act_form = ActForm()

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
                "selected_projects": [],
                "approval_items": [],
            },
        )

    @transaction.atomic
    def post(self, request: HttpRequest) -> HttpResponse:
        posted_ids = _parse_int_list(request.POST.getlist("projects"))

        projects_form = ActProjectsForm(request.POST)
        _set_projects_queryset_for_form(projects_form, selected_ids=posted_ids)

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
                    "selected_projects": _selected_projects_for_render(posted_ids),
                    "approval_items": _approval_items_from_post(request),
                },
            )

        act = act_form.save()

        if hasattr(act, "created_by_id") and not getattr(act, "created_by_id", None):
            act.created_by = request.user
            act.save(update_fields=["created_by"])

        act.projects.set(projects_form.cleaned_data["projects"])

        _save_approval_items(act=act, request=request)

        ensure_default_parties_for_act(act=act, user=request.user)

        material_fs.instance = act
        attach_fs.instance = act
        material_fs.save()
        attach_fs.save()

        try:
            AppendixBuilder(act).rebuild()
            generate_act_docx(act)  # ✅ создаём/перезаписываем docx при сохранении
            messages.success(request, "Акт сохранён. Приложения пересобраны. DOCX обновлён.")
        except AppendixBuilderError as e:
            messages.warning(request, f"Акт сохранён, но приложения не пересобраны: {e}")
        except DocxRenderError as e:
            messages.warning(request, f"Акт сохранён, но DOCX не собран: {e}")

        url = reverse("acts_app:act_detail", kwargs={"uuid": str(act.uuid)})
        return redirect(url)


class ActUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"
    template_name = "acts_app/act_form.html"

    def get_object(self, uuid: str) -> Act:
        return get_object_or_404(Act, uuid=uuid)

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = self.get_object(uuid)
        ensure_default_parties_for_act(act=act, user=request.user)

        selected_ids = list(act.projects.values_list("id", flat=True))

        projects_form = ActProjectsForm(initial={"projects": act.projects.all()})
        _set_projects_queryset_for_form(projects_form, selected_ids=selected_ids)

        act_form = ActForm(instance=act)

        first_project = act.projects.order_by("id").first()
        project_id_hint = first_project.id if first_project else None

        material_fs = ActMaterialFormSet(
            instance=act,
            prefix="mat",
            form_kwargs={"project_id": project_id_hint},
        )

        attach_fs = ActAttachmentFormSet(instance=act, prefix="att", act_number=act.number)

        approval_items = [
            {
                "id": item.approval_id,
                "label": (item.label_override or _approval_auto_label(item.approval)),
                "sheets": int(item.sheets_count or 1),
            }
            for item in act.approval_items.select_related("approval", "approval__project").order_by("position", "id")
        ]

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
                "selected_projects": _selected_projects_for_render(selected_ids),
                "approval_items": approval_items,
                **_act_parties_context(act),
            },
        )

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = self.get_object(uuid)
        ensure_default_parties_for_act(act=act, user=request.user)

        old_date = act.act_date

        posted_ids = _parse_int_list(request.POST.getlist("projects"))

        projects_form = ActProjectsForm(request.POST)
        _set_projects_queryset_for_form(projects_form, selected_ids=posted_ids)

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
                    "selected_projects": _selected_projects_for_render(posted_ids),
                    "approval_items": _approval_items_from_post(request),
                    **_act_parties_context(act),
                },
            )

        act = act_form.save()
        act.projects.set(projects_form.cleaned_data["projects"])

        _save_approval_items(act=act, request=request)

        if act.act_date != old_date:
            reset_choices_for_act_on_date_change(act)

        material_fs.save()
        attach_fs.save()

        try:
            AppendixBuilder(act).rebuild()
            generate_act_docx(act)  # ✅ создаём/перезаписываем docx при сохранении
            messages.success(request, "Изменения сохранены. Приложения пересобраны. DOCX обновлён.")
        except AppendixBuilderError as e:
            messages.warning(request, f"Изменения сохранены, но приложения не пересобраны: {e}")
        except DocxRenderError as e:
            messages.warning(request, f"Изменения сохранены, но DOCX не собран: {e}")

        url = reverse("acts_app:act_detail", kwargs={"uuid": str(act.uuid)})
        return redirect(url)


# -------------------------
# Parties (HTMX/AJAX endpoints)
# -------------------------

class ActPartiesTableView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)
        ensure_default_parties_for_act(act=act, user=request.user)

        return render(
            request,
            "acts_app/partials/act_parties_table.html",
            _act_parties_context(act),
        )


class ActPartiesPreviewByDateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)
        ensure_default_parties_for_act(act=act, user=request.user)

        date_param = request.GET.get("date")
        date_override = _parse_iso_date(date_param)

        if not date_override:
            return render(
                request,
                "acts_app/partials/act_parties_table.html",
                _act_parties_context(act),
            )

        return render(
            request,
            "acts_app/partials/act_parties_table.html",
            _act_parties_context_for_date(act, date_override),
        )


class ActPartyRowView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    def get(self, request: HttpRequest, party_uuid: str) -> HttpResponse:
        party = get_object_or_404(ActParty, uuid=party_uuid)
        resolved = resolve_party(party, party.act.act_date)

        return render(
            request,
            "acts_app/partials/act_party_row.html",
            {
                "party": party,
                "resolved": resolved,
                "organizations": list(_organizations_qs()),
            },
        )


class ActPartyToggleEnabledView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    @transaction.atomic
    def post(self, request: HttpRequest, party_uuid: str) -> HttpResponse:
        party = get_object_or_404(ActParty, uuid=party_uuid)

        key = f"is_enabled_{party_uuid}"
        raw = (request.POST.get(key) or "").strip().lower()
        is_enabled = raw in {"1", "true", "yes", "on"}

        party.is_enabled = is_enabled
        if not is_enabled:
            party.chosen_authorization = None
        party.save(update_fields=["is_enabled", "chosen_authorization"])

        resolved = resolve_party(party, party.act.act_date)
        return render(
            request,
            "acts_app/partials/act_party_row.html",
            {"party": party, "resolved": resolved, "organizations": list(_organizations_qs())},
        )


class ActPartySetOrganizationView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    @transaction.atomic
    def post(self, request: HttpRequest, party_uuid: str) -> HttpResponse:
        party = get_object_or_404(ActParty, uuid=party_uuid)

        key = f"organization_id_{party_uuid}"
        org_id_raw = (request.POST.get(key) or "").strip()
        org_id = int(org_id_raw) if org_id_raw.isdigit() else None

        if org_id is None:
            party.organization = None
        else:
            from orgs_app.models import Organization  # noqa: WPS433
            party.organization = get_object_or_404(Organization, id=org_id)

        party.chosen_authorization = None
        party.save(update_fields=["organization", "chosen_authorization"])

        resolved = resolve_party(party, party.act.act_date)
        return render(
            request,
            "acts_app/partials/act_party_row.html",
            {"party": party, "resolved": resolved, "organizations": list(_organizations_qs())},
        )


class ActPartyCandidatesView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    def get(self, request: HttpRequest, party_uuid: str) -> HttpResponse:
        party = get_object_or_404(ActParty, uuid=party_uuid)
        candidates = get_candidates_for_party(party, party.act.act_date)

        return render(
            request,
            "acts_app/partials/act_party_candidates.html",
            {"party": party, "candidates": candidates},
        )


class ActPartyChooseAuthorizationView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    @transaction.atomic
    def post(self, request: HttpRequest, party_uuid: str) -> HttpResponse:
        party = get_object_or_404(ActParty, uuid=party_uuid)

        auth_id_raw = (request.POST.get("authorization_id") or "").strip()
        if not auth_id_raw.isdigit():
            messages.error(request, "Не выбрано полномочие.")
            resolved = resolve_party(party, party.act.act_date)
            return render(
                request,
                "acts_app/partials/act_party_row.html",
                {"party": party, "resolved": resolved, "organizations": list(_organizations_qs())},
            )

        try:
            choose_authorization_for_party(party=party, authorization_id=int(auth_id_raw))
        except Exception as e:
            messages.error(request, str(e))

        party.refresh_from_db()
        resolved = resolve_party(party, party.act.act_date)
        return render(
            request,
            "acts_app/partials/act_party_row.html",
            {"party": party, "resolved": resolved, "organizations": list(_organizations_qs())},
        )


class ActPartyAddOtherView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)
        ensure_default_parties_for_act(act=act, user=request.user)

        max_pos = act.parties.aggregate(m=Max("position")).get("m") or 0
        party = ActParty.objects.create(
            act=act,
            role=ActRole.OTHER_REP,
            organization=None,
            is_enabled=True,
            position=int(max_pos) + 1,
        )

        resolved = resolve_party(party, act.act_date)
        return render(
            request,
            "acts_app/partials/act_party_row.html",
            {"party": party, "resolved": resolved, "organizations": list(_organizations_qs())},
        )


class ActPartyDeleteOtherView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    @transaction.atomic
    def post(self, request: HttpRequest, party_uuid: str) -> HttpResponse:
        party = get_object_or_404(ActParty, uuid=party_uuid)

        if party.role != ActRole.OTHER_REP:
            messages.error(request, "Удалять можно только строки 'Иные лица'.")
            return HttpResponse(status=400)

        party.delete()
        return HttpResponse("")


# -------------------------
# Finalize
# -------------------------

class ActFinalizeView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)
        ensure_default_parties_for_act(act=act, user=request.user)

        try:
            validate_before_finalize(act)
        except Exception as e:
            messages.error(request, "Нельзя финализировать акт. Исправь блок подписантов.")
            try:
                from django.core.exceptions import ValidationError as DjangoValidationError
                if isinstance(e, DjangoValidationError):
                    for msg in e.messages:
                        messages.error(request, msg)
                else:
                    messages.error(request, str(e))
            except Exception:
                messages.error(request, str(e))
            return redirect("acts_app:act_update", uuid=str(act.uuid))

        freeze_signatories_to_snapshots(act)
        act.status = ActStatus.FINAL
        act.save(update_fields=["status"])

        messages.success(request, "Акт переведён в финальный. Подписанты зафиксированы.")
        return redirect("acts_app:act_detail", uuid=str(act.uuid))


# -------------------------
# rebuild appendix
# -------------------------

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


# -------------------------
# passports labels
# -------------------------

class PassportsLabelsView(LoginRequiredMixin, PermissionRequiredMixin, View):
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


class ActDocxDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)

        # 1) пробуем отдать уже сохранённый файл
        paths = get_act_docx_paths(act)
        for p in paths:
            if p.exists():
                return FileResponse(
                    open(p, "rb"),
                    as_attachment=True,
                    filename=p.name,
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )

        # 2) если файла нет — генерим и отдаём
        try:
            paths = generate_act_docx(act)
        except DocxRenderError as e:
            return HttpResponse(f"DOCX ERROR: {e}", status=500, content_type="text/plain; charset=utf-8")

        if not paths:
            return HttpResponse("DOCX ERROR: файл не был создан.", status=500, content_type="text/plain; charset=utf-8")

        p = paths[0]
        return FileResponse(
            open(p, "rb"),
            as_attachment=True,
            filename=p.name,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
