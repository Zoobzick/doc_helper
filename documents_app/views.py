from __future__ import annotations

import io
import mimetypes
import re
import zipfile
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.generic import DetailView, TemplateView

from acts_app.models import Act, ActAppendixLine, AttachmentType
from acts_app.services.date_format import fmt_date_range_g
from acts_app.services.material_resolver import resolve_material_fields
from documents_app.forms import BatchAttachmentUploadForm, BoxLabelForm, DocumentBatchMasterForm
from documents_app.models import (
    BatchAttachment,
    BatchAttachmentType,
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchActReviewNote,
    DocumentBatchActSource,
    DocumentBatchGenerationMode,
    DocumentBatchProject,
    DocumentBatchProjectReviewStatus,
    DocumentBatchProjectScope,
    DocumentBatchSelectionMode,
    GeneratedDocument,
    GeneratedDocumentType,
    TitleSheet,
)
from documents_app.services.box_label_docx import render_box_label_docx
from documents_app.services.id_handover.batch_composer import (
    BatchCreateParams,
    DocumentBatchComposer,
    DocumentBatchComposerValidationError,
)
from documents_app.services.id_handover.batch_editing import (
    BatchActMoveParams,
    BatchManualActAddParams,
    DocumentBatchEditingService,
    DocumentBatchEditingValidationError,
)
from documents_app.services.id_handover.batch_generation_service import (
    BatchGenerationService,
    BatchGenerationValidationError,
)
from documents_app.services.id_handover.material_registry_projection import (
    build_act_material_registry_documents,
)
from documents_app.services.id_handover.letter_generation_service import LetterGenerationService
from documents_app.services.id_handover.project_registry_generation_service import (
    ProjectRegistryGenerationService,
)
from documents_app.services.id_handover.document_signatures import DocumentSignatureService
from documents_app.services.id_handover.preview_builder import (
    DocumentBatchPreviewBuilder,
    DocumentBatchPreviewBuilderError,
)
from documents_app.services.title_sheets import ensure_title_sheet
from projects_app.models import Project, Stage

DEFAULT_DSM = 'ГУП "Московский метрополитен"'
DEFAULT_MIP = 'АО "Мосинжпроект"'
DEFAULT_SMU = 'ООО "СМУ-12 Мосметростроя"'

MAX_LINES = 50


def _build_batch_create_params_from_form(*, form: DocumentBatchMasterForm, created_by) -> BatchCreateParams:
    """
    Собирает BatchCreateParams из формы.

    created_by (User): пользователь, от имени которого создаётся/обновляется batch
    return (BatchCreateParams): DTO для composer
    """
    return BatchCreateParams(
        created_by=created_by,
        title=(form.cleaned_data.get("title") or "").strip(),
        comment=(form.cleaned_data.get("comment") or "").strip(),
        selection_mode=form.cleaned_data["selection_mode"],
        month_from=(form.cleaned_data.get("month_from") or "").strip(),
        month_to=(form.cleaned_data.get("month_to") or "").strip(),
        generation_mode=form.cleaned_data["generation_mode"],
        letter_type=form.cleaned_data["letter_type"],
        letter_number=(form.cleaned_data.get("letter_number") or "").strip(),
        letter_date=form.cleaned_data.get("letter_date"),
        documentation_type=form.cleaned_data["documentation_type"],
        project_scope=form.cleaned_data["project_scope"],
        project_ids=form.cleaned_data.get("selected_project_ids") or [],
    )


def _assign_batch_fields_from_form(*, batch: DocumentBatch, form: DocumentBatchMasterForm) -> None:
    """
    Переносит изменённые значения из формы в существующий batch.

    batch (DocumentBatch): обновляемый комплект
    form (DocumentBatchMasterForm): валидная форма
    """
    batch.title = (form.cleaned_data.get("title") or "").strip()
    batch.comment = (form.cleaned_data.get("comment") or "").strip()
    batch.selection_mode = form.cleaned_data["selection_mode"]
    batch.month_from = (form.cleaned_data.get("month_from") or "").strip()
    batch.month_to = (form.cleaned_data.get("month_to") or "").strip()
    batch.generation_mode = form.cleaned_data["generation_mode"]
    batch.letter_type = form.cleaned_data["letter_type"]
    batch.letter_number = (form.cleaned_data.get("letter_number") or "").strip()
    batch.letter_date = form.cleaned_data.get("letter_date")
    batch.documentation_type = form.cleaned_data["documentation_type"]
    batch.project_scope = form.cleaned_data["project_scope"]


def _refresh_batch_generated_documents_actuality(*, batch: DocumentBatch) -> None:
    """
    Пересчитывает актуальность всех сгенерированных документов batch.

    batch (DocumentBatch): комплект, для которого нужно обновить is_actual
    """
    signature_service = DocumentSignatureService()
    signature_service.refresh_batch_documents_actuality(batch=batch)


def _get_batch_preview_project(*, batch: DocumentBatch, project_id: int) -> dict:
    """
    Возвращает preview одного шифра из общего preview комплекта.
    """
    builder = DocumentBatchPreviewBuilder()
    preview_data = builder.build(batch=batch)

    for project_payload in preview_data.get("projects", []):
        if project_payload.get("project_id") == project_id:
            return project_payload

    raise Http404("Проект не входит в состав данного комплекта.")


def _build_project_review_context(*, batch: DocumentBatch, project_id: int, request_user=None) -> dict:
    """
    Готовит context для отдельной страницы проверки одного шифра.
    """
    project_preview = _get_batch_preview_project(batch=batch, project_id=project_id)
    batch_project = _get_batch_project_or_404(batch=batch, project_id=project_id)
    acts_map = {
        act_payload["batch_act_id"]: act_payload
        for act_payload in project_preview.get("acts", [])
    }

    review_rows: list[dict] = []
    total_acts_count = len(project_preview.get("acts", []))
    appendix_line_ids = [
        row.get("source_appendix_line_id")
        for row in project_preview.get("registry_rows", [])
        if row.get("source_appendix_line_id")
    ]
    appendix_lines_by_id = _get_appendix_lines_by_id(appendix_line_ids=appendix_line_ids)
    current_page = 1

    for row in project_preview.get("registry_rows", []):
        row_payload = dict(row)
        act_payload = acts_map.get(row_payload.get("source_batch_act_id"))
        row_payload["review_row_class"] = _get_project_review_row_class(row_payload)
        row_payload["review_row_label"] = _get_project_review_row_label(row_payload)
        row_payload["page_start"] = current_page
        row_payload["page_end"] = current_page + max(int(row_payload.get("sheets_count") or 0), 1) - 1
        row_payload["page_display"] = _format_page_range(row_payload["page_start"], row_payload["page_end"])
        row_payload["registry_children"] = _build_registry_children_for_review_row(
            row_payload=row_payload,
            appendix_lines_by_id=appendix_lines_by_id,
        )
        current_page = row_payload["page_end"] + 1

        if act_payload:
            row_payload["action_batch_act_id"] = act_payload.get("batch_act_id")
            row_payload["action_act_uuid"] = act_payload.get("act_uuid")
            row_payload["action_act_order"] = act_payload.get("order")
            row_payload["action_can_move_up"] = bool(act_payload.get("order", 0) > 1)
            row_payload["action_can_move_down"] = bool(act_payload.get("order", 0) < total_acts_count)
        else:
            row_payload["action_batch_act_id"] = None
            row_payload["action_act_uuid"] = ""
            row_payload["action_act_order"] = None
            row_payload["action_can_move_up"] = False
            row_payload["action_can_move_down"] = False

        review_rows.append(row_payload)

    notes_by_batch_act_id = _get_review_notes_by_batch_act_id(
        batch_act_ids=[
            row_payload["action_batch_act_id"]
            for row_payload in review_rows
            if row_payload.get("action_batch_act_id")
        ]
    )
    for row_payload in review_rows:
        batch_act_id = row_payload.get("action_batch_act_id")
        row_payload["review_notes"] = notes_by_batch_act_id.get(batch_act_id, [])
        row_payload["review_notes_count"] = len(row_payload["review_notes"])

    act_groups = _build_project_review_act_groups(review_rows=review_rows)
    review_started_by = batch_project.review_started_by
    reviewed_by = batch_project.reviewed_by

    return {
        "batch": batch,
        "project_preview": project_preview,
        "project_review_rows": review_rows,
        "project_review_act_groups": act_groups,
        "project_review_back_url": f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2",
        "project_review_mark_done_url": reverse(
            "documents:id_handover_batch_project_mark_reviewed",
            kwargs={"batch_id": batch.id, "project_id": project_id},
        ),
        "project_review_is_reviewed": batch_project.is_reviewed,
        "project_review_is_in_progress": batch_project.is_in_progress,
        "project_review_status": batch_project.review_status,
        "project_review_status_label": batch_project.get_review_status_display(),
        "project_review_started_at": batch_project.review_started_at,
        "project_review_started_by": review_started_by,
        "project_review_started_by_name": _get_user_display_name(review_started_by),
        "project_review_is_started_by_current_user": (
            bool(request_user and review_started_by and review_started_by.pk == request_user.pk)
        ),
        "project_review_reviewed_at": batch_project.reviewed_at,
        "project_review_reviewed_by": reviewed_by,
        "project_review_reviewed_by_name": _get_user_display_name(reviewed_by),
    }


def _format_page_range(start: int, end: int) -> str:
    if start == end:
        return str(start)
    return f"{start}-{end}"


def _get_user_display_name(user) -> str:
    if not user:
        return ""

    display_name = ""
    if hasattr(user, "get_full_name"):
        display_name = (user.get_full_name() or "").strip()
    return display_name or getattr(user, "username", "") or str(user)


def _natural_sort_key(value: str) -> list:
    parts = re.split(r"(\d+)", value)
    return [int(part) if part.isdigit() else part.casefold() for part in parts]


def _build_generated_document_download_name(*, generated_document: GeneratedDocument, file_path: Path) -> str:
    extension = file_path.suffix.lower()
    document_type = generated_document.document_type

    if document_type in {
        GeneratedDocumentType.LETTER_DOCX,
        GeneratedDocumentType.LETTER_PREVIEW_PDF,
    }:
        return _build_letter_download_name(batch=generated_document.batch, extension=extension)

    if document_type in {
        GeneratedDocumentType.REGISTRY_XLSX,
        GeneratedDocumentType.REGISTRY_PREVIEW_PDF,
    } and generated_document.project_id:
        return _build_registry_download_name(
            batch=generated_document.batch,
            project=generated_document.project,
            extension=extension,
        )

    return generated_document.original_name or file_path.name


def _build_batch_attachment_download_name(*, attachment: BatchAttachment, file_path: Path) -> str:
    extension = file_path.suffix.lower() or ".pdf"

    if attachment.attachment_type == BatchAttachmentType.STAMPED_LETTER_PDF:
        return _build_stamped_letter_download_name(batch=attachment.batch, extension=extension)

    if attachment.attachment_type == BatchAttachmentType.MARKSURVEY_PDF:
        return _build_marksurvey_download_name(batch=attachment.batch, extension=extension)

    return attachment.original_name or file_path.name


def _build_letter_download_name(*, batch: DocumentBatch, extension: str) -> str:
    letter_number = _sanitize_download_filename_part((batch.letter_number or "").strip()) or f"batch_{batch.id}"
    letter_date = batch.letter_date.strftime("%d.%m.%Y") if batch.letter_date else "без_даты"
    batch_title = _sanitize_download_filename_part((batch.title or "").strip())
    title_part = f" ({batch_title})" if batch_title else ""
    return f"Письмо №{letter_number} от {letter_date}{title_part}{extension}"


def _build_stamped_letter_download_name(*, batch: DocumentBatch, extension: str) -> str:
    letter_number = _sanitize_download_filename_part((batch.letter_number or "").strip()) or f"batch_{batch.id}"
    letter_date = batch.letter_date.strftime("%d.%m.%Y") if batch.letter_date else "без_даты"
    batch_title = _sanitize_download_filename_part((batch.title or "").strip())
    title_part = f" ({batch_title}, с отметкой)" if batch_title else " (с отметкой)"
    return f"Письмо №{letter_number} от {letter_date}{title_part}{extension}"


def _build_marksurvey_download_name(*, batch: DocumentBatch, extension: str) -> str:
    batch_title = _sanitize_download_filename_part((batch.title or "").strip()) or f"batch_{batch.id}"
    return f"Маркзамер ({batch_title}, с отметкой){extension}"


def _build_registry_download_name(*, batch: DocumentBatch, project, extension: str) -> str:
    project_code = _sanitize_download_filename_part((getattr(project, "full_code", "") or "").strip())
    if not project_code:
        project_code = f"project_{getattr(project, 'id', 'unknown')}"

    batch_title = _sanitize_download_filename_part((batch.title or "").strip())
    title_part = f" ({batch_title})" if batch_title else ""
    return f"{_build_registry_download_label(batch=batch)} {project_code}{title_part}{extension}"


def _build_registry_download_label(*, batch: DocumentBatch) -> str:
    if batch.documentation_type == "RD":
        return "Реестр РД"
    if batch.documentation_type == "ID_RD":
        return "Реестр ИД-РД"
    return "Реестр ИД"


def _sanitize_download_filename_part(value: str) -> str:
    invalid_chars = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in invalid_chars else ch for ch in value)
    cleaned = cleaned.strip().strip(".")
    return cleaned


def _get_review_notes_by_batch_act_id(*, batch_act_ids: list[int]) -> dict[int, list[DocumentBatchActReviewNote]]:
    if not batch_act_ids:
        return {}

    notes_by_batch_act_id: dict[int, list[DocumentBatchActReviewNote]] = {}
    notes = (
        DocumentBatchActReviewNote.objects
        .filter(batch_act_id__in=batch_act_ids, is_resolved=False)
        .select_related("created_by")
        .order_by("created_at", "id")
    )
    for note in notes:
        notes_by_batch_act_id.setdefault(note.batch_act_id, []).append(note)
    return notes_by_batch_act_id


def _mark_batch_project_in_progress(*, batch: DocumentBatch, project_id: int, user) -> DocumentBatchProject:
    now = timezone.now()
    batch_project = _get_batch_project_or_404(batch=batch, project_id=project_id)

    if batch_project.review_status == DocumentBatchProjectReviewStatus.REVIEWED:
        return batch_project

    updated = (
        DocumentBatchProject.objects
        .filter(
            pk=batch_project.pk,
            review_status=DocumentBatchProjectReviewStatus.PENDING,
        )
        .update(
            review_status=DocumentBatchProjectReviewStatus.IN_PROGRESS,
            review_started_at=now,
            review_started_by=user,
            reviewed_at=None,
            reviewed_by=None,
        )
    )

    if updated:
        batch_project.review_status = DocumentBatchProjectReviewStatus.IN_PROGRESS
        batch_project.review_started_at = now
        batch_project.review_started_by = user
        batch_project.reviewed_at = None
        batch_project.reviewed_by = None
        return batch_project

    return _get_batch_project_or_404(batch=batch, project_id=project_id)


def _mark_batch_project_review_touched(*, batch: DocumentBatch, project_id: int, user) -> None:
    DocumentBatchProject.objects.filter(
        batch=batch,
        project_id=project_id,
    ).update(
        review_status=DocumentBatchProjectReviewStatus.IN_PROGRESS,
        review_started_at=timezone.now(),
        review_started_by=user,
        reviewed_at=None,
        reviewed_by=None,
    )


def _enrich_preview_with_review_state(*, batch: DocumentBatch, preview_data: dict) -> dict:
    batch_projects = (
        DocumentBatchProject.objects
        .filter(batch=batch)
        .select_related("review_started_by", "reviewed_by")
        .order_by("order", "id")
    )
    review_state_by_project_id = {
        batch_project.project_id: {
            "is_reviewed": batch_project.is_reviewed,
            "is_in_progress": batch_project.is_in_progress,
            "review_status": batch_project.review_status,
            "review_status_label": batch_project.get_review_status_display(),
            "review_started_at": batch_project.review_started_at,
            "review_started_by": batch_project.review_started_by,
            "review_started_by_name": _get_user_display_name(batch_project.review_started_by),
            "reviewed_at": batch_project.reviewed_at,
            "reviewed_by": batch_project.reviewed_by,
            "reviewed_by_name": _get_user_display_name(batch_project.reviewed_by),
        }
        for batch_project in batch_projects
    }

    reviewed_count = 0
    for project_payload in preview_data.get("projects", []):
        state = review_state_by_project_id.get(project_payload.get("project_id"), {})
        is_reviewed = bool(state.get("is_reviewed"))
        if is_reviewed:
            reviewed_count += 1

        project_payload["is_reviewed"] = is_reviewed
        project_payload["is_in_progress"] = bool(state.get("is_in_progress"))
        project_payload["review_status"] = state.get("review_status") or DocumentBatchProjectReviewStatus.PENDING
        project_payload["review_status_label"] = state.get("review_status_label") or "Ожидает проверки"
        project_payload["review_started_at"] = state.get("review_started_at")
        project_payload["review_started_by"] = state.get("review_started_by")
        project_payload["review_started_by_name"] = state.get("review_started_by_name") or ""
        project_payload["reviewed_at"] = state.get("reviewed_at")
        project_payload["reviewed_by"] = state.get("reviewed_by")
        project_payload["reviewed_by_name"] = state.get("reviewed_by_name") or ""

    preview_data.setdefault("summary", {})
    preview_data["summary"]["reviewed_projects_count"] = reviewed_count
    preview_data["summary"]["unreviewed_projects_count"] = max(
        int(preview_data["summary"].get("projects_count") or 0) - reviewed_count,
        0,
    )
    preview_data["summary"]["all_projects_reviewed"] = (
        int(preview_data["summary"].get("projects_count") or 0) > 0
        and preview_data["summary"]["unreviewed_projects_count"] == 0
    )
    return preview_data


def _get_appendix_lines_by_id(*, appendix_line_ids: list[int]) -> dict[int, ActAppendixLine]:
    if not appendix_line_ids:
        return {}

    lines = (
        ActAppendixLine.objects
        .filter(id__in=appendix_line_ids)
        .select_related("source_attachment", "act")
        .prefetch_related(
            "act__materials",
            "act__materials__passport",
            "act__materials__passport__material",
            "act__attachments",
            "act__approval_items",
            "act__approval_items__approval",
        )
    )
    return {line.id: line for line in lines}


def _build_registry_children_for_review_row(
    *,
    row_payload: dict,
    appendix_lines_by_id: dict[int, ActAppendixLine],
) -> list[dict]:
    appendix_line_id = row_payload.get("source_appendix_line_id")
    if not appendix_line_id:
        return []

    appendix_line = appendix_lines_by_id.get(appendix_line_id)
    if not appendix_line:
        return []

    registry_attachment = getattr(appendix_line, "source_attachment", None)
    registry_type = getattr(registry_attachment, "type", None)
    if registry_type not in {
        AttachmentType.MATERIALS_REGISTRY,
        AttachmentType.DOCS_REGISTRY,
        getattr(AttachmentType, "APPROVALS_REGISTRY", None),
    }:
        return []

    act = appendix_line.act
    registry_sheets = max(int(getattr(registry_attachment, "sheets_count", 1) or 1), 1)
    child_page = int(row_payload.get("page_start") or 1) + registry_sheets

    children: list[dict] = []

    if registry_type == AttachmentType.MATERIALS_REGISTRY:
        for document in build_act_material_registry_documents(act):
            children.append(
                _build_registry_child_payload(
                    label=document.label,
                    sheets=document.sheets_count,
                    page_start=child_page,
                )
            )
            child_page += document.sheets_count

    elif registry_type == AttachmentType.DOCS_REGISTRY:
        excluded_types = {
            AttachmentType.EXEC_SCHEME,
            AttachmentType.MATERIALS_REGISTRY,
            AttachmentType.DOCS_REGISTRY,
        }
        approvals_registry_type = getattr(AttachmentType, "APPROVALS_REGISTRY", None)
        if approvals_registry_type:
            excluded_types.add(approvals_registry_type)

        attachments = sorted(act.attachments.all(), key=lambda item: (item.created_at or 0, item.id))
        for attachment in attachments:
            if attachment.type in excluded_types:
                continue

            parts = [(attachment.title or "").strip() or "Документ"]
            if attachment.doc_no:
                parts.append(f"№{attachment.doc_no}")
            date_text = fmt_date_range_g(attachment.doc_date, getattr(attachment, "doc_date_to", None))
            if date_text:
                parts.append(f"от {date_text}")

            sheets = max(int(attachment.sheets_count or 0), 1)
            children.append(
                _build_registry_child_payload(label=" ".join(parts), sheets=sheets, page_start=child_page)
            )
            child_page += sheets

    else:
        approval_items = list(act.approval_items.all().order_by("position", "id"))
        for item in approval_items:
            approval = getattr(item, "approval", None)
            label = (item.label_override or "").strip()
            if not label and approval is not None:
                label = (getattr(approval, "description", "") or "").strip()

            sheets = max(int(item.sheets_count or 0), 1)
            children.append(_build_registry_child_payload(label=label or "Согласование", sheets=sheets, page_start=child_page))
            child_page += sheets

    return children


def _build_registry_child_payload(*, label: str, sheets: int, page_start: int) -> dict:
    page_end = page_start + sheets - 1
    return {
        "label": label or "—",
        "sheets": sheets,
        "page_start": page_start,
        "page_end": page_end,
        "page_display": _format_page_range(page_start, page_end),
    }


def _build_project_review_act_groups(*, review_rows: list[dict]) -> list[dict]:
    groups: list[dict] = []
    current_group: dict | None = None

    for row in review_rows:
        if row.get("is_act") or current_group is None:
            current_group = {
                "act_row": row,
                "rows": [],
                "documents_count": 0,
                "sheets_count": 0,
                "page_start": row.get("page_start"),
                "page_end": row.get("page_end"),
                "page_display": row.get("page_display"),
            }
            groups.append(current_group)

        current_group["rows"].append(row)
        current_group["documents_count"] += 1
        current_group["sheets_count"] += max(int(row.get("sheets_count") or 0), 1)
        current_group["page_end"] = row.get("page_end")
        current_group["page_display"] = _format_page_range(
            int(current_group["page_start"] or 1),
            int(current_group["page_end"] or current_group["page_start"] or 1),
        )

    return groups


def _get_project_review_row_class(row_payload: dict) -> str:
    document_name = str(row_payload.get("document_name") or "").strip().lower()

    if row_payload.get("is_act"):
        return "project-review-row-act"

    if "исполнительная схема" in document_name:
        return "project-review-row-scheme"

    if "согласован" in document_name or "согласование" in document_name:
        return "project-review-row-approval"

    if "акт освидетельствования скрытых работ" in document_name:
        return "project-review-row-act"

    if document_name.startswith("акт ") or document_name.startswith("акт№"):
        return "project-review-row-act"

    return "project-review-row-appendix"


def _get_project_review_row_label(row_payload: dict) -> str:
    row_class = row_payload.get("review_row_class")
    if row_class == "project-review-row-act":
        return "Акт"
    if row_class == "project-review-row-scheme":
        return "Схема"
    if row_class == "project-review-row-approval":
        return "Согласование"
    return "Приложение"


class BoxLabelPageView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_box_label_page"
    raise_exception = True

    def get(self, request):
        stages = Stage.objects.filter(is_active=True).order_by("code")

        return render(
            request,
            "documents_app/box_label_page.html",
            {
                "default_dsm": DEFAULT_DSM,
                "default_mip": DEFAULT_MIP,
                "default_smu": DEFAULT_SMU,
                "stages": stages,
            },
        )


class BoxLabelProjectSearchView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_box_label_page"
    raise_exception = True

    def get(self, request):
        q = (request.GET.get("q") or "").strip()

        qs = Project.objects.all().select_related("line", "plot", "designer", "section")
        if q:
            qs = qs.filter(
                Q(full_code__icontains=q)
                | Q(designer__code__icontains=q)
                | Q(line__code__icontains=q)
                | Q(plot__code__icontains=q)
                | Q(plot__full_name__icontains=q)
                | Q(section__code__icontains=q)
                | Q(construction__icontains=q)
            )

        qs = qs.order_by("-id")[:30]
        return JsonResponse(
            {
                "results": [
                    {
                        "id": p.id,
                        "full_code": p.full_code,
                        "construction": p.construction,
                    }
                    for p in qs
                ]
            }
        )


class BoxLabelGenerateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_box_label_page"
    raise_exception = True

    def post(self, request):
        form = BoxLabelForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"error": form.errors.get_json_data()}, status=400)

        dsm = form.cleaned_data.get("DSM") or DEFAULT_DSM
        mip = form.cleaned_data.get("MIP") or DEFAULT_MIP
        smu = form.cleaned_data.get("SMU") or DEFAULT_SMU

        exec_ids: list[int] = form.cleaned_data["exec_ids"]
        work_ids: list[int] = form.cleaned_data["work_ids"]
        stage_id: int | None = form.cleaned_data.get("stage_id")

        all_ids = list({*exec_ids, *work_ids})

        projects = {
            p.id: p
            for p in Project.objects.filter(id__in=all_ids).select_related("line", "plot", "stage")
        }

        first_project = projects.get(all_ids[0]) if all_ids else None
        if not first_project:
            return JsonResponse({"error": "Проекты не найдены"}, status=400)

        object_ = first_project.line.full_name if first_project.line else ""
        plot_ = first_project.plot.full_name if first_project.plot else ""

        stage_ = ""
        if stage_id:
            stage_ = get_object_or_404(Stage, pk=stage_id).full_name
        elif first_project.stage:
            stage_ = first_project.stage.full_name

        kits: list[str] = []
        for pid in exec_ids:
            p = projects.get(pid)
            if p:
                kits.append(f"Комплект исполнительной документации: {p.full_code} (папка №1, 2-шт.)")

        works: list[str] = []
        for pid in work_ids:
            p = projects.get(pid)
            if p:
                works.append(f"Комплект рабочей документации: {p.full_code} (папка №1, 2-шт.)")

        ctx = {
            "DSM": dsm,
            "MIP": mip,
            "SMU": smu,
            "object": object_,
            "plot": plot_,
            "stage": stage_,
        }

        for i in range(1, MAX_LINES + 1):
            ctx[f"kit_{i}"] = kits[i - 1] if i <= len(kits) else ""
            ctx[f"work_{i}"] = works[i - 1] if i <= len(works) else ""

        template_path = str(settings.DOCX_TEMPLATES_DIR / "lable_template.docx")
        docx_buf = render_box_label_docx(template_path, ctx)

        resp = HttpResponse(
            docx_buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        resp["Content-Disposition"] = 'attachment; filename="наклейка_коробка.docx"'
        return resp


@method_decorator(xframe_options_sameorigin, name="dispatch")
class TitleSheetOpenPdfView(PermissionRequiredMixin, View):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    def get(self, request, project_id: int, doc_type: str):
        project = get_object_or_404(Project, pk=project_id)

        allowed = {TitleSheet.DocType.ID, TitleSheet.DocType.RD, TitleSheet.DocType.ID_RD}
        if doc_type not in allowed:
            raise Http404("Неизвестный тип титульного листа")

        ts = ensure_title_sheet(project=project, doc_type=doc_type)

        pdf_path = Path(ts.pdf_path) if ts.pdf_path else None
        if not pdf_path or not pdf_path.exists():
            raise Http404("PDF титульного листа не найден")

        return FileResponse(pdf_path.open("rb"), content_type="application/pdf", as_attachment=False)


class GeneratedDocumentOpenView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_documentbatch"
    raise_exception = True

    def get(self, request, document_uuid):
        generated_document = get_object_or_404(
            GeneratedDocument.objects.select_related("batch", "project"),
            uuid=document_uuid,
        )

        if not generated_document.file:
            raise Http404("Файл сгенерированного документа отсутствует.")

        try:
            file_path = Path(generated_document.file.path)
        except Exception as exc:
            raise Http404("Не удалось получить путь к файлу.") from exc

        if not file_path.exists():
            raise Http404("Файл сгенерированного документа не найден.")

        content_type, _ = mimetypes.guess_type(file_path.name)
        return FileResponse(
            file_path.open("rb"),
            content_type=content_type or "application/octet-stream",
            as_attachment=False,
            filename=_build_generated_document_download_name(
                generated_document=generated_document,
                file_path=file_path,
            ),
        )


class DocumentBatchDownloadRegistriesView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_documentbatch"
    raise_exception = True

    def get(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])
        batch_projects = list(
            DocumentBatchProject.objects
            .filter(batch=batch)
            .select_related("project")
            .order_by("order", "id")
        )
        registry_documents = {
            document.project_id: document
            for document in GeneratedDocument.objects.filter(
                batch=batch,
                document_type=GeneratedDocumentType.REGISTRY_XLSX,
                project__isnull=False,
            ).select_related("batch", "project")
        }

        signature_service = DocumentSignatureService()
        unavailable_projects: list[str] = []
        archive_items: list[tuple[str, GeneratedDocument]] = []

        for batch_project in batch_projects:
            project_code = (batch_project.project.full_code or "").strip() or str(batch_project.project_id)
            document = registry_documents.get(batch_project.project_id)
            if not document or not document.file:
                unavailable_projects.append(project_code)
                continue

            try:
                is_actual = signature_service.check_document_actuality(
                    generated_document=document,
                ).is_actual
            except Exception:
                is_actual = False

            if not is_actual:
                unavailable_projects.append(project_code)
                continue

            archive_items.append((project_code, document))

        if unavailable_projects:
            messages.error(
                request,
                (
                    "Нельзя скачать комплект: отсутствуют или устарели XLSX-реестры для шифров: "
                    f"{', '.join(unavailable_projects)}. Сначала сформируйте документы."
                ),
            )
            return redirect("documents:id_handover_batch_detail", batch_id=batch.id)

        if not archive_items:
            messages.error(request, "В комплекте нет XLSX-реестров для скачивания.")
            return redirect("documents:id_handover_batch_detail", batch_id=batch.id)

        root_folder = self._build_root_folder_name(batch=batch)
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for project_code, document in archive_items:
                safe_project_code = (
                    _sanitize_download_filename_part(project_code)
                    or f"project_{document.project_id}"
                )
                archive_path = (
                    f"{root_folder}/{safe_project_code}/"
                    f"Реестр {safe_project_code}.xlsx"
                )
                document.file.open("rb")
                try:
                    archive.writestr(archive_path, document.file.read())
                finally:
                    document.file.close()

        archive_buffer.seek(0)
        return FileResponse(
            archive_buffer,
            content_type="application/zip",
            as_attachment=True,
            filename=f"{root_folder}.zip",
        )

    def _build_root_folder_name(self, *, batch: DocumentBatch) -> str:
        if (
            batch.selection_mode == DocumentBatchSelectionMode.RANGE
            and batch.month_from
            and batch.month_to
        ):
            if batch.month_from == batch.month_to:
                period = batch.month_from
            else:
                period = f"{batch.month_from} - {batch.month_to}"
        else:
            period = "за весь период"
        return f"Реестры ({period})"


class BatchAttachmentOpenView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_documentbatch"
    raise_exception = True

    def get(self, request, attachment_uuid):
        attachment = get_object_or_404(
            BatchAttachment.objects.select_related("batch"),
            uuid=attachment_uuid,
        )

        if not attachment.file:
            raise Http404("Файл вложения отсутствует.")

        try:
            file_path = Path(attachment.file.path)
        except Exception as exc:
            raise Http404("Не удалось получить путь к файлу.") from exc

        if not file_path.exists():
            raise Http404("Файл вложения не найден.")

        content_type, _ = mimetypes.guess_type(file_path.name)
        return FileResponse(
            file_path.open("rb"),
            content_type=content_type or "application/pdf",
            as_attachment=(request.GET.get("download") == "1"),
            filename=_build_batch_attachment_download_name(
                attachment=attachment,
                file_path=file_path,
            ),
        )


class BatchAttachmentUploadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])
        form = BatchAttachmentUploadForm(request.POST, request.FILES)

        if not form.is_valid():
            messages.error(request, form.errors.as_text())
            return redirect("documents:id_handover_batch_detail", batch_id=batch.id)

        attachment_type = form.cleaned_data["attachment_type"]
        uploaded_file = form.cleaned_data["file"]

        if attachment_type == BatchAttachmentType.STAMPED_LETTER_PDF:
            attachment, _created = BatchAttachment.objects.get_or_create(
                batch=batch,
                attachment_type=attachment_type,
                defaults={"uploaded_by": request.user},
            )

            if attachment.file:
                attachment.file.delete(save=False)

            attachment.file = uploaded_file
            attachment.original_name = _build_stamped_letter_download_name(
                batch=batch,
                extension=Path(uploaded_file.name).suffix.lower() or ".pdf",
            )
            attachment.uploaded_by = request.user
            attachment.uploaded_at = timezone.now()
            attachment.full_clean()
            attachment.save()
            messages.success(request, "Письмо с отметкой загружено.")
        else:
            attachment = BatchAttachment(
                batch=batch,
                attachment_type=attachment_type,
                file=uploaded_file,
                original_name=_build_marksurvey_download_name(
                    batch=batch,
                    extension=Path(uploaded_file.name).suffix.lower() or ".pdf",
                ),
                uploaded_by=request.user,
            )
            attachment.full_clean()
            attachment.save()
            messages.success(request, "Маркзамер загружен.")

        return redirect("documents:id_handover_batch_detail", batch_id=batch.id)


class BatchAttachmentDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        attachment = get_object_or_404(
            BatchAttachment,
            uuid=kwargs["attachment_uuid"],
            batch_id=kwargs["batch_id"],
        )
        batch_id = attachment.batch_id
        attachment_type = attachment.attachment_type

        if attachment.file:
            attachment.file.delete(save=False)
        attachment.delete()

        if attachment_type == BatchAttachmentType.STAMPED_LETTER_PDF:
            messages.success(request, "Письмо с отметкой удалено.")
        else:
            messages.success(request, "Маркзамер удалён.")

        return redirect("documents:id_handover_batch_detail", batch_id=batch_id)


class DocumentBatchMasterView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "documents_app/id_handover/batch_master.html"
    raise_exception = True

    def has_permission(self):
        batch = self.get_batch()
        user = self.request.user

        if batch is not None:
            return user.is_superuser or user.has_perm("documents_app.view_documentbatch")

        return user.is_superuser or user.has_perm("documents_app.add_documentbatch")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        batch = self.get_batch()
        form = kwargs.get("form") or self.get_form(batch=batch)

        context["page_title"] = "Комплекты"
        context["batch"] = batch
        context["form"] = form
        context["projects_count"] = Project.objects.count()
        context["preview_data"] = self._build_preview_safe(batch=batch) if batch else None
        context["generated_documents"] = self._get_generated_documents(batch=batch) if batch else []
        context["is_edit_mode"] = batch is not None
        context["requested_step"] = kwargs.get("requested_step", self._get_requested_step())

        return context

    def get_batch(self) -> DocumentBatch | None:
        batch_id = self.kwargs.get("batch_id")
        if not batch_id:
            return None
        return get_object_or_404(DocumentBatch, pk=batch_id)

    def get_form(self, *, batch: DocumentBatch | None) -> DocumentBatchMasterForm:
        if batch is None:
            return DocumentBatchMasterForm()

        initial = {
            "title": batch.title or "",
            "comment": batch.comment or "",
            "selection_mode": batch.selection_mode,
            "month_from": batch.month_from or "",
            "month_to": batch.month_to or "",
            "generation_mode": batch.generation_mode,
            "letter_type": batch.letter_type,
            "letter_number": batch.letter_number or "",
            "letter_date": batch.letter_date,
            "documentation_type": batch.documentation_type,
            "project_scope": batch.project_scope,
        }

        project_ids = list(
            batch.batch_projects.order_by("order", "id").values_list("project_id", flat=True)
        )

        if batch.project_scope == "one_project" and project_ids:
            initial["one_project"] = project_ids[0]

        if batch.project_scope == "multi_project" and project_ids:
            initial["multiple_projects"] = project_ids

        return DocumentBatchMasterForm(initial=initial)

    def _get_requested_step(self) -> int:
        raw_value = (self.request.GET.get("step") or "").strip()
        try:
            step = int(raw_value)
        except (TypeError, ValueError):
            return 2 if self.get_batch() else 1
        return step if step in (1, 2) else (2 if self.get_batch() else 1)

    def _build_preview_safe(self, *, batch: DocumentBatch) -> dict | None:
        builder = DocumentBatchPreviewBuilder()
        try:
            preview_data = builder.build(batch=batch)
            return _enrich_preview_with_review_state(batch=batch, preview_data=preview_data)
        except DocumentBatchPreviewBuilderError as exc:
            messages.warning(
                self.request,
                f"Не удалось построить preview комплекта: {exc}",
            )
            return None

    def _get_generated_documents(self, *, batch: DocumentBatch):
        signature_service = DocumentSignatureService()
        documents = list(
            GeneratedDocument.objects.select_related("project", "batch")
            .filter(batch=batch)
            .order_by("project_id", "document_type", "id")
        )

        actuality_map: dict[int, bool] = {}
        for document in documents:
            try:
                result = signature_service.check_document_actuality(
                    generated_document=document,
                )
                actuality_map[document.id] = result.is_actual
            except Exception:
                actuality_map[document.id] = document.is_actual

        for document in documents:
            document.calculated_is_actual = actuality_map.get(document.id, document.is_actual)

        return documents


class DocumentBatchListView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "documents_app.view_documentbatch"
    raise_exception = True
    template_name = "documents_app/id_handover/batch_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        query = (self.request.GET.get("q") or "").strip()

        batch_projects_qs = (
            DocumentBatchProject.objects
            .select_related("project", "review_started_by", "reviewed_by")
            .order_by("order", "id")
        )

        batches_qs = (
            DocumentBatch.objects
            .select_related("created_by")
            .prefetch_related(
                Prefetch("batch_projects", queryset=batch_projects_qs),
                "generated_documents",
                "attachments",
            )
            .order_by("-created_at", "-id")
        )

        if query:
            query_filter = (
                Q(title__icontains=query)
                | Q(comment__icontains=query)
                | Q(batch_projects__project__full_code__icontains=query)
            )
            if query.isdigit():
                query_filter |= Q(id=int(query))

            batches_qs = (
                batches_qs
                .filter(query_filter)
                .distinct()
            )

        batches = list(batches_qs)

        for batch in batches:
            self._decorate_batch_for_list(batch=batch)

        status_filter = (self.request.GET.get("status") or "all").strip()
        if status_filter != "all":
            batches = [
                batch
                for batch in batches
                if batch.list_state_key == status_filter
            ]

        context["page_title"] = "Комплекты документов"
        context["batches"] = batches
        context["search_query"] = query
        context["status_filter"] = status_filter
        context["total_count"] = len(batches)
        context["summary"] = self._build_list_summary(batches=batches)
        context["status_filters"] = [
            {"key": "all", "label": "Все"},
            {"key": "needs_review", "label": "Нужно проверить"},
            {"key": "in_progress", "label": "В работе"},
            {"key": "ready_to_generate", "label": "К генерации"},
            {"key": "generated", "label": "С файлами"},
        ]
        return context

    def _decorate_batch_for_list(self, *, batch: DocumentBatch) -> None:
        batch_projects = list(batch.batch_projects.all())
        generated_documents = list(batch.generated_documents.all())
        attachments = list(batch.attachments.all())

        project_codes = [
            batch_project.project.full_code
            for batch_project in batch_projects
            if batch_project.project_id and batch_project.project
        ]

        projects_count = len(batch_projects)
        reviewed_count = sum(1 for item in batch_projects if item.is_reviewed)
        in_progress_count = sum(1 for item in batch_projects if item.is_in_progress)
        pending_count = max(projects_count - reviewed_count - in_progress_count, 0)
        generated_count = len(generated_documents)
        actual_generated_count = sum(1 for item in generated_documents if item.is_actual)
        stale_generated_count = generated_count - actual_generated_count
        stamped_letter_count = sum(
            1
            for item in attachments
            if item.attachment_type == BatchAttachmentType.STAMPED_LETTER_PDF
        )
        marksurvey_count = sum(
            1
            for item in attachments
            if item.attachment_type == BatchAttachmentType.MARKSURVEY_PDF
        )

        in_progress_users = []
        for item in batch_projects:
            if item.is_in_progress and item.review_started_by:
                user_name = _get_user_display_name(item.review_started_by)
                if user_name and user_name not in in_progress_users:
                    in_progress_users.append(user_name)

        batch.project_codes = project_codes
        batch.project_codes_preview = project_codes[:4]
        batch.project_codes_more_count = max(len(project_codes) - len(batch.project_codes_preview), 0)
        batch.project_codes_full = ", ".join(project_codes)
        batch.project_codes_count = len(project_codes)
        batch.projects_count = projects_count
        batch.reviewed_projects_count = reviewed_count
        batch.in_progress_projects_count = in_progress_count
        batch.pending_projects_count = pending_count
        batch.review_progress_percent = round((reviewed_count / projects_count) * 100) if projects_count else 0
        batch.generated_documents_count = generated_count
        batch.actual_generated_documents_count = actual_generated_count
        batch.stale_generated_documents_count = stale_generated_count
        batch.stamped_letter_attachment_count = stamped_letter_count
        batch.has_stamped_letter_attachment = stamped_letter_count > 0
        batch.marksurvey_attachments_count = marksurvey_count
        batch.latest_generated_at = max(
            [
                document.generated_at
                for document in generated_documents
                if document.generated_at
            ],
            default=None,
        )
        batch.in_progress_user_names = in_progress_users
        batch.generation_mode_label = batch.get_generation_mode_display()
        batch.period_label = self._build_period_label(batch=batch)
        batch.list_state_key, batch.list_state_label, batch.list_state_class, batch.list_state_icon = (
            self._resolve_batch_list_state(batch=batch)
        )
        batch.primary_step = self._resolve_primary_step(batch=batch)
        batch.primary_action_label = self._resolve_primary_action_label(batch=batch)

    def _build_list_summary(self, *, batches: list[DocumentBatch]) -> dict:
        return {
            "total": len(batches),
            "needs_review": sum(1 for batch in batches if batch.list_state_key == "needs_review"),
            "in_progress": sum(1 for batch in batches if batch.list_state_key == "in_progress"),
            "ready_to_generate": sum(1 for batch in batches if batch.list_state_key == "ready_to_generate"),
            "generated": sum(1 for batch in batches if batch.list_state_key == "generated"),
        }

    def _resolve_batch_list_state(self, *, batch: DocumentBatch) -> tuple[str, str, str, str]:
        if batch.generated_documents_count:
            if batch.stale_generated_documents_count:
                return "generated", "Файлы требуют обновления", "warning", "bi-exclamation-triangle"
            return "generated", "Сформирован", "success", "bi-check2-circle"

        if batch.projects_count and batch.reviewed_projects_count == batch.projects_count:
            return "ready_to_generate", "Готов к генерации", "primary", "bi-gear"

        if batch.in_progress_projects_count:
            return "in_progress", "Проверяется", "warning", "bi-person-workspace"

        return "needs_review", "Нужно проверить", "secondary", "bi-hourglass-split"

    def _resolve_primary_step(self, *, batch: DocumentBatch) -> int:
        return 2

    def _resolve_primary_action_label(self, *, batch: DocumentBatch) -> str:
        if batch.list_state_key == "generated":
            return "Открыть файлы"
        if batch.list_state_key == "ready_to_generate":
            return "Открыть карточку"
        if batch.list_state_key == "in_progress":
            return "Продолжить проверку"
        return "Начать проверку"

    def _build_period_label(self, *, batch: DocumentBatch) -> str:
        if batch.selection_mode == DocumentBatchSelectionMode.RANGE:
            if batch.month_from and batch.month_to:
                return f"{batch.month_from} — {batch.month_to}"
            return "Период не задан"

        return "За весь период"


class DocumentBatchDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.delete_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])
        batch_label = batch.title or f"#{batch.id}"

        with transaction.atomic():
            generated_documents = list(batch.generated_documents.all())
            attachments = list(batch.attachments.all())

            for document in generated_documents:
                if document.file:
                    document.file.delete(save=False)

            for attachment in attachments:
                if attachment.file:
                    attachment.file.delete(save=False)

            batch.delete()

        messages.success(request, f"Комплект {batch_label} удалён.")
        return redirect("documents:id_handover_batch_list")


class DocumentBatchCreateDraftView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.add_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        form = DocumentBatchMasterForm(request.POST)

        if not form.is_valid():
            return self._render_invalid_form(form=form)

        params = _build_batch_create_params_from_form(
            form=form,
            created_by=request.user,
        )
        composer = DocumentBatchComposer(params=params)

        try:
            batch = composer.create_batch()
        except DocumentBatchComposerValidationError as exc:
            form.add_error(None, str(exc))
            messages.error(request, f"Комплект не создан: {exc}")
            return self._render_invalid_form(form=form)
        except Exception as exc:
            form.add_error(None, f"Внутренняя ошибка создания комплекта: {exc}")
            messages.error(request, f"Ошибка создания комплекта: {exc}")
            return self._render_invalid_form(form=form)

        messages.success(request, "Черновик комплекта успешно создан.")
        return redirect(
            f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
        )

    def _render_invalid_form(self, *, form: DocumentBatchMasterForm):
        return render(
            self.request,
            "documents_app/id_handover/batch_master.html",
            {
                "page_title": "Комплекты",
                "batch": None,
                "form": form,
                "projects_count": Project.objects.count(),
                "preview_data": None,
                "generated_documents": [],
                "is_edit_mode": False,
                "requested_step": 1,
            },
            status=400,
        )


class DocumentBatchUpdateDraftView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])
        form = DocumentBatchMasterForm(request.POST)

        if not form.is_valid():
            return self._render_invalid_form(batch=batch, form=form)

        params = _build_batch_create_params_from_form(
            form=form,
            created_by=batch.created_by,
        )
        composer = DocumentBatchComposer(params=params)

        try:
            with transaction.atomic():
                composer._validate_input_params()

                _assign_batch_fields_from_form(batch=batch, form=form)
                selected_project_ids = {
                    int(project_id)
                    for project_id in (form.cleaned_data.get("selected_project_ids") or [])
                }
                if batch.project_scope != DocumentBatchProjectScope.AUTO_BY_PERIOD:
                    batch.excluded_project_ids = [
                        project_id
                        for project_id in (batch.excluded_project_ids or [])
                        if int(project_id) not in selected_project_ids
                    ]
                batch.save(
                    update_fields=[
                        "title",
                        "comment",
                        "selection_mode",
                        "month_from",
                        "month_to",
                        "generation_mode",
                        "letter_type",
                        "letter_number",
                        "letter_date",
                        "documentation_type",
                        "project_scope",
                        "excluded_project_ids",
                        "updated_at",
                    ]
                )

                result = DocumentBatchRefreshCompositionView()._refresh_batch_composition(
                    batch=batch,
                    project_ids_override=form.cleaned_data.get("selected_project_ids") or [],
                )
                _refresh_batch_generated_documents_actuality(batch=batch)

        except DocumentBatchComposerValidationError as exc:
            form.add_error(None, str(exc))
            messages.error(request, f"Не удалось обновить параметры комплекта: {exc}")
            return self._render_invalid_form(batch=batch, form=form)
        except Exception as exc:
            form.add_error(None, f"Внутренняя ошибка обновления комплекта: {exc}")
            messages.error(request, f"Ошибка обновления комплекта: {exc}")
            return self._render_invalid_form(batch=batch, form=form)

        messages.success(
            request,
            (
                "Параметры комплекта обновлены. "
                f"Проектов — {result['projects_count']}, "
                f"auto-актов — {result['auto_acts_count']}, "
                f"manual-актов сохранено — {result['manual_acts_count']}."
            ),
        )
        return redirect(
            f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
        )

    def _render_invalid_form(self, *, batch: DocumentBatch, form: DocumentBatchMasterForm):
        master_view = DocumentBatchMasterView()
        master_view.request = self.request
        master_view.args = ()
        master_view.kwargs = {"batch_id": batch.id}

        context = master_view.get_context_data(form=form, requested_step=1)
        return render(
            self.request,
            "documents_app/id_handover/batch_master.html",
            context,
            status=400,
        )


class DocumentBatchRefreshCompositionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])

        try:
            result = self._refresh_batch_composition(batch=batch)
            _refresh_batch_generated_documents_actuality(batch=batch)
        except DocumentBatchComposerValidationError as exc:
            messages.error(request, f"Не удалось обновить состав комплекта: {exc}")
            return redirect(
                f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
            )
        except Exception as exc:
            messages.error(request, f"Ошибка обновления состава комплекта: {exc}")
            return redirect(
                f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
            )

        messages.success(
            request,
            (
                "Состав комплекта обновлён: "
                f"проектов — {result['projects_count']}, "
                f"auto-актов — {result['auto_acts_count']}, "
                f"сохранено manual-актов — {result['manual_acts_count']}."
            ),
        )
        return redirect(
            f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
        )

    @transaction.atomic
    def _refresh_batch_composition(
        self,
        *,
        batch: DocumentBatch,
        project_ids_override: list[int] | None = None,
    ) -> dict[str, int]:
        if project_ids_override is None:
            project_ids_from_batch = list(
                batch.batch_projects.order_by("order", "id").values_list("project_id", flat=True)
            )
        else:
            project_ids_from_batch = list(project_ids_override)

        if batch.project_scope == DocumentBatchProjectScope.AUTO_BY_PERIOD:
            project_ids_from_batch = []

        params = BatchCreateParams(
            created_by=batch.created_by,
            title=(batch.title or "").strip(),
            comment=(batch.comment or "").strip(),
            selection_mode=batch.selection_mode,
            month_from=(batch.month_from or "").strip(),
            month_to=(batch.month_to or "").strip(),
            generation_mode=batch.generation_mode,
            letter_type=batch.letter_type,
            letter_number=(batch.letter_number or "").strip(),
            letter_date=batch.letter_date,
            documentation_type=batch.documentation_type,
            project_scope=batch.project_scope,
            project_ids=project_ids_from_batch,
        )

        composer = DocumentBatchComposer(params=params)
        composer._validate_input_params()

        fresh_projects = composer._resolve_projects()
        excluded_project_ids = {
            int(project_id)
            for project_id in (batch.excluded_project_ids or [])
            if str(project_id).isdigit()
        }
        if excluded_project_ids:
            fresh_projects = [
                project for project in fresh_projects
                if project.id not in excluded_project_ids
            ]
        fresh_acts_by_project = composer._resolve_acts_by_project(projects=fresh_projects)

        existing_manual_acts = list(
            DocumentBatchAct.objects.filter(
                batch=batch,
                source=DocumentBatchActSource.MANUAL,
            )
            .select_related("act", "project")
            .order_by("project_id", "order", "id")
        )
        existing_batch_projects = (
            DocumentBatchProject.objects
            .filter(batch=batch)
            .select_related("review_started_by", "reviewed_by")
            .order_by("order", "id")
        )
        existing_review_state_by_project_id = {
            batch_project.project_id: {
                "review_status": batch_project.review_status,
                "review_started_at": batch_project.review_started_at,
                "review_started_by_id": batch_project.review_started_by_id,
                "reviewed_at": batch_project.reviewed_at,
                "reviewed_by_id": batch_project.reviewed_by_id,
            }
            for batch_project in existing_batch_projects
        }
        existing_act_ids_by_project_id: dict[int, list[int]] = {}
        existing_batch_acts = (
            DocumentBatchAct.objects
            .filter(batch=batch)
            .order_by("project_id", "order", "id")
        )
        for batch_act in existing_batch_acts:
            existing_act_ids_by_project_id.setdefault(batch_act.project_id, []).append(batch_act.act_id)

        fresh_project_ids = {project.id for project in fresh_projects}

        preserved_manual_acts_by_project: dict[int, list[DocumentBatchAct]] = {}
        for batch_act in existing_manual_acts:
            if batch_act.project_id not in fresh_project_ids:
                continue
            preserved_manual_acts_by_project.setdefault(batch_act.project_id, []).append(batch_act)

        DocumentBatchAct.objects.filter(batch=batch).delete()
        DocumentBatchProject.objects.filter(batch=batch).delete()

        fresh_batch_projects: list[DocumentBatchProject] = []
        for order, project in enumerate(fresh_projects, start=1):
            fresh_project_acts = fresh_acts_by_project.get(project.id, [])
            fresh_act_ids = [act.id for act in fresh_project_acts]
            auto_act_ids = set(fresh_act_ids)
            for manual_item in preserved_manual_acts_by_project.get(project.id, []):
                if manual_item.act_id not in auto_act_ids:
                    fresh_act_ids.append(manual_item.act_id)

            preserved_state = {}
            if existing_act_ids_by_project_id.get(project.id) == fresh_act_ids:
                preserved_state = existing_review_state_by_project_id.get(project.id, {})

            fresh_batch_projects.append(
                DocumentBatchProject(
                    batch=batch,
                    project=project,
                    order=order,
                    review_status=preserved_state.get(
                        "review_status",
                        DocumentBatchProjectReviewStatus.PENDING,
                    ),
                    review_started_at=preserved_state.get("review_started_at"),
                    review_started_by_id=preserved_state.get("review_started_by_id"),
                    reviewed_at=preserved_state.get("reviewed_at"),
                    reviewed_by_id=preserved_state.get("reviewed_by_id"),
                )
            )
        if fresh_batch_projects:
            DocumentBatchProject.objects.bulk_create(fresh_batch_projects)

        auto_batch_acts_to_create: list[DocumentBatchAct] = []
        auto_act_ids_by_project: dict[int, set[int]] = {}

        for project in fresh_projects:
            project_acts = fresh_acts_by_project.get(project.id, [])
            auto_act_ids_by_project[project.id] = {act.id for act in project_acts}

            for order, act in enumerate(project_acts, start=1):
                auto_batch_acts_to_create.append(
                    DocumentBatchAct(
                        batch=batch,
                        project=project,
                        act=act,
                        order=order,
                        source=DocumentBatchActSource.AUTO,
                        added_by=batch.created_by,
                    )
                )
        if auto_batch_acts_to_create:
            DocumentBatchAct.objects.bulk_create(auto_batch_acts_to_create)

        manual_batch_acts_to_create: list[DocumentBatchAct] = []
        for project in fresh_projects:
            project_id = project.id
            auto_count = len(fresh_acts_by_project.get(project_id, []))
            manual_items = preserved_manual_acts_by_project.get(project_id, [])
            auto_act_ids = auto_act_ids_by_project.get(project_id, set())

            next_order = auto_count + 1
            for manual_item in manual_items:
                if manual_item.act_id in auto_act_ids:
                    continue

                manual_batch_acts_to_create.append(
                    DocumentBatchAct(
                        batch=batch,
                        project=project,
                        act=manual_item.act,
                        order=next_order,
                        source=DocumentBatchActSource.MANUAL,
                        added_by=manual_item.added_by,
                    )
                )
                next_order += 1

        if manual_batch_acts_to_create:
            DocumentBatchAct.objects.bulk_create(manual_batch_acts_to_create)

        preview_builder = DocumentBatchPreviewBuilder()
        preview_builder.build_and_save_snapshot(batch=batch)

        return {
            "projects_count": len(fresh_projects),
            "auto_acts_count": len(auto_batch_acts_to_create),
            "manual_acts_count": len(manual_batch_acts_to_create),
        }


class DocumentBatchRefreshProjectCompositionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Обновляет состав актов только одного шифра внутри комплекта."""

    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])
        batch_project = get_object_or_404(
            DocumentBatchProject.objects.select_related("project"),
            batch=batch,
            project_id=kwargs["project_id"],
        )

        try:
            result = self._refresh_project_composition(
                batch=batch,
                batch_project=batch_project,
            )
            _refresh_batch_generated_documents_actuality(batch=batch)
        except DocumentBatchComposerValidationError as exc:
            messages.error(request, f"Не удалось обновить шифр: {exc}")
            return redirect(
                f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
            )
        except Exception as exc:
            messages.error(request, f"Ошибка обновления шифра: {exc}")
            return redirect(
                f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
            )

        messages.success(
            request,
            (
                f"Шифр {batch_project.project.full_code or batch_project.project_id} обновлён: "
                f"auto-актов — {result['auto_acts_count']}, "
                f"сохранено manual-актов — {result['manual_acts_count']}."
            ),
        )
        return redirect(
            f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
        )

    @transaction.atomic
    def _refresh_project_composition(
        self,
        *,
        batch: DocumentBatch,
        batch_project: DocumentBatchProject,
    ) -> dict[str, int | bool]:
        project_ids = list(
            batch.batch_projects.order_by("order", "id").values_list("project_id", flat=True)
        )
        if batch.project_scope == DocumentBatchProjectScope.AUTO_BY_PERIOD:
            project_ids = []

        params = BatchCreateParams(
            created_by=batch.created_by,
            title=(batch.title or "").strip(),
            comment=(batch.comment or "").strip(),
            selection_mode=batch.selection_mode,
            month_from=(batch.month_from or "").strip(),
            month_to=(batch.month_to or "").strip(),
            generation_mode=batch.generation_mode,
            letter_type=batch.letter_type,
            letter_number=(batch.letter_number or "").strip(),
            letter_date=batch.letter_date,
            documentation_type=batch.documentation_type,
            project_scope=batch.project_scope,
            project_ids=project_ids,
        )
        composer = DocumentBatchComposer(params=params)
        composer._validate_input_params()

        project = batch_project.project
        fresh_auto_acts = composer._resolve_acts_by_project(projects=[project]).get(project.id, [])
        existing_acts = list(
            DocumentBatchAct.objects.filter(batch=batch, project=project)
            .select_related("act", "added_by")
            .order_by("order", "id")
        )
        existing_act_ids = [item.act_id for item in existing_acts]
        manual_acts = [
            item for item in existing_acts if item.source == DocumentBatchActSource.MANUAL
        ]

        fresh_auto_act_ids = {act.id for act in fresh_auto_acts}
        preserved_manual_acts = [
            item for item in manual_acts if item.act_id not in fresh_auto_act_ids
        ]
        fresh_act_ids = [act.id for act in fresh_auto_acts] + [
            item.act_id for item in preserved_manual_acts
        ]
        composition_changed = existing_act_ids != fresh_act_ids

        DocumentBatchAct.objects.filter(batch=batch, project=project).delete()

        batch_acts_to_create = []
        for order, act in enumerate(fresh_auto_acts, start=1):
            batch_acts_to_create.append(
                DocumentBatchAct(
                    batch=batch,
                    project=project,
                    act=act,
                    order=order,
                    source=DocumentBatchActSource.AUTO,
                    added_by=batch.created_by,
                )
            )

        next_order = len(batch_acts_to_create) + 1
        for manual_item in preserved_manual_acts:
            batch_acts_to_create.append(
                DocumentBatchAct(
                    batch=batch,
                    project=project,
                    act=manual_item.act,
                    order=next_order,
                    source=DocumentBatchActSource.MANUAL,
                    added_by=manual_item.added_by,
                )
            )
            next_order += 1

        if batch_acts_to_create:
            DocumentBatchAct.objects.bulk_create(batch_acts_to_create)

        if composition_changed:
            batch_project.review_status = DocumentBatchProjectReviewStatus.PENDING
            batch_project.review_started_at = None
            batch_project.review_started_by = None
            batch_project.reviewed_at = None
            batch_project.reviewed_by = None
            batch_project.save(
                update_fields=[
                    "review_status",
                    "review_started_at",
                    "review_started_by",
                    "reviewed_at",
                    "reviewed_by",
                ]
            )

        DocumentBatchPreviewBuilder().build_and_save_snapshot(batch=batch)

        return {
            "auto_acts_count": len(fresh_auto_acts),
            "manual_acts_count": len(preserved_manual_acts),
            "composition_changed": composition_changed,
        }


def _parse_month_code(month_code: str) -> tuple[int, int]:
    """
    Преобразует 'MM.YYYY' -> (year, month)

    month_code (str): строка формата MM.YYYY
    return (tuple[int, int]): (year, month)
    """
    month_str, year_str = month_code.split(".")
    return int(year_str), int(month_str)


def _build_period_q(batch: DocumentBatch) -> Q:
    """
    Возвращает Q для актов, попадающих в период batch.

    ВАЖНО:
    Composer сейчас работает по act_year / act_month,
    поэтому lookup должен использовать ту же логику.
    """
    if batch.selection_mode != DocumentBatchSelectionMode.RANGE:
        return Q()

    from_year, from_month = _parse_month_code(batch.month_from)
    to_year, to_month = _parse_month_code(batch.month_to)

    return (
                   Q(act_year__gt=from_year) | Q(act_year=from_year, act_month__gte=from_month)
           ) & (
                   Q(act_year__lt=to_year) | Q(act_year=to_year, act_month__lte=to_month)
           )


def _get_batch_project_or_404(*, batch: DocumentBatch, project_id: int) -> DocumentBatchProject:
    batch_project = (
        DocumentBatchProject.objects.select_related("project")
        .filter(batch=batch, project_id=project_id)
        .first()
    )
    if not batch_project:
        raise Http404("Указанный проект не входит в состав данного комплекта.")
    return batch_project


def _build_available_project_acts_payload(*, batch: DocumentBatch, project_id: int) -> dict:
    """
    Единая логика lookup для модалки "Добавить акт".

    Возвращает только акты:
    - выбранного проекта batch
    - которых ещё нет в batch
    - и если batch == RANGE, то только ВНЕ периода
    """
    batch_project = _get_batch_project_or_404(batch=batch, project_id=project_id)

    existing_act_ids = set(
        DocumentBatchAct.objects.filter(batch=batch, project_id=project_id)
        .values_list("act_id", flat=True)
    )

    acts_qs = (
        Act.objects.filter(projects__id=project_id)
        .distinct()
        .exclude(id__in=existing_act_ids)
    )

    if batch.selection_mode == DocumentBatchSelectionMode.RANGE:
        acts_qs = acts_qs.exclude(_build_period_q(batch))

    acts_qs = acts_qs.order_by("work_end_date", "act_date", "id")

    results: list[dict] = []
    for act in acts_qs:
        results.append(
            {
                "id": act.id,
                "act_id": act.id,
                "uuid": str(act.uuid) if getattr(act, "uuid", None) else "",
                "act_number": act.number or "",
                "act_date": act.act_date.strftime("%d.%m.%Y") if act.act_date else "",
                "work_name": (act.work_name or "").strip(),
                "work_end_date": act.work_end_date.strftime("%d.%m.%Y") if act.work_end_date else "",
                "status": getattr(act, "status", "") or "",
                "status_display": act.get_status_display() if hasattr(act, "get_status_display") else "",
                "is_already_in_batch": False,
            }
        )

    return {
        "ok": True,
        "project": {
            "id": batch_project.project_id,
            "full_code": batch_project.project.full_code,
            "order": batch_project.order,
        },
        "project_id": batch_project.project_id,
        "batch_id": batch.id,
        "selection_mode": batch.selection_mode,
        "results": results,
    }


class DocumentBatchProjectActsLookupView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def get(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])

        project_id_raw = (request.GET.get("project_id") or "").strip()
        if not project_id_raw:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Не передан project_id.",
                    "results": [],
                },
                status=400,
            )

        try:
            project_id = int(project_id_raw)
        except ValueError:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Некорректный project_id.",
                    "results": [],
                },
                status=400,
            )

        try:
            payload = _build_available_project_acts_payload(
                batch=batch,
                project_id=project_id,
            )
        except Http404:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Указанный проект не входит в состав данного комплекта.",
                    "results": [],
                },
                status=404,
            )

        return JsonResponse(payload)


@login_required
@permission_required("documents_app.change_documentbatch", raise_exception=True)
def id_handover_batch_acts_lookup(request, batch_id: int):
    """
    Function-based alias lookup.

    Оставлен для совместимости с текущими urls/шаблонами.
    """
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise Http404("AJAX only")

    batch = get_object_or_404(DocumentBatch, pk=batch_id)

    project_id_raw = (request.GET.get("project_id") or "").strip()
    if not project_id_raw:
        return JsonResponse(
            {
                "ok": False,
                "error": "Не передан project_id.",
                "results": [],
            },
            status=400,
        )

    try:
        project_id = int(project_id_raw)
    except ValueError:
        return JsonResponse(
            {
                "ok": False,
                "error": "Некорректный project_id.",
                "results": [],
            },
            status=400,
        )

    try:
        payload = _build_available_project_acts_payload(
            batch=batch,
            project_id=project_id,
        )
    except Http404:
        return JsonResponse(
            {
                "ok": False,
                "error": "Проект не входит в состав данного комплекта.",
                "results": [],
            },
            status=404,
        )

    return JsonResponse(payload)


class DocumentBatchProjectReviewView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "documents_app.view_documentbatch"
    raise_exception = True
    template_name = "documents_app/id_handover/batch_project_review.html"

    def get(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=self.kwargs["batch_id"])
        project_id = self.kwargs["project_id"]
        _mark_batch_project_in_progress(batch=batch, project_id=project_id, user=request.user)

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            context = _build_project_review_context(batch=batch, project_id=project_id, request_user=request.user)
            html = render_to_string(
                "documents_app/id_handover/partials/project_review_content.html",
                context=context,
                request=request,
            )
            return HttpResponse(html)

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        batch = get_object_or_404(DocumentBatch, pk=self.kwargs["batch_id"])
        project_id = self.kwargs["project_id"]
        project_context = _build_project_review_context(batch=batch, project_id=project_id, request_user=self.request.user)

        context.update(project_context)
        context["page_title"] = f"Проверка шифра: {project_context['project_preview']['project_code']}"
        return context


class DocumentBatchProjectMarkReviewedView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])
        project_id = kwargs["project_id"]
        batch_project = _get_batch_project_or_404(batch=batch, project_id=project_id)

        batch_project.review_status = DocumentBatchProjectReviewStatus.REVIEWED
        batch_project.review_started_at = batch_project.review_started_at or timezone.now()
        batch_project.review_started_by = batch_project.review_started_by or request.user
        batch_project.reviewed_at = timezone.now()
        batch_project.reviewed_by = request.user
        batch_project.save(
            update_fields=[
                "review_status",
                "review_started_at",
                "review_started_by",
                "reviewed_at",
                "reviewed_by",
            ]
        )

        messages.success(
            request,
            f"Шифр {batch_project.project.full_code} отмечен как проверенный.",
        )
        return redirect(f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2")


class DocumentBatchActAddReviewNoteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])
        batch_act = get_object_or_404(
            DocumentBatchAct.objects.select_related("batch", "project", "act"),
            pk=kwargs["batch_act_id"],
            batch=batch,
        )
        note_text = (request.POST.get("note_text") or "").strip()
        wants_partial = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if not note_text:
            if wants_partial:
                return JsonResponse({"ok": False, "error": "Введите текст замечания."}, status=400)
            messages.error(request, "Введите текст замечания.")
            return redirect(
                "documents:id_handover_batch_project_review",
                batch_id=batch.id,
                project_id=batch_act.project_id,
            )

        DocumentBatchActReviewNote.objects.create(
            batch_act=batch_act,
            text=note_text,
            created_by=request.user,
        )
        _mark_batch_project_review_touched(
            batch=batch,
            project_id=batch_act.project_id,
            user=request.user,
        )
        messages.success(request, "Замечание добавлено к акту.")

        if wants_partial:
            context = _build_project_review_context(
                batch=batch,
                project_id=batch_act.project_id,
                request_user=request.user,
            )
            html = render_to_string(
                "documents_app/id_handover/partials/project_review_content.html",
                context=context,
                request=request,
            )
            return HttpResponse(html)

        return redirect(
            "documents:id_handover_batch_project_review",
            batch_id=batch.id,
            project_id=batch_act.project_id,
        )


class DocumentBatchStep2BaseActionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def get_batch(self, *, batch_id: int) -> DocumentBatch:
        return get_object_or_404(DocumentBatch, pk=batch_id)

    def get_batch_act(self, *, batch: DocumentBatch, batch_act_id: int) -> DocumentBatchAct:
        return get_object_or_404(
            DocumentBatchAct.objects.select_related("batch", "project", "act"),
            pk=batch_act_id,
            batch=batch,
        )

    def rebuild_preview_snapshot(self, *, batch: DocumentBatch) -> None:
        builder = DocumentBatchPreviewBuilder()
        builder.build_and_save_snapshot(batch=batch)

    def refresh_generated_documents_actuality(self, *, batch: DocumentBatch) -> None:
        _refresh_batch_generated_documents_actuality(batch=batch)

    def build_master_url(
            self,
            *,
            batch: DocumentBatch,
            step: int = 2,
            fragment: str | None = None,
    ) -> str:
        base_url = reverse("documents:id_handover_batch_master", kwargs={"batch_id": batch.id})
        url = f"{base_url}?step={step}"
        if fragment:
            url = f"{url}#{fragment}"
        return url

    def redirect_to_master(
            self,
            *,
            batch: DocumentBatch,
            step: int = 2,
            fragment: str | None = None,
    ):
        return redirect(self.build_master_url(batch=batch, step=step, fragment=fragment))

    def build_project_review_url(
            self,
            *,
            batch: DocumentBatch,
            project_id: int,
            fragment: str | None = None,
    ) -> str:
        url = reverse(
            "documents:id_handover_batch_project_review",
            kwargs={"batch_id": batch.id, "project_id": project_id},
        )
        if fragment:
            url = f"{url}#{fragment}"
        return url

    def redirect_to_project_review(
            self,
            *,
            batch: DocumentBatch,
            project_id: int,
            fragment: str | None = None,
    ):
        return redirect(self.build_project_review_url(batch=batch, project_id=project_id, fragment=fragment))

    def render_project_review_partial(
            self,
            *,
            request,
            batch: DocumentBatch,
            project_id: int,
            status: int = 200,
    ) -> HttpResponse:
        context = _build_project_review_context(batch=batch, project_id=project_id, request_user=request.user)
        html = render_to_string(
            "documents_app/id_handover/partials/project_review_content.html",
            context=context,
            request=request,
        )
        return HttpResponse(html, status=status)

    def wants_partial_response(self, *, request) -> bool:
        return request.headers.get("X-Requested-With") == "XMLHttpRequest"


class DocumentBatchAddManualActView(DocumentBatchStep2BaseActionView):
    def post(self, request, *args, **kwargs):
        batch = self.get_batch(batch_id=kwargs["batch_id"])

        project_id_raw = (request.POST.get("project_id") or "").strip()
        act_id_raw = (request.POST.get("act_id") or "").strip()
        order_raw = (request.POST.get("order") or "").strip()

        if not project_id_raw or not act_id_raw:
            messages.error(
                request,
                "Для добавления акта нужно выбрать проект и акт.",
            )
            return self.redirect_to_master(batch=batch, step=2)

        try:
            project_id = int(project_id_raw)
            act_id = int(act_id_raw)
            order = int(order_raw) if order_raw else None
        except ValueError:
            messages.error(request, "Некорректные параметры добавления акта.")
            return self.redirect_to_master(batch=batch, step=2)

        service = DocumentBatchEditingService()

        try:
            batch_act = service.add_manual_act(
                BatchManualActAddParams(
                    batch=batch,
                    project_id=project_id,
                    act_id=act_id,
                    added_by=request.user,
                    order=order,
                )
            )
            _mark_batch_project_review_touched(batch=batch, project_id=project_id, user=request.user)
            self.rebuild_preview_snapshot(batch=batch)
            self.refresh_generated_documents_actuality(batch=batch)
        except DocumentBatchEditingValidationError as exc:
            messages.error(request, f"Не удалось добавить акт: {exc}")
            return self.redirect_to_master(batch=batch, step=2, fragment=f"project-{project_id}")
        except Exception as exc:
            messages.error(request, f"Ошибка добавления акта: {exc}")
            return self.redirect_to_master(batch=batch, step=2, fragment=f"project-{project_id}")

        messages.success(
            request,
            f"Акт №{batch_act.act.number} добавлен в проект {batch_act.project.full_code}.",
        )
        return self.redirect_to_master(
            batch=batch,
            step=2,
            fragment=f"batch-act-{batch_act.id}",
        )


class DocumentBatchMoveActUpView(DocumentBatchStep2BaseActionView):
    def post(self, request, *args, **kwargs):
        batch = self.get_batch(batch_id=kwargs["batch_id"])
        batch_act = self.get_batch_act(batch=batch, batch_act_id=kwargs["batch_act_id"])
        project_id = batch_act.project_id
        fragment = f"batch-act-{batch_act.id}"
        wants_partial = self.wants_partial_response(request=request)
        return_to_project_review = (request.POST.get("return_to") or "").strip() == "project_review"

        if batch_act.order <= 1:
            messages.info(request, "Этот акт уже находится на первом месте в проекте.")
            if wants_partial:
                return self.render_project_review_partial(request=request, batch=batch, project_id=project_id)
            if return_to_project_review:
                return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fragment)
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)

        service = DocumentBatchEditingService()

        try:
            service.move_act_within_project(
                BatchActMoveParams(
                    batch=batch,
                    project_id=batch_act.project_id,
                    batch_act_id=batch_act.id,
                    new_order=batch_act.order - 1,
                )
            )
            _mark_batch_project_review_touched(batch=batch, project_id=project_id, user=request.user)
            self.rebuild_preview_snapshot(batch=batch)
            self.refresh_generated_documents_actuality(batch=batch)
        except DocumentBatchEditingValidationError as exc:
            messages.error(request, f"Не удалось переместить акт вверх: {exc}")
            if wants_partial:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            if return_to_project_review:
                return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fragment)
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)
        except Exception as exc:
            messages.error(request, f"Ошибка перемещения акта вверх: {exc}")
            if wants_partial:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            if return_to_project_review:
                return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fragment)
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)

        messages.success(request, "Акт перемещён вверх.")
        if wants_partial:
            return self.render_project_review_partial(request=request, batch=batch, project_id=project_id)
        if return_to_project_review:
            return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fragment)
        return self.redirect_to_master(batch=batch, step=2, fragment=fragment)


class DocumentBatchMoveActDownView(DocumentBatchStep2BaseActionView):
    def post(self, request, *args, **kwargs):
        batch = self.get_batch(batch_id=kwargs["batch_id"])
        batch_act = self.get_batch_act(batch=batch, batch_act_id=kwargs["batch_act_id"])
        project_id = batch_act.project_id
        fragment = f"batch-act-{batch_act.id}"
        wants_partial = self.wants_partial_response(request=request)
        return_to_project_review = (request.POST.get("return_to") or "").strip() == "project_review"

        project_items_count = DocumentBatchAct.objects.filter(
            batch=batch,
            project_id=batch_act.project_id,
        ).count()

        if batch_act.order >= project_items_count:
            messages.info(request, "Этот акт уже находится на последнем месте в проекте.")
            if wants_partial:
                return self.render_project_review_partial(request=request, batch=batch, project_id=project_id)
            if return_to_project_review:
                return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fragment)
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)

        service = DocumentBatchEditingService()

        try:
            service.move_act_within_project(
                BatchActMoveParams(
                    batch=batch,
                    project_id=batch_act.project_id,
                    batch_act_id=batch_act.id,
                    new_order=batch_act.order + 1,
                )
            )
            _mark_batch_project_review_touched(batch=batch, project_id=project_id, user=request.user)
            self.rebuild_preview_snapshot(batch=batch)
            self.refresh_generated_documents_actuality(batch=batch)
        except DocumentBatchEditingValidationError as exc:
            messages.error(request, f"Не удалось переместить акт вниз: {exc}")
            if wants_partial:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            if return_to_project_review:
                return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fragment)
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)
        except Exception as exc:
            messages.error(request, f"Ошибка перемещения акта вниз: {exc}")
            if wants_partial:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            if return_to_project_review:
                return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fragment)
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)

        messages.success(request, "Акт перемещён вниз.")
        if wants_partial:
            return self.render_project_review_partial(request=request, batch=batch, project_id=project_id)
        if return_to_project_review:
            return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fragment)
        return self.redirect_to_master(batch=batch, step=2, fragment=fragment)


class DocumentBatchRemoveActView(DocumentBatchStep2BaseActionView):
    def post(self, request, *args, **kwargs):
        batch = self.get_batch(batch_id=kwargs["batch_id"])
        batch_act = self.get_batch_act(batch=batch, batch_act_id=kwargs["batch_act_id"])
        project_id = batch_act.project_id
        fallback_fragment = f"project-{batch_act.project_id}"
        wants_partial = self.wants_partial_response(request=request)
        return_to_project_review = (request.POST.get("return_to") or "").strip() == "project_review"

        service = DocumentBatchEditingService()
        act_number = batch_act.act.number
        project_code = batch_act.project.full_code
        source = batch_act.source

        try:
            service.remove_act(
                batch=batch,
                project_id=batch_act.project_id,
                batch_act_id=batch_act.id,
            )
            _mark_batch_project_review_touched(batch=batch, project_id=project_id, user=request.user)
            self.rebuild_preview_snapshot(batch=batch)
            self.refresh_generated_documents_actuality(batch=batch)
        except DocumentBatchEditingValidationError as exc:
            messages.error(request, f"Не удалось удалить акт: {exc}")
            if wants_partial:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            if return_to_project_review:
                return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fallback_fragment)
            return self.redirect_to_master(batch=batch, step=2, fragment=fallback_fragment)
        except Exception as exc:
            messages.error(request, f"Ошибка удаления акта: {exc}")
            if wants_partial:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            if return_to_project_review:
                return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fallback_fragment)
            return self.redirect_to_master(batch=batch, step=2, fragment=fallback_fragment)

        source_label = "manual" if source == DocumentBatchActSource.MANUAL else "auto"
        messages.success(
            request,
            f"Акт №{act_number} удалён из проекта {project_code} ({source_label}).",
        )
        if wants_partial:
            return self.render_project_review_partial(request=request, batch=batch, project_id=project_id)
        if return_to_project_review:
            return self.redirect_to_project_review(batch=batch, project_id=project_id, fragment=fallback_fragment)
        return self.redirect_to_master(batch=batch, step=2, fragment=fallback_fragment)


class DocumentBatchRemoveProjectView(DocumentBatchStep2BaseActionView):
    def post(self, request, *args, **kwargs):
        batch = self.get_batch(batch_id=kwargs["batch_id"])
        project_id = kwargs["project_id"]
        service = DocumentBatchEditingService()

        try:
            result = service.remove_project(batch=batch, project_id=project_id)
            self.rebuild_preview_snapshot(batch=batch)
            self.refresh_generated_documents_actuality(batch=batch)
        except DocumentBatchEditingValidationError as exc:
            messages.error(request, f"Не удалось удалить шифр: {exc}")
            return self.redirect_to_master(batch=batch, step=2)
        except Exception as exc:
            messages.error(request, f"Ошибка удаления шифра: {exc}")
            return self.redirect_to_master(batch=batch, step=2)

        messages.success(
            request,
            (
                f"Шифр {result.project_code} удалён из комплекта. "
                f"Удалено актов: {result.removed_acts_count}; "
                f"ранее сформированных реестров: {result.removed_generated_documents_count}."
            ),
        )
        return self.redirect_to_master(batch=batch, step=2)


class DocumentBatchDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = "documents_app.view_documentbatch"
    raise_exception = True

    model = DocumentBatch
    template_name = "documents_app/id_handover/batch_detail.html"
    context_object_name = "batch"
    pk_url_kwarg = "batch_id"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        batch = self.object

        preview_data = self._build_preview_safe(batch=batch)
        generated_documents = self._get_generated_documents(batch=batch)
        attachments = self._get_batch_attachments(batch=batch)

        context["preview_data"] = preview_data
        context["generated_documents"] = generated_documents
        context["attachments"] = attachments
        context["stamped_letter_attachment"] = attachments["stamped_letter_attachment"]
        context["marksurvey_attachments"] = attachments["marksurvey_attachments"]
        context["stamped_letter_upload_form"] = BatchAttachmentUploadForm(
            initial={"attachment_type": BatchAttachmentType.STAMPED_LETTER_PDF}
        )
        context["marksurvey_upload_form"] = BatchAttachmentUploadForm(
            initial={"attachment_type": BatchAttachmentType.MARKSURVEY_PDF}
        )
        context["page_title"] = f"Комплект ИД: {batch.title}"
        return context

    def _build_preview_safe(self, *, batch: DocumentBatch) -> dict | None:
        builder = DocumentBatchPreviewBuilder()
        try:
            preview_data = builder.build(batch=batch)
            preview_data["projects"] = sorted(
                preview_data.get("projects", []),
                key=lambda project_payload: _natural_sort_key(project_payload.get("project_code") or ""),
            )
            return preview_data
        except DocumentBatchPreviewBuilderError as exc:
            messages.warning(
                self.request,
                f"Не удалось построить preview комплекта: {exc}",
            )
            return None

    def _get_generated_documents(self, *, batch: DocumentBatch):
        signature_service = DocumentSignatureService()
        documents = list(
            GeneratedDocument.objects.select_related("project", "batch")
            .filter(batch=batch)
            .order_by("project_id", "document_type", "id")
        )

        actuality_map: dict[int, bool] = {}
        for document in documents:
            try:
                result = signature_service.check_document_actuality(
                    generated_document=document,
                )
                actuality_map[document.id] = result.is_actual
            except Exception:
                actuality_map[document.id] = document.is_actual

        for document in documents:
            document.calculated_is_actual = actuality_map.get(document.id, document.is_actual)

        return documents

    def _get_batch_attachments(self, *, batch: DocumentBatch) -> dict:
        attachments = list(
            BatchAttachment.objects.select_related("uploaded_by")
            .filter(batch=batch)
            .order_by("attachment_type", "-uploaded_at", "-id")
        )

        stamped_letter_attachment = None
        marksurvey_attachments = []

        for attachment in attachments:
            if attachment.attachment_type == BatchAttachmentType.STAMPED_LETTER_PDF:
                stamped_letter_attachment = attachment
            elif attachment.attachment_type == BatchAttachmentType.MARKSURVEY_PDF:
                marksurvey_attachments.append(attachment)

        return {
            "all": attachments,
            "stamped_letter_attachment": stamped_letter_attachment,
            "marksurvey_attachments": marksurvey_attachments,
        }


class DocumentBatchGenerateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])

        generation_step = (request.POST.get("generation_step") or "").strip()
        if self._is_ajax_request(request) and generation_step:
            return self._generate_ajax_step(
                request=request,
                batch=batch,
                generation_step=generation_step,
            )

        service = BatchGenerationService()

        try:
            result = service.generate(
                batch=batch,
                registry_template_path=self._get_registry_template_path(),
                regular_letter_template_path=self._get_regular_letter_template_path(),
                archive_letter_template_path=self._get_archive_letter_template_path(),
            )
        except BatchGenerationValidationError as exc:
            error_message = f"Генерация не запущена: {exc}"
            messages.error(request, error_message)
            if self._is_ajax_request(request):
                return JsonResponse(
                    {
                        "ok": False,
                        "message": error_message,
                    },
                    status=400,
                )
            return self._redirect_after_error(batch=batch)
        except Exception as exc:
            error_message = f"Ошибка генерации комплекта: {exc}"
            messages.error(request, error_message)
            if self._is_ajax_request(request):
                return JsonResponse(
                    {
                        "ok": False,
                        "message": error_message,
                    },
                    status=500,
                )
            return self._redirect_after_error(batch=batch)

        success_message = self._build_success_message(result=result)
        messages.success(request, success_message)

        if self._is_ajax_request(request):
            return JsonResponse(
                {
                    "ok": True,
                    "redirect_url": reverse(
                        "documents:id_handover_batch_detail",
                        kwargs={"batch_id": batch.id},
                    ),
                    "message": success_message,
                }
            )

        return redirect("documents:id_handover_batch_detail", batch_id=batch.id)

    def _generate_ajax_step(self, *, request, batch: DocumentBatch, generation_step: str):
        try:
            if generation_step == "registry":
                project_id = int(request.POST.get("project_id") or 0)
                if not DocumentBatchProject.objects.filter(
                    batch=batch,
                    project_id=project_id,
                ).exists():
                    raise BatchGenerationValidationError(
                        "Проект не входит в текущий состав комплекта."
                    )

                ProjectRegistryGenerationService().generate_for_project(
                    batch=batch,
                    project_id=project_id,
                    template_path=self._get_registry_template_path(),
                )
                return JsonResponse({"ok": True, "step": "registry", "project_id": project_id})

            if generation_step == "letter":
                if batch.generation_mode not in {
                    DocumentBatchGenerationMode.LETTER_ONLY,
                    DocumentBatchGenerationMode.FULL_SET,
                }:
                    raise BatchGenerationValidationError(
                        "Для выбранного режима письмо не формируется."
                    )

                LetterGenerationService().generate_for_batch(
                    batch=batch,
                    regular_template_path=self._get_regular_letter_template_path(),
                    archive_template_path=self._get_archive_letter_template_path(),
                )
                return JsonResponse({"ok": True, "step": "letter"})

            if generation_step == "finalize":
                DocumentSignatureService().refresh_batch_documents_actuality(batch=batch)
                return JsonResponse(
                    {
                        "ok": True,
                        "step": "finalize",
                        "message": "Генерация комплекта завершена.",
                        "redirect_url": reverse(
                            "documents:id_handover_batch_detail",
                            kwargs={"batch_id": batch.id},
                        ),
                    }
                )

            raise BatchGenerationValidationError("Неизвестный этап генерации комплекта.")
        except (BatchGenerationValidationError, ValueError) as exc:
            return JsonResponse({"ok": False, "message": str(exc)}, status=400)
        except Exception as exc:
            return JsonResponse(
                {"ok": False, "message": f"Ошибка генерации комплекта: {exc}"},
                status=500,
            )

    def _redirect_after_error(self, *, batch: DocumentBatch):
        return redirect("documents:id_handover_batch_detail", batch_id=batch.id)

    def _legacy_success_message_stub(self, *, result) -> str:
        parts: list[str] = []

        if result.registries_generated_count:
            parts.append(f"реестров сформировано: {result.registries_generated_count}")
        else:
            parts.append("реестры не формировались")

        if result.letter_generated:
            parts.append("письмо сформировано")
        else:
            parts.append("письмо не формировалось")

        if getattr(result, "registries_auto_generated_for_letter", False):
            parts.append("часть реестров была автоматически догенерирована для письма")

        messages.success(
            request,
            f"Генерация завершена: {', '.join(parts)}.",
        )

    def _get_registry_template_path(self) -> Path:
        return Path(settings.XLSX_TEMPLATES_DIR) / "id_handover_registry.xlsx"

    def _is_ajax_request(self, request) -> bool:
        return request.headers.get("x-requested-with") == "XMLHttpRequest"

    def _build_success_message(self, *, result) -> str:
        parts: list[str] = []

        if result.registries_generated_count:
            parts.append(f"реестров сформировано: {result.registries_generated_count}")
        else:
            parts.append("реестры не формировались")

        if result.letter_generated:
            parts.append("письмо сформировано")
        else:
            parts.append("письмо не формировалось")

        if getattr(result, "registries_auto_generated_for_letter", False):
            parts.append("часть реестров была автоматически догенерирована для письма")

        return f"Генерация завершена: {', '.join(parts)}."

    def _get_regular_letter_template_path(self) -> Path:
        return Path(settings.DOCX_TEMPLATES_DIR) / "id_handover_letter_for_execution.docx"

    def _get_archive_letter_template_path(self) -> Path:
        return Path(settings.DOCX_TEMPLATES_DIR) / "id_handover_letter_to_archive.docx"

