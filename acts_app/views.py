from __future__ import annotations

from datetime import datetime
import io
from pathlib import Path
import shutil
import subprocess
import zipfile
from dataclasses import dataclass

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import router, transaction
from django.db.models import Q, Max
from django.db.models.deletion import Collector, ProtectedError
from django.http import HttpRequest, HttpResponse, JsonResponse, FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DetailView, ListView

from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_sameorigin

from acts_app.forms import (
    ActAttachmentFormSet,
    ActAttachmentCreateFormSet,
    ActForm,
    AookForm,
    AookProtocolFormSet,
    ActMaterialFormSet,
    ActProjectsForm,
)
from acts_app.models import (
    Act,
    Aook,
    AookSourceAct,
    ActStatus,
    AttachmentType,
    ActParty,
    ActApprovalItem, ActAttachment, ActMaterialItem,
)

from acts_app.services.registry_p3_docx_generator import generate_and_save_registry_p3_docx, get_registry_p3_docx_paths
from acts_app.services.bulk_export import build_acts_bulk_export_zip
from acts_app.services.aook_xlsx_generator import generate_and_save_aook_files

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


def _build_bulk_export_filename(date_from, date_to) -> str:
    if date_from and date_to:
        period = f"{date_from:%d.%m.%Y}-{date_to:%d.%m.%Y}"
    elif date_from:
        period = f"from-{date_from:%d.%m.%Y}"
    elif date_to:
        period = f"to-{date_to:%d.%m.%Y}"
    else:
        period = "all"
    return f"acts_export_{period}.zip"


def _unlink_file(path: Path) -> None:
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        pass


def _delete_act_files(act: Act) -> None:
    paths: set[Path] = set()

    try:
        for path in get_act_docx_paths(act):
            paths.add(path)
            paths.add(path.with_suffix(".pdf"))
    except Exception:
        pass

    registries = list(act.attachments.filter(type=AttachmentType.MATERIALS_REGISTRY))
    for registry in registries:
        try:
            for path in get_registry_p3_docx_paths(act=act, registry=registry):
                paths.add(path)
                paths.add(path.with_suffix(".pdf"))
        except Exception:
            pass

    for attachment in act.attachments.all():
        file_field = getattr(attachment, "file", None)
        if file_field:
            try:
                file_field.delete(save=False)
            except Exception:
                pass

    for path in paths:
        _unlink_file(path)

    attachments_dir = Path(settings.MEDIA_ROOT) / "acts" / str(act.uuid)
    try:
        if attachments_dir.exists() and attachments_dir.is_dir():
            shutil.rmtree(attachments_dir)
    except Exception:
        pass


def _ensure_act_can_be_deleted(act: Act) -> None:
    collector = Collector(using=router.db_for_write(Act))
    collector.collect([act])


def _detach_act_from_document_batches(act: Act) -> int:
    try:
        from documents_app.models import (
            DocumentBatchAct,
            DocumentBatchProject,
            DocumentBatchProjectReviewStatus,
            GeneratedDocument,
        )
    except Exception:
        return 0

    batch_items = list(
        DocumentBatchAct.objects
        .filter(act=act)
        .values("id", "batch_id", "project_id")
    )
    if not batch_items:
        return 0

    affected_batch_ids = {item["batch_id"] for item in batch_items}
    affected_pairs = {(item["batch_id"], item["project_id"]) for item in batch_items}

    DocumentBatchAct.objects.filter(id__in=[item["id"] for item in batch_items]).delete()

    for batch_id, project_id in affected_pairs:
        remaining_items = list(
            DocumentBatchAct.objects
            .filter(batch_id=batch_id, project_id=project_id)
            .order_by("order", "id")
        )
        changed_items = []
        for order, item in enumerate(remaining_items, start=1):
            if item.order != order:
                item.order = order
                changed_items.append(item)
        if changed_items:
            DocumentBatchAct.objects.bulk_update(changed_items, ["order"])

    GeneratedDocument.objects.filter(batch_id__in=affected_batch_ids).update(is_actual=False)

    for batch_id, project_id in affected_pairs:
        DocumentBatchProject.objects.filter(batch_id=batch_id, project_id=project_id).update(
            review_status=DocumentBatchProjectReviewStatus.PENDING,
            review_started_at=None,
            review_started_by=None,
            reviewed_at=None,
            reviewed_by=None,
        )

    return len(affected_batch_ids)


def _month_range_filter_q(d_from, d_to):
    """
    Фильтр по отчетному месяцу акта: act_year/act_month.
    Эти поля считаются от даты окончания работ.
    """
    y1, m1 = int(d_from.year), int(d_from.month)
    y2, m2 = int(d_to.year), int(d_to.month)

    if (y1, m1) > (y2, m2):
        y1, m1, y2, m2 = y2, m2, y1, m1

    left = Q(act_year__gt=y1) | (Q(act_year=y1) & Q(act_month__gte=m1))
    right = Q(act_year__lt=y2) | (Q(act_year=y2) & Q(act_month__lte=m2))

    return left & right


def _apply_month_period_filter(qs, d_from, d_to):
    if d_from and d_to:
        return qs.filter(_month_range_filter_q(d_from, d_to))
    if d_from:
        return qs.filter(
            Q(act_year__gt=d_from.year) |
            (Q(act_year=d_from.year) & Q(act_month__gte=d_from.month))
        )
    if d_to:
        return qs.filter(
            Q(act_year__lt=d_to.year) |
            (Q(act_year=d_to.year) & Q(act_month__lte=d_to.month))
        )
    return qs


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

AOOK_SOURCE_KEYWORDS = ("армирование", "бетонирование", "металлоизоляция")


def _aook_candidate_acts_qs(project_id: int):
    keyword_q = Q()
    for keyword in AOOK_SOURCE_KEYWORDS:
        keyword_q |= Q(work_name__icontains=keyword)
    return (
        Act.objects.filter(projects__id=project_id)
        .filter(keyword_q)
        .distinct()
        .prefetch_related("projects")
        .order_by("work_start_date", "work_end_date", "act_date", "number", "id")
    )


def _aook_period_from_acts(acts: list[Act]):
    starts = [act.work_start_date for act in acts if act.work_start_date]
    ends = [act.work_end_date for act in acts if act.work_end_date]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _parse_aook_source_ids(request: HttpRequest) -> list[int]:
    return _parse_int_list(request.POST.getlist("source_acts"))


@dataclass(frozen=True)
class AppendixSearchRow:
    source: str
    source_label: str
    document_title: str
    material_name: str
    document_no: str
    document_date: object
    document_date_label: str
    act: Act
    file_url: str
    object_url: str

    @property
    def primary_url(self) -> str:
        return self.file_url or self.object_url


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
    from approvals_app.models import Approval  # noqa: WPS433

    ids = _parse_int_list_from_post(request, "approvals")
    approvals_map = {
        a.id: a
        for a in Approval.objects.filter(id__in=ids).only("id", "description")
    }

    items = []
    for aid in ids:
        approval = approvals_map.get(aid)
        fallback = (getattr(approval, "description", "") or "").strip()

        items.append(
            {
                "id": int(aid),
                "label": (request.POST.get(f"approval_label_{aid}", "") or "").strip(),
                "fallback": fallback,
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


def _first_existing_file_url(obj) -> str | None:
    """
    (obj) любой объект модели.
    Пытаемся найти файловое поле по частым именам, и вернуть .url если файл есть.
    """
    if obj is None:
        return None

    candidates = ("file", "pdf", "scan", "document", "doc", "attachment")
    for name in candidates:
        if hasattr(obj, name):
            f = getattr(obj, name, None)
            if not f:
                continue
            try:
                if getattr(f, "url", None) and getattr(f, "name", ""):
                    return f.url
            except Exception:
                continue
    return None


def _docx_to_pdf_cached(docx_path: Path) -> Path:
    """
    (docx_path) путь к docx.
    Возвращает путь к pdf рядом с docx.
    Если pdf нет или он старее docx — пересобирает через LibreOffice.
    """
    if not docx_path.exists():
        raise FileNotFoundError(f"DOCX not found: {docx_path}")

    out_dir = docx_path.parent
    pdf_path = out_dir / (docx_path.stem + ".pdf")

    if pdf_path.exists():
        try:
            if pdf_path.stat().st_mtime >= docx_path.stat().st_mtime:
                return pdf_path
        except Exception:
            pass

    cmd = [
        "libreoffice",
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(docx_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError("LibreOffice не найден. Установи пакет libreoffice на сервере.")
    except subprocess.CalledProcessError as e:
        err = ""
        try:
            err = (e.stderr or b"").decode("utf-8", errors="ignore")
        except Exception:
            err = str(e)
        raise RuntimeError(f"Не удалось конвертировать DOCX→PDF: {err[:500]}")

    if not pdf_path.exists():
        raise RuntimeError("Конвертация завершилась без ошибки, но PDF не появился.")

    return pdf_path


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
            "construction",
            "description",
            "file",
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
            desc_full = (a.description or "").strip()
            desc_short = desc_full or "—"
            construction = (a.construction or "").strip() or "—"

            data.append(
                {
                    "id": a.id,
                    "project": proj,
                    "construction": construction,
                    "status": status,
                    "created_at": created,
                    "description": desc_short,
                    "description_full": desc_full,
                    "label": _approval_label_short(a),
                    "preview_url": reverse("acts_app:approval_open", kwargs={"pk": a.id}) if a.file else "",
                }
            )

        return JsonResponse(
            {"draw": draw, "recordsTotal": records_total, "recordsFiltered": records_filtered, "data": data}
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

    def _parse_any_date(self, raw: str):
        """
        raw (str) — значение из GET date_from/date_to
        Поддерживает:
        - YYYY-MM-DD (из input[type=date])
        - DD.MM.YYYY (если пользователь руками)
        """
        d = _parse_iso_date(raw)
        if d:
            return d
        return _parse_search_date(raw)

    def get_queryset(self):
        # 1) базовый queryset + сортировка по created_at (самые свежие)
        qs = (
            Act.objects
            .prefetch_related("projects")
            .order_by("-created_at", "-id")
        )

        # 2) фильтр по проекту (ПЕРВЫМ, как ты просил)
        project_id = (self.request.GET.get("project") or "").strip()
        if project_id.isdigit():
            qs = qs.filter(projects__id=int(project_id)).distinct()

        # 3) фильтр по периоду (по act_year/act_month, НЕ по act_date)
        date_from_raw = (self.request.GET.get("date_from") or "").strip()
        date_to_raw = (self.request.GET.get("date_to") or "").strip()

        d_from = self._parse_any_date(date_from_raw) if date_from_raw else None
        d_to = self._parse_any_date(date_to_raw) if date_to_raw else None

        qs = _apply_month_period_filter(qs, d_from, d_to)

        # 4) статус (как было)
        status = (self.request.GET.get("status") or "").strip()
        if status in {ActStatus.DRAFT, ActStatus.FINAL}:
            qs = qs.filter(status=status)

        # 5) общий поиск по всем колонкам (ПОСЛЕДНИМ)
        q = (self.request.GET.get("q") or "").strip()
        if q:
            filters = Q()

            # № акта
            filters |= Q(number__icontains=q)

            # работы
            filters |= Q(work_name__icontains=q)

            # если ввели дату — ищем совпадение по act_date (полезно как “точный поиск”)
            parsed_date = _parse_search_date(q)
            if parsed_date:
                filters |= Q(act_date=parsed_date)

            # если ввели год (2026)
            if q.isdigit() and len(q) == 4:
                filters |= Q(act_year=int(q))

            # проекты (как у тебя уже делалось)
            if Project is not None:
                project_fields = {f.name for f in Project._meta.get_fields() if getattr(f, "concrete", False)}

                proj_q = Q()
                for fname in ("full_code", "code", "cipher", "number", "name", "title", "short_name"):
                    if fname in project_fields:
                        proj_q |= Q(**{f"projects__{fname}__icontains": q})

                if proj_q:
                    filters |= proj_q

            qs = qs.filter(filters).distinct()

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # чтобы select2 проекта сохранял выбранное значение
        project_id = (self.request.GET.get("project") or "").strip()
        selected = []
        if project_id.isdigit() and Project is not None:
            p = Project.objects.filter(id=int(project_id)).first()
            if p:
                selected = [{"id": p.id, "label": _project_label(p)}]

        ctx["selected_projects"] = selected
        return ctx


def _project_choices_for_aook():
    if Project is None:
        return []
    return Project.objects.all().order_by("full_code", "id")


def _save_aook_source_items(*, aook: Aook, source_ids: list[int]) -> None:
    AookSourceAct.objects.filter(aook=aook).delete()
    acts_map = {
        act.id: act
        for act in Act.objects.filter(id__in=source_ids)
    }
    items = []
    for position, act_id in enumerate(source_ids, start=1):
        if act_id not in acts_map:
            continue
        items.append(AookSourceAct(aook=aook, act_id=act_id, position=position))
    if items:
        AookSourceAct.objects.bulk_create(items)


class AookCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.add_act"
    template_name = "acts_app/aook_form.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        project_id = request.GET.get("project")
        selected_project = None
        source_acts = []
        initial = {}

        if project_id and str(project_id).isdigit() and Project is not None:
            selected_project = Project.objects.filter(id=int(project_id)).first()
            source_acts = list(_aook_candidate_acts_qs(int(project_id)))
            start, end = _aook_period_from_acts(source_acts)
            initial = {"work_start_date": start, "work_end_date": end}

        return render(
            request,
            self.template_name,
            {
                "mode": "create",
                "project_choices": _project_choices_for_aook(),
                "selected_project": selected_project,
                "source_acts": source_acts,
                "form": AookForm(initial=initial),
                "protocol_formset": AookProtocolFormSet(prefix="protocols"),
            },
        )

    @transaction.atomic
    def post(self, request: HttpRequest) -> HttpResponse:
        project_id = request.POST.get("project")
        selected_project = None
        source_ids = _parse_aook_source_ids(request)
        source_acts = list(Act.objects.filter(id__in=source_ids).order_by("work_start_date", "work_end_date", "act_date", "number", "id"))
        if project_id and str(project_id).isdigit() and Project is not None:
            selected_project = Project.objects.filter(id=int(project_id)).first()

        form = AookForm(request.POST)
        aook = Aook(project=selected_project, created_by=request.user) if selected_project else Aook(created_by=request.user)
        protocol_formset = AookProtocolFormSet(request.POST, instance=aook, prefix="protocols")

        if not selected_project:
            messages.error(request, "Выбери проект для АООК.")
        elif not source_ids:
            messages.error(request, "Выбери хотя бы один исходный АОСР.")
        elif form.is_valid() and protocol_formset.is_valid():
            aook = form.save(commit=False)
            aook.project = selected_project
            aook.created_by = request.user
            start, end = _aook_period_from_acts(source_acts)
            aook.work_start_date = start or aook.work_start_date
            aook.work_end_date = end or aook.work_end_date
            aook.aosr_registry_number = f"П-3.{aook.number}"
            aook.protocols_registry_number = f"П-6.{aook.number}"
            aook.save()

            _save_aook_source_items(aook=aook, source_ids=source_ids)
            protocol_formset.instance = aook
            protocol_formset.save()

            try:
                generate_and_save_aook_files(aook)
            except Exception as exc:
                messages.warning(request, f"АООК сохранён, но файлы не сгенерированы: {exc}")
            else:
                messages.success(request, f"АООК №{aook.number} создан.")
            return redirect("acts_app:aook_detail", uuid=str(aook.uuid))

        if selected_project and not source_acts:
            source_acts = list(_aook_candidate_acts_qs(selected_project.id))

        return render(
            request,
            self.template_name,
            {
                "mode": "create",
                "project_choices": _project_choices_for_aook(),
                "selected_project": selected_project,
                "source_acts": source_acts,
                "selected_source_ids": set(source_ids),
                "form": form,
                "protocol_formset": protocol_formset,
            },
        )


class AookUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"
    template_name = "acts_app/aook_form.html"

    def get_object(self, uuid: str) -> Aook:
        return get_object_or_404(Aook.objects.select_related("project"), uuid=uuid)

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        aook = self.get_object(uuid)
        source_acts = list(_aook_candidate_acts_qs(aook.project_id))
        selected_source_ids = set(aook.source_act_items.values_list("act_id", flat=True))
        return render(
            request,
            self.template_name,
            {
                "mode": "update",
                "aook": aook,
                "project_choices": _project_choices_for_aook(),
                "selected_project": aook.project,
                "source_acts": source_acts,
                "selected_source_ids": selected_source_ids,
                "form": AookForm(instance=aook),
                "protocol_formset": AookProtocolFormSet(instance=aook, prefix="protocols"),
            },
        )

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        aook = self.get_object(uuid)
        source_ids = _parse_aook_source_ids(request)
        source_acts = list(Act.objects.filter(id__in=source_ids).order_by("work_start_date", "work_end_date", "act_date", "number", "id"))
        form = AookForm(request.POST, instance=aook)
        protocol_formset = AookProtocolFormSet(request.POST, instance=aook, prefix="protocols")

        if not source_ids:
            messages.error(request, "Выбери хотя бы один исходный АОСР.")
        elif form.is_valid() and protocol_formset.is_valid():
            aook = form.save(commit=False)
            start, end = _aook_period_from_acts(source_acts)
            aook.work_start_date = start or aook.work_start_date
            aook.work_end_date = end or aook.work_end_date
            aook.aosr_registry_number = f"П-3.{aook.number}"
            aook.protocols_registry_number = f"П-6.{aook.number}"
            aook.save()
            _save_aook_source_items(aook=aook, source_ids=source_ids)
            protocol_formset.save()

            try:
                generate_and_save_aook_files(aook)
            except Exception as exc:
                messages.warning(request, f"АООК сохранён, но файлы не сгенерированы: {exc}")
            else:
                messages.success(request, f"АООК №{aook.number} обновлён.")
            return redirect("acts_app:aook_detail", uuid=str(aook.uuid))

        source_acts = list(_aook_candidate_acts_qs(aook.project_id))
        return render(
            request,
            self.template_name,
            {
                "mode": "update",
                "aook": aook,
                "project_choices": _project_choices_for_aook(),
                "selected_project": aook.project,
                "source_acts": source_acts,
                "selected_source_ids": set(source_ids),
                "form": form,
                "protocol_formset": protocol_formset,
            },
        )


class AookDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "acts_app.view_act"
    model = Aook
    template_name = "acts_app/aook_detail.html"
    context_object_name = "aook"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    def get_queryset(self):
        return (
            Aook.objects.select_related("project")
            .prefetch_related("source_act_items__act", "protocol_items")
        )


class AookRebuildFilesView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.change_act"

    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        aook = get_object_or_404(Aook, uuid=uuid)
        try:
            generate_and_save_aook_files(aook)
        except Exception as exc:
            messages.error(request, f"Не удалось пересобрать файлы АООК: {exc}")
        else:
            messages.success(request, "Файлы АООК пересобраны.")
        return redirect("acts_app:aook_detail", uuid=str(aook.uuid))


class AookXlsxDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        aook = get_object_or_404(Aook, uuid=uuid)
        if not aook.xlsx_file:
            generate_and_save_aook_files(aook)
        return FileResponse(aook.xlsx_file.open("rb"), as_attachment=True, filename=Path(aook.xlsx_file.name).name)


class AookPdfPreviewView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        aook = get_object_or_404(Aook, uuid=uuid)
        if not aook.pdf_file:
            generate_and_save_aook_files(aook)
        return FileResponse(aook.pdf_file.open("rb"), content_type="application/pdf")


class AookZipDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        aook = get_object_or_404(Aook.objects.prefetch_related("source_act_items__act"), uuid=uuid)
        if not aook.xlsx_file or not aook.pdf_file:
            generate_and_save_aook_files(aook)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            if aook.xlsx_file:
                archive.write(aook.xlsx_file.path, f"АООК/{Path(aook.xlsx_file.name).name}")
            if aook.pdf_file:
                archive.write(aook.pdf_file.path, f"АООК/{Path(aook.pdf_file.name).name}")

            acts = [item.act for item in aook.source_act_items.all()]
            acts_zip = build_acts_bulk_export_zip(acts)
            with zipfile.ZipFile(io.BytesIO(acts_zip.content), mode="r") as source_archive:
                for name in source_archive.namelist():
                    archive.writestr(f"Исходные АОСР/{name}", source_archive.read(name))

        buffer.seek(0)
        filename = f"aook_{aook.number}_{aook.act_date:%Y-%m-%d}.zip"
        return HttpResponse(
            buffer.getvalue(),
            content_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


class ActDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.delete_act"

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)
        act_number = act.number
        affected_batches_count = _detach_act_from_document_batches(act)

        try:
            _ensure_act_can_be_deleted(act)
        except ProtectedError:
            transaction.set_rollback(True)
            messages.error(
                request,
                "Акт нельзя удалить, потому что он уже используется в других разделах, например в комплекте сдачи ИД.",
            )
            return redirect("acts_app:act_list")

        _delete_act_files(act)
        act.delete()

        message = f"Акт №{act_number} удалён."
        if affected_batches_count:
            message += f" Убран из комплектов: {affected_batches_count}. Документы комплектов помечены неактуальными."
        messages.success(request, message)

        next_url = (request.POST.get("next") or "").strip()
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("acts_app:act_list")


class ActDuplicateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = ("acts_app.view_act", "acts_app.add_act")

    @transaction.atomic
    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        source = get_object_or_404(
            Act.objects.prefetch_related(
                "projects",
                "materials__passport",
                "attachments",
                "approval_items__approval",
                "parties__organization",
            ),
            uuid=uuid,
        )

        duplicate = Act.objects.create(
            created_by=request.user,
            number=source.number,
            act_date=source.act_date,
            work_name=source.work_name,
            work_start_date=source.work_start_date,
            work_end_date=source.work_end_date,
            work_norms_text=source.work_norms_text,
            allow_next_works_text=source.allow_next_works_text,
            extra_info_text=source.extra_info_text,
            copies_count=source.copies_count,
            status=ActStatus.DRAFT,
        )
        duplicate.projects.set(source.projects.all())

        material_items = [
            ActMaterialItem(
                act=duplicate,
                position=item.position,
                passport=item.passport,
                manual_name=item.manual_name,
                manual_doc_name=item.manual_doc_name,
                manual_doc_no=item.manual_doc_no,
                manual_doc_date=item.manual_doc_date,
                manual_doc_date_text=item.manual_doc_date_text,
                concrete_volume_m3=item.concrete_volume_m3,
                sheets_count=item.sheets_count,
            )
            for item in source.materials.all()
        ]
        if material_items:
            ActMaterialItem.objects.bulk_create(material_items)

        excluded_attachment_types = [
            AttachmentType.MATERIALS_REGISTRY,
            AttachmentType.DOCS_REGISTRY,
            AttachmentType.APPROVALS_REGISTRY,
        ]
        attachment_items = [
            ActAttachment(
                act=duplicate,
                type=item.type,
                title=item.title,
                doc_no=item.doc_no,
                doc_date=item.doc_date,
                doc_date_to=item.doc_date_to,
                sheets_count=item.sheets_count,
                original_state=item.original_state,
            )
            for item in source.attachments.all()
            if item.type not in excluded_attachment_types
        ]
        if attachment_items:
            ActAttachment.objects.bulk_create(attachment_items)

        approval_items = [
            ActApprovalItem(
                act=duplicate,
                approval=item.approval,
                position=item.position,
                label_override=item.label_override,
                sheets_count=item.sheets_count,
            )
            for item in source.approval_items.all()
        ]
        if approval_items:
            ActApprovalItem.objects.bulk_create(approval_items)

        party_items = [
            ActParty(
                act=duplicate,
                role=item.role,
                organization=item.organization,
                is_enabled=item.is_enabled,
                position=item.position,
                chosen_authorization=None,
            )
            for item in source.parties.all()
        ]
        if party_items:
            ActParty.objects.bulk_create(party_items)

        messages.success(
            request,
            "\u0410\u043a\u0442 \u2116{} \u0441\u0434\u0443\u0431\u043b\u0438\u0440\u043e\u0432\u0430\u043d. "
            "\u041f\u0440\u043e\u0432\u0435\u0440\u044c \u0436\u0451\u043b\u0442\u044b\u0435 \u043f\u043e\u043b\u044f \u0438 "
            "\u0441\u043e\u0445\u0440\u0430\u043d\u0438 \u043d\u043e\u0432\u044b\u0439 \u0430\u043a\u0442.".format(source.number),
        )
        url = reverse("acts_app:act_update", kwargs={"uuid": str(duplicate.uuid)})
        return redirect(f"{url}?duplicated=1")


class ActDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "acts_app.view_act"
    model = Act
    template_name = "acts_app/act_detail.html"
    context_object_name = "act"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    REGISTRY_THRESHOLD = 5  # как в AppendixBuilder

    def get_queryset(self):
        return (
            Act.objects
            .prefetch_related(
                "projects",
                "materials",
                "materials__passport",
                "materials__passport__material",
                "appendix_lines",
                "attachments",
                "parties",
                "signatory_snapshots",
                "approval_items",
                "approval_items__approval",
            )
        )

    # -------------------------
    # helpers (UI only)
    # -------------------------

    def _looks_like_grouped_material_line(self, label: str) -> bool:
        """
        AppendixBuilder при <5 материалов делает VIRTUAL-строку:
        '... №123 от 01.01.2026, №456 от 02.02.2026, арматура 22 A500C'
        Нам надо такую строку спрятать в UI, чтобы вместо неё показать паспорта поштучно.
        """
        s = (label or "").strip().lower()
        if not s:
            return False
        return ("№" in s) and (" от " in s) and ("," in s)

    def _looks_like_virtual_approval_line(self, label: str) -> bool:
        """
        AppendixBuilder при <5 approvals делает VIRTUAL-строки без source_attachment.
        """
        s = (label or "").strip().lower()
        if not s:
            return False
        return ("№" not in s) and (" от " not in s)

    def _build_material_rows_flat(self, materials: list) -> list[dict]:
        """
        Для UI: каждый паспорт отдельной строкой, с url на passport_open.
        """
        out: list[dict] = []
        for mi in materials:
            data = resolve_material_fields(mi)

            parts = [data["document_name"]]
            if data["document_no"]:
                parts.append(f"№{data['document_no']}")
            if data["document_date_str"] and data["document_date_str"] != "—":
                parts.append(f"от {data['document_date_str']}")

            base = " ".join(parts).strip()
            mat = (data["material_name"] or "").strip()
            label = f"{base}, {mat}".strip().strip(",")

            url = None
            if mi.passport_id and getattr(mi, "passport", None) is not None:
                url = reverse("acts_app:passport_open", kwargs={"pk": mi.passport_id})

            out.append(
                {"label": label or "—", "sheets": int(mi.sheets_count or 0), "url": url, "children": []}
            )
        return out

    def _build_approval_rows_flat(self, approval_items: list) -> list[dict]:
        """
        Для UI: каждое согласование отдельной строкой, с url на approval_open.
        """
        out: list[dict] = []
        for it in approval_items:
            approval = getattr(it, "approval", None)

            label = (it.label_override or "").strip()
            if not label and approval is not None:
                label = (getattr(approval, "description", "") or "").strip()

            url = reverse("acts_app:approval_open", kwargs={"pk": approval.id}) if approval is not None else None
            out.append(
                {"label": label or "—", "sheets": int(it.sheets_count or 0), "url": url, "children": []}
            )
        return out

    # -------------------------
    # main
    # -------------------------

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        act: Act = ctx["act"]

        materials = list(act.materials.all().order_by("position", "id"))
        approval_items = list(act.approval_items.all().order_by("position", "id"))
        attachments = list(act.attachments.all())
        appendix_lines = list(act.appendix_lines.all())
        snapshots = list(act.signatory_snapshots.all())

        materials_count = len(materials)
        approvals_count = len(approval_items)

        # флаги наличия строк-реестров в витрине приложений
        has_materials_registry_line = any(
            getattr(getattr(l, "source_attachment", None), "type", None) == AttachmentType.MATERIALS_REGISTRY
            for l in appendix_lines
        )
        approvals_reg_type = getattr(AttachmentType, "APPROVALS_REGISTRY", None)
        has_approvals_registry_line = any(
            approvals_reg_type
            and getattr(getattr(l, "source_attachment", None), "type", None) == approvals_reg_type
            for l in appendix_lines
        )

        # кнопка "Реестр" (П-3)
        ctx["has_p3_registry"] = any(a.type == AttachmentType.MATERIALS_REGISTRY for a in attachments)

        appendix_rows: list[dict] = []

        # будем вставлять материалы “на место”, а approvals — всегда в конец
        insert_materials_at = None

        for line in appendix_lines:
            label = (line.label or "").strip()
            sheets = int(line.sheets_count or 0)

            src = getattr(line, "source_attachment", None)
            src_type = getattr(src, "type", None) if src is not None else None

            # --- Реестр материалов: разворачиваем ---
            if src_type == AttachmentType.MATERIALS_REGISTRY:
                children = []
                for mi in materials:
                    data = resolve_material_fields(mi)

                    parts = [data["document_name"]]
                    if data["document_no"]:
                        parts.append(f"№{data['document_no']}")
                    if data["document_date_str"] and data["document_date_str"] != "—":
                        parts.append(f"от {data['document_date_str']}")

                    base = " ".join(parts).strip()
                    mat = (data["material_name"] or "").strip()
                    child_label = f"{base}, {mat}".strip().strip(",")

                    url = None
                    if mi.passport_id and getattr(mi, "passport", None) is not None:
                        url = reverse("acts_app:passport_open", kwargs={"pk": mi.passport_id})

                    children.append(
                        {"label": child_label or "—", "sheets": int(mi.sheets_count or 0), "url": url}
                    )

                appendix_rows.append({"label": label or "—", "sheets": sheets, "children": children})
                continue

            # --- Реестр документов ---
            if hasattr(AttachmentType, "DOCS_REGISTRY") and src_type == AttachmentType.DOCS_REGISTRY:
                exclude_types = {
                    AttachmentType.EXEC_SCHEME,
                    AttachmentType.MATERIALS_REGISTRY,
                    AttachmentType.DOCS_REGISTRY,
                }
                if approvals_reg_type:
                    exclude_types.add(approvals_reg_type)

                children = []
                for a in sorted(attachments, key=lambda x: (x.created_at or 0)):
                    if a.type in exclude_types:
                        continue

                    title = (a.title or "").strip() or "—"
                    doc_no = (a.doc_no or "").strip()

                    parts = [title]
                    if doc_no:
                        parts.append(f"№{doc_no}")

                    date_str = fmt_date_range_g(a.doc_date, getattr(a, "doc_date_to", None))
                    if date_str:
                        parts.append(f"от {date_str}")

                    url = reverse("acts_app:act_attachment_open", kwargs={"pk": a.id}) if getattr(a, "file",
                                                                                                  None) else None
                    children.append(
                        {"label": " ".join(parts), "sheets": int(a.sheets_count or 0), "url": url}
                    )

                appendix_rows.append({"label": label or "—", "sheets": sheets, "children": children})
                continue

            # --- Реестр согласований: разворачиваем ---
            if approvals_reg_type and src_type == approvals_reg_type:
                children = []
                for it in approval_items:
                    approval = getattr(it, "approval", None)

                    label2 = (it.label_override or "").strip()
                    if not label2 and approval is not None:
                        label2 = (getattr(approval, "description", "") or "").strip()

                    url = reverse("acts_app:approval_open",
                                  kwargs={"pk": approval.id}) if approval is not None else None
                    children.append(
                        {"label": label2 or "—", "sheets": int(it.sheets_count or 0), "url": url}
                    )

                appendix_rows.append({"label": label or "—", "sheets": sheets, "children": children})
                continue

            # --- НЕТ реестра материалов и материалов < 5: скрываем склейку материалов ---
            if (not has_materials_registry_line) and (0 < materials_count < self.REGISTRY_THRESHOLD) and (src is None):
                if self._looks_like_grouped_material_line(label):
                    if insert_materials_at is None:
                        insert_materials_at = len(appendix_rows)
                    continue

            # --- НЕТ реестра согласований и approvals < 5: скрываем VIRTUAL строки approvals ---
            if (not has_approvals_registry_line) and approvals_reg_type and (
                    0 < approvals_count < self.REGISTRY_THRESHOLD) and (src is None):
                if self._looks_like_virtual_approval_line(label):
                    continue

            appendix_rows.append({"label": label or "—", "sheets": sheets, "children": []})

        # 1) вставляем плоские материалы “на место”
        if (not has_materials_registry_line) and (0 < materials_count < self.REGISTRY_THRESHOLD):
            flat_m = self._build_material_rows_flat(materials)
            if insert_materials_at is None:
                insert_materials_at = len(appendix_rows)
            appendix_rows[insert_materials_at:insert_materials_at] = flat_m

        # 2) вставляем плоские согласования ВСЕГДА В КОНЕЦ
        if approvals_reg_type and (not has_approvals_registry_line) and (0 < approvals_count < self.REGISTRY_THRESHOLD):
            flat_a = self._build_approval_rows_flat(approval_items)
            appendix_rows.extend(flat_a)

        ctx["appendix_rows"] = [{"pos": i + 1, **r} for i, r in enumerate(appendix_rows)]

        # ---- Signatories ----
        if act.status == ActStatus.FINAL and snapshots:
            ctx["signatories_source"] = "snapshot"
            ctx["signatories"] = sorted(snapshots, key=lambda x: (x.position or 0, x.id))
        else:
            ctx["signatories_source"] = "resolved"
            ctx["resolved_parties"] = resolve_act_parties(act)

        ctx["pdf_preview_url"] = reverse("acts_app:act_pdf_preview", kwargs={"uuid": str(act.uuid)})
        ctx["registry_pdf_preview_url"] = reverse(
            "acts_app:act_registry_p3_pdf_preview",
            kwargs={"uuid": str(act.uuid)},
        )
        return ctx


class ProtocolListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "acts_app.view_act"
    model = ActAttachment
    template_name = "acts_app/protocol_list.html"
    context_object_name = "rows"
    paginate_by = 50

    SORT_MAP = {
        "doc_no": "doc_no",
        "doc_date": "doc_date",
        "act_no": "act__number",
    }

    def get_queryset(self):
        qs = (
            ActAttachment.objects
            .exclude(original_state=ActAttachment.OriginalState.IGNORE)  # всё кроме "Не отслеживать"
            .select_related("act")
            .prefetch_related("act__projects")
        )

        # --- фильтр по шифру проекта (project_id) ---
        project_id = (self.request.GET.get("project") or "").strip()
        if project_id.isdigit():
            qs = qs.filter(act__projects__id=int(project_id)).distinct()

        # --- поиск ---
        q = (self.request.GET.get("q") or "").strip()
        if q:
            cond = (
                    Q(doc_no__icontains=q) |
                    Q(title__icontains=q) |
                    Q(act__number__icontains=q)
            )

            # поиск по шифрам проектов (full_code/code/cipher/number — что есть)
            if Project is not None:
                project_fields = {f.name for f in Project._meta.get_fields() if getattr(f, "concrete", False)}
                proj_q = Q()
                for fname in ("full_code", "code", "cipher", "number", "name", "title", "short_name"):
                    if fname in project_fields:
                        proj_q |= Q(**{f"act__projects__{fname}__icontains": q})
                if proj_q:
                    cond |= proj_q

            # если ввели дату — ищем по doc_date
            parsed = _parse_search_date(q) or _parse_iso_date(q)
            if parsed:
                cond |= Q(doc_date=parsed)

            qs = qs.filter(cond).distinct()

        # --- сортировка ---
        sort = (self.request.GET.get("sort") or "doc_no").strip()
        direction = (self.request.GET.get("dir") or "asc").strip()

        order_field = self.SORT_MAP.get(sort, "doc_no")
        if direction == "desc":
            order_field = "-" + order_field

        # стабильность
        return qs.order_by(order_field, "id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # список проектов для фильтра (если Project подключен)
        ctx["projects_for_filter"] = []
        if Project is not None:
            ctx["projects_for_filter"] = list(Project.objects.order_by("full_code").only("id", "full_code"))

        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["project_selected"] = (self.request.GET.get("project") or "").strip()
        ctx["sort"] = (self.request.GET.get("sort") or "doc_no").strip()
        ctx["dir"] = (self.request.GET.get("dir") or "asc").strip()
        return ctx


def _attachment_type_label(value: str) -> str:
    try:
        return AttachmentType(value).label
    except Exception:
        return value or "Документ акта"


def _appendix_search_project_filter(qs, project_id: str, relation_prefix: str):
    if project_id.isdigit():
        return qs.filter(**{f"{relation_prefix}projects__id": int(project_id)}).distinct()
    return qs


class AppendixSearchView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "acts_app.view_act"
    template_name = "acts_app/appendix_search.html"
    context_object_name = "rows"
    paginate_by = 50

    SOURCE_ALL = ""
    SOURCE_MATERIALS = "materials"
    SOURCE_ATTACHMENTS = "attachments"
    SOURCE_PROTOCOLS = "protocols"

    def get_queryset(self):
        q = (self.request.GET.get("q") or "").strip()
        project_id = (self.request.GET.get("project") or "").strip()
        source = (self.request.GET.get("source") or "").strip()

        if not q and not project_id and source == self.SOURCE_ALL:
            return []

        rows: list[AppendixSearchRow] = []

        if source in {self.SOURCE_ALL, self.SOURCE_MATERIALS}:
            material_qs = (
                ActMaterialItem.objects
                .select_related("act", "passport", "passport__material")
                .prefetch_related("act__projects")
                .order_by("-act__act_date", "-act__id", "position", "id")
            )
            material_qs = _appendix_search_project_filter(material_qs, project_id, "act__")

            if q:
                cond = (
                    Q(manual_name__icontains=q)
                    | Q(manual_doc_name__icontains=q)
                    | Q(manual_doc_no__icontains=q)
                    | Q(manual_doc_date_text__icontains=q)
                    | Q(passport__document_name__icontains=q)
                    | Q(passport__document_number__icontains=q)
                    | Q(passport__original_name__icontains=q)
                    | Q(passport__material__name__icontains=q)
                    | Q(act__number__icontains=q)
                    | Q(act__work_name__icontains=q)
                )
                if Project is not None:
                    project_fields = {f.name for f in Project._meta.get_fields() if getattr(f, "concrete", False)}
                    for fname in ("full_code", "code", "cipher", "number", "name", "title", "short_name"):
                        if fname in project_fields:
                            cond |= Q(**{f"act__projects__{fname}__icontains": q})
                parsed = _parse_search_date(q) or _parse_iso_date(q)
                if parsed:
                    cond |= Q(manual_doc_date=parsed) | Q(passport__document_date=parsed)
                material_qs = material_qs.filter(cond).distinct()

            for item in material_qs[:500]:
                passport = item.passport if item.passport_id else None
                material_name = (
                    (getattr(getattr(passport, "material", None), "name", "") or "").strip()
                    or (item.manual_name or "").strip()
                )
                document_title = (
                    (getattr(passport, "document_name", "") or "").strip()
                    or (item.manual_doc_name or "").strip()
                    or "Материал"
                )
                document_no = (
                    (getattr(passport, "document_number", "") or "").strip()
                    or (item.manual_doc_no or "").strip()
                )
                document_date = getattr(passport, "document_date", None) or item.manual_doc_date
                document_date_label = ""
                if document_date:
                    try:
                        document_date_label = document_date.strftime("%d.%m.%Y")
                    except Exception:
                        document_date_label = str(document_date)
                elif item.manual_doc_date_text:
                    document_date_label = item.manual_doc_date_text
                object_url = (
                    reverse("acts_app:passport_open", kwargs={"pk": passport.pk})
                    if passport and getattr(passport, "file", None)
                    else ""
                )
                rows.append(
                    AppendixSearchRow(
                        source="materials",
                        source_label="Материал",
                        document_title=document_title,
                        material_name=material_name,
                        document_no=document_no,
                        document_date=document_date,
                        document_date_label=document_date_label,
                        act=item.act,
                        file_url="",
                        object_url=object_url,
                    )
                )

        if source in {self.SOURCE_ALL, self.SOURCE_ATTACHMENTS, self.SOURCE_PROTOCOLS}:
            attachment_qs = (
                ActAttachment.objects
                .select_related("act")
                .prefetch_related("act__projects")
                .order_by("-act__act_date", "-act__id", "created_at", "id")
            )
            attachment_qs = _appendix_search_project_filter(attachment_qs, project_id, "act__")

            if source == self.SOURCE_PROTOCOLS:
                attachment_qs = attachment_qs.filter(type=AttachmentType.TEST_PROTOCOL)

            if q:
                cond = (
                    Q(title__icontains=q)
                    | Q(doc_no__icontains=q)
                    | Q(file__icontains=q)
                    | Q(act__number__icontains=q)
                    | Q(act__work_name__icontains=q)
                )
                if Project is not None:
                    project_fields = {f.name for f in Project._meta.get_fields() if getattr(f, "concrete", False)}
                    for fname in ("full_code", "code", "cipher", "number", "name", "title", "short_name"):
                        if fname in project_fields:
                            cond |= Q(**{f"act__projects__{fname}__icontains": q})
                parsed = _parse_search_date(q) or _parse_iso_date(q)
                if parsed:
                    cond |= Q(doc_date=parsed) | Q(doc_date_to=parsed)
                attachment_qs = attachment_qs.filter(cond).distinct()

            for item in attachment_qs[:500]:
                file_url = (
                    reverse("acts_app:act_attachment_open", kwargs={"pk": item.pk})
                    if getattr(item, "file", None)
                    else ""
                )
                rows.append(
                    AppendixSearchRow(
                        source="attachments",
                        source_label=_attachment_type_label(item.type),
                        document_title=(item.title or "").strip() or _attachment_type_label(item.type),
                        material_name="",
                        document_no=(item.doc_no or "").strip(),
                        document_date=item.doc_date,
                        document_date_label=item.doc_date.strftime("%d.%m.%Y") if item.doc_date else "",
                        act=item.act,
                        file_url=file_url,
                        object_url="",
                    )
                )

        return sorted(
            rows,
            key=lambda row: (
                row.act.act_date or datetime.min.date(),
                row.act.id or 0,
                row.source_label,
                row.document_title,
            ),
            reverse=True,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["project_selected"] = (self.request.GET.get("project") or "").strip()
        ctx["source_selected"] = (self.request.GET.get("source") or "").strip()
        ctx["has_filters"] = bool(ctx["q"] or ctx["project_selected"] or ctx["source_selected"])
        ctx["source_options"] = [
            (self.SOURCE_ALL, "Все приложения"),
            (self.SOURCE_MATERIALS, "Материалы"),
            (self.SOURCE_ATTACHMENTS, "Документы акта"),
            (self.SOURCE_PROTOCOLS, "Протоколы"),
        ]
        ctx["projects_for_filter"] = []
        if Project is not None:
            ctx["projects_for_filter"] = list(Project.objects.order_by("full_code").only("id", "full_code"))
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
            "sheets_count",
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
        qs = qs.order_by("-document_date", "-id")[start:start + length]

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
                    "doc_date_iso": p.document_date.isoformat() if p.document_date else "",
                    "sheets_count": int(p.sheets_count or 1),
                    "label": label,
                    "preview_url": reverse("acts_app:passport_open", kwargs={"pk": p.id}),
                }
            )

        return JsonResponse(
            {"draw": draw, "recordsTotal": records_total, "recordsFiltered": records_filtered, "data": data}
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
            generate_act_docx(act)
            reg = act.attachments.filter(type=AttachmentType.MATERIALS_REGISTRY).order_by("-created_at", "-id").first()
            if reg:
                generate_and_save_registry_p3_docx(act=act, registry=reg)
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
                "label": (
                        (item.label_override or "").strip()
                        or (getattr(item.approval, "description", "") or "").strip()
                ),
                "fallback": (getattr(item.approval, "description", "") or "").strip(),
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
                "duplicate_highlight": request.GET.get("duplicated") == "1",
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
                    "duplicate_highlight": request.GET.get("duplicated") == "1",
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
            generate_act_docx(act)
            reg = act.attachments.filter(type=AttachmentType.MATERIALS_REGISTRY).order_by("-created_at", "-id").first()
            if reg:
                generate_and_save_registry_p3_docx(act=act, registry=reg)
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
# ... (всё дальше без изменений, я оставил как у тебя)
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


class ActBulkExportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"
    template_name = "acts_app/act_bulk_export.html"

    def _base_queryset(self, request: HttpRequest):
        date_from = _parse_iso_date(request.GET.get("date_from", ""))
        date_to = _parse_iso_date(request.GET.get("date_to", ""))
        if date_from is not None and date_to is not None and date_from > date_to:
            date_from, date_to = date_to, date_from

        creator_id = (request.GET.get("created_by") or "").strip()
        project_id = (request.GET.get("project") or "").strip()
        q = (request.GET.get("q") or "").strip()

        qs = Act.objects.prefetch_related("projects", "attachments").order_by("act_year", "act_month", "number", "id")
        qs = _apply_month_period_filter(qs, date_from, date_to)
        if creator_id.isdigit():
            qs = qs.filter(created_by_id=int(creator_id))
        if project_id.isdigit():
            qs = qs.filter(projects__id=int(project_id))
        if q:
            qs = qs.filter(Q(number__icontains=q) | Q(work_name__icontains=q))

        return qs.distinct(), date_from, date_to, creator_id, project_id, q

    def get(self, request: HttpRequest) -> HttpResponse:
        qs, date_from, date_to, creator_id, project_id, q = self._base_queryset(request)

        if request.GET.get("download") == "1":
            if not qs.exists():
                messages.error(request, "За выбранный период и фильтры акты не найдены. Архив не сформирован.")
                query = request.GET.copy()
                query.pop("download", None)
                url = reverse("acts_app:act_bulk_export")
                if query:
                    url = f"{url}?{query.urlencode()}"
                return redirect(url)

            result = build_acts_bulk_export_zip(qs)
            response = HttpResponse(result.content, content_type="application/zip")
            response["Content-Disposition"] = f'attachment; filename="{_build_bulk_export_filename(date_from, date_to)}"'
            download_token = (request.GET.get("download_token") or "").strip()
            if download_token:
                response.set_cookie("bulk_export_download_done", download_token, max_age=60, path="/", samesite="Lax")
            return response

        User = get_user_model()
        creators = (
            User.objects.filter(created_acts__isnull=False)
            .distinct()
            .order_by("last_name", "first_name", "email")
        )

        projects = []
        if Project is not None:
            projects = (
                Project.objects.filter(acts__isnull=False)
                .distinct()
                .order_by("full_code", "id")
            )

        return render(
            request,
            self.template_name,
            {
                "date_from": date_from,
                "date_to": date_to,
                "selected_creator_id": int(creator_id) if creator_id.isdigit() else None,
                "selected_project_id": int(project_id) if project_id.isdigit() else None,
                "q": q,
                "acts_count": qs.count(),
                "creators": creators,
                "projects": projects,
            },
        )


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


@method_decorator(xframe_options_sameorigin, name="dispatch")
class ActPdfPreviewView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    PDF preview (inline) для страницы деталей.
    Генерит DOCX при необходимости, затем конвертирует в PDF (кеш).
    """
    permission_required = "acts_app.view_act"

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)

        # 1) гарантируем docx
        paths = get_act_docx_paths(act)
        docx_path = None
        for p in paths:
            if p.exists():
                docx_path = p
                break

        if docx_path is None:
            try:
                paths = generate_act_docx(act)
                docx_path = paths[0] if paths else None
            except DocxRenderError as e:
                return HttpResponse(f"DOCX ERROR: {e}", status=500, content_type="text/plain; charset=utf-8")

        if docx_path is None or not docx_path.exists():
            return HttpResponse("DOCX ERROR: файл не был создан.", status=500, content_type="text/plain; charset=utf-8")

        # 2) docx -> pdf (кеш)
        try:
            pdf_path = _docx_to_pdf_cached(Path(docx_path))
        except Exception as e:
            return HttpResponse(f"PDF ERROR: {e}", status=500, content_type="text/plain; charset=utf-8")

        resp = FileResponse(open(pdf_path, "rb"), content_type="application/pdf")

        safe_date = act.act_date.strftime("%d.%m.%Y") if act.act_date else ""
        download_name = f"Акт №{act.number} от {safe_date}.pdf".strip()

        resp["Content-Disposition"] = f'inline; filename="{download_name}"'
        return resp


class ActRegistryP3DocxDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)

        registry = (
            act.attachments
            .filter(type=AttachmentType.MATERIALS_REGISTRY)
            .order_by("-created_at", "-id")
            .first()
        )
        if not registry:
            return HttpResponse("Реестр П-3 не создан (материалов меньше 5 или приложения не пересобраны).", status=404)

        from acts_app.services.registry_p3_docx_generator import (
            get_registry_p3_docx_paths,
            generate_and_save_registry_p3_docx,
        )

        # 1) пробуем отдать уже сохранённый файл
        paths = get_registry_p3_docx_paths(act=act, registry=registry)
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
            paths = generate_and_save_registry_p3_docx(act=act, registry=registry)
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


@method_decorator(xframe_options_sameorigin, name="dispatch")
class ActRegistryP3PdfPreviewView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"

    def get(self, request: HttpRequest, uuid: str) -> HttpResponse:
        act = get_object_or_404(Act, uuid=uuid)

        registry = (
            act.attachments
            .filter(type=AttachmentType.MATERIALS_REGISTRY)
            .order_by("-created_at", "-id")
            .first()
        )
        if not registry:
            return HttpResponse("Реестр П-3 не создан (материалов меньше 5 или приложения не пересобраны).", status=404)

        from acts_app.services.registry_p3_docx_generator import (
            get_registry_p3_docx_paths,
            generate_and_save_registry_p3_docx,
        )

        paths = get_registry_p3_docx_paths(act=act, registry=registry)
        docx_path = None
        for p in paths:
            if p.exists():
                docx_path = p
                break

        if docx_path is None:
            try:
                paths = generate_and_save_registry_p3_docx(act=act, registry=registry)
                docx_path = paths[0] if paths else None
            except DocxRenderError as e:
                return HttpResponse(f"DOCX ERROR: {e}", status=500, content_type="text/plain; charset=utf-8")

        if docx_path is None or not docx_path.exists():
            return HttpResponse("DOCX ERROR: файл не был создан.", status=500, content_type="text/plain; charset=utf-8")

        try:
            pdf_path = _docx_to_pdf_cached(Path(docx_path))
        except Exception as e:
            return HttpResponse(f"PDF ERROR: {e}", status=500, content_type="text/plain; charset=utf-8")

        resp = FileResponse(open(pdf_path, "rb"), content_type="application/pdf")

        date_value = registry.doc_date or act.work_end_date or act.act_date
        safe_date = date_value.strftime("%d.%m.%Y") if date_value else ""
        registry_no = (registry.doc_no or f"П-3.{act.number}").strip()
        download_name = f"Реестр №{registry_no} от {safe_date}.pdf".strip()

        resp["Content-Disposition"] = f'inline; filename="{download_name}"'
        return resp


class PassportOpenView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"

    def get(self, request, pk: int):
        from passports_app.models import Passport  # noqa

        p = get_object_or_404(Passport, pk=pk)

        if not p.file:
            return HttpResponse("FILE ERROR: у паспорта нет файла.", status=404,
                                content_type="text/plain; charset=utf-8")

        # storage локальный (FileSystemStorage), значит path должен быть
        try:
            path = p.file.path
        except Exception:
            return HttpResponse("FILE ERROR: не удалось получить путь к файлу.", status=500,
                                content_type="text/plain; charset=utf-8")

        resp = FileResponse(open(path, "rb"), content_type="application/pdf")
        resp["Content-Disposition"] = "inline"
        return resp


class ApprovalOpenView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"

    def get(self, request, pk: int):
        from approvals_app.models import Approval  # noqa

        a = get_object_or_404(Approval, pk=pk)

        if not a.file:
            return HttpResponse("FILE ERROR: у согласования нет файла.", status=404,
                                content_type="text/plain; charset=utf-8")

        try:
            path = a.file.path
        except Exception:
            return HttpResponse("FILE ERROR: не удалось получить путь к файлу.", status=500,
                                content_type="text/plain; charset=utf-8")

        # чаще всего это PDF
        resp = FileResponse(open(path, "rb"), content_type="application/pdf")
        resp["Content-Disposition"] = "inline"
        return resp


class ActAttachmentOpenView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "acts_app.view_act"

    def get(self, request, pk: int):
        from acts_app.models import ActAttachment  # noqa

        att = get_object_or_404(ActAttachment, pk=pk)

        if not getattr(att, "file", None):
            return HttpResponse("FILE ERROR: у документа нет файла.", status=404,
                                content_type="text/plain; charset=utf-8")

        try:
            path = att.file.path
        except Exception:
            return HttpResponse("FILE ERROR: не удалось получить путь к файлу.", status=500,
                                content_type="text/plain; charset=utf-8")

        resp = FileResponse(open(path, "rb"), content_type="application/pdf")
        resp["Content-Disposition"] = "inline"
        return resp
