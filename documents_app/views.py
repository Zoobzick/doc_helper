from __future__ import annotations

import mimetypes
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
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.generic import DetailView, TemplateView

from acts_app.models import Act
from documents_app.forms import BoxLabelForm, DocumentBatchMasterForm
from documents_app.models import (
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchActSource,
    DocumentBatchProject,
    DocumentBatchProjectScope,
    DocumentBatchSelectionMode,
    GeneratedDocument,
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
from documents_app.services.id_handover.document_signatures import DocumentSignatureService
from documents_app.services.id_handover.preview_builder import (
    DocumentBatchPreviewBuilder,
    DocumentBatchPreviewBuilderError,
)
from documents_app.services.title_sheets import ensure_title_sheet
from projects_app.models import Project, Stage

DEFAULT_DSM = 'Р“РЈРџ "РњРѕСЃРєРѕРІСЃРєРёР№ РјРµС‚СЂРѕРїРѕР»РёС‚РµРЅ"'
DEFAULT_MIP = 'РђРћ "РњРѕСЃРёРЅР¶РїСЂРѕРµРєС‚"'
DEFAULT_SMU = 'РћРћРћ "РЎРњРЈ-12 РњРѕСЃРјРµС‚СЂРѕСЃС‚СЂРѕСЏ"'

MAX_LINES = 50


def _build_batch_create_params_from_form(*, form: DocumentBatchMasterForm, created_by) -> BatchCreateParams:
    """
    РЎРѕР±РёСЂР°РµС‚ BatchCreateParams РёР· С„РѕСЂРјС‹.

    created_by (User): РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ, РѕС‚ РёРјРµРЅРё РєРѕС‚РѕСЂРѕРіРѕ СЃРѕР·РґР°С‘С‚СЃСЏ/РѕР±РЅРѕРІР»СЏРµС‚СЃСЏ batch
    return (BatchCreateParams): DTO РґР»СЏ composer
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
    РџРµСЂРµРЅРѕСЃРёС‚ РёР·РјРµРЅС‘РЅРЅС‹Рµ Р·РЅР°С‡РµРЅРёСЏ РёР· С„РѕСЂРјС‹ РІ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёР№ batch.

    batch (DocumentBatch): РѕР±РЅРѕРІР»СЏРµРјС‹Р№ РєРѕРјРїР»РµРєС‚
    form (DocumentBatchMasterForm): РІР°Р»РёРґРЅР°СЏ С„РѕСЂРјР°
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
    РџРµСЂРµСЃС‡РёС‚С‹РІР°РµС‚ Р°РєС‚СѓР°Р»СЊРЅРѕСЃС‚СЊ РІСЃРµС… СЃРіРµРЅРµСЂРёСЂРѕРІР°РЅРЅС‹С… РґРѕРєСѓРјРµРЅС‚РѕРІ batch.

    batch (DocumentBatch): РєРѕРјРїР»РµРєС‚, РґР»СЏ РєРѕС‚РѕСЂРѕРіРѕ РЅСѓР¶РЅРѕ РѕР±РЅРѕРІРёС‚СЊ is_actual
    """
    signature_service = DocumentSignatureService()
    signature_service.refresh_batch_documents_actuality(batch=batch)


def _get_batch_preview_project(*, batch: DocumentBatch, project_id: int) -> dict:
    """
    Р’РѕР·РІСЂР°С‰Р°РµС‚ preview РѕРґРЅРѕРіРѕ С€РёС„СЂР° РёР· РѕР±С‰РµРіРѕ preview РєРѕРјРїР»РµРєС‚Р°.
    """
    builder = DocumentBatchPreviewBuilder()
    preview_data = builder.build(batch=batch)

    for project_payload in preview_data.get("projects", []):
        if project_payload.get("project_id") == project_id:
            return project_payload

    raise Http404("РџСЂРѕРµРєС‚ РЅРµ РІС…РѕРґРёС‚ РІ СЃРѕСЃС‚Р°РІ РґР°РЅРЅРѕРіРѕ РєРѕРјРїР»РµРєС‚Р°.")


def _build_project_review_context(*, batch: DocumentBatch, project_id: int) -> dict:
    """
    Р“РѕС‚РѕРІРёС‚ context РґР»СЏ РѕС‚РґРµР»СЊРЅРѕР№ СЃС‚СЂР°РЅРёС†С‹ РїСЂРѕРІРµСЂРєРё РѕРґРЅРѕРіРѕ С€РёС„СЂР°.
    """
    project_preview = _get_batch_preview_project(batch=batch, project_id=project_id)
    acts_map = {
        act_payload["batch_act_id"]: act_payload
        for act_payload in project_preview.get("acts", [])
    }

    review_rows: list[dict] = []
    total_acts_count = len(project_preview.get("acts", []))

    for row in project_preview.get("registry_rows", []):
        row_payload = dict(row)
        act_payload = acts_map.get(row_payload.get("source_batch_act_id"))

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

    return {
        "batch": batch,
        "project_preview": project_preview,
        "project_review_rows": review_rows,
        "project_review_back_url": f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2",
    }


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
            return JsonResponse({"error": "РџСЂРѕРµРєС‚С‹ РЅРµ РЅР°Р№РґРµРЅС‹"}, status=400)

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
                kits.append(f"РљРѕРјРїР»РµРєС‚ РёСЃРїРѕР»РЅРёС‚РµР»СЊРЅРѕР№ РґРѕРєСѓРјРµРЅС‚Р°С†РёРё: {p.full_code} (РїР°РїРєР° в„–1, 2-С€С‚.)")

        works: list[str] = []
        for pid in work_ids:
            p = projects.get(pid)
            if p:
                works.append(f"РљРѕРјРїР»РµРєС‚ СЂР°Р±РѕС‡РµР№ РґРѕРєСѓРјРµРЅС‚Р°С†РёРё: {p.full_code} (РїР°РїРєР° в„–1, 2-С€С‚.)")

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
        resp["Content-Disposition"] = 'attachment; filename="РЅР°РєР»РµР№РєР°_РєРѕСЂРѕР±РєР°.docx"'
        return resp


@method_decorator(xframe_options_sameorigin, name="dispatch")
class TitleSheetOpenPdfView(PermissionRequiredMixin, View):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    def get(self, request, project_id: int, doc_type: str):
        project = get_object_or_404(Project, pk=project_id)

        allowed = {TitleSheet.DocType.ID, TitleSheet.DocType.RD, TitleSheet.DocType.ID_RD}
        if doc_type not in allowed:
            raise Http404("РќРµРёР·РІРµСЃС‚РЅС‹Р№ С‚РёРї С‚РёС‚СѓР»СЊРЅРѕРіРѕ Р»РёСЃС‚Р°")

        ts = ensure_title_sheet(project=project, doc_type=doc_type)

        pdf_path = Path(ts.pdf_path) if ts.pdf_path else None
        if not pdf_path or not pdf_path.exists():
            raise Http404("PDF С‚РёС‚СѓР»СЊРЅРѕРіРѕ Р»РёСЃС‚Р° РЅРµ РЅР°Р№РґРµРЅ")

        return FileResponse(pdf_path.open("rb"), content_type="application/pdf", as_attachment=False)


class GeneratedDocumentOpenView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.view_documentbatch"
    raise_exception = True

    def get(self, request, document_id: int):
        generated_document = get_object_or_404(
            GeneratedDocument.objects.select_related("batch", "project"),
            pk=document_id,
        )

        if not generated_document.file:
            raise Http404("Р¤Р°Р№Р» СЃРіРµРЅРµСЂРёСЂРѕРІР°РЅРЅРѕРіРѕ РґРѕРєСѓРјРµРЅС‚Р° РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚.")

        try:
            file_path = Path(generated_document.file.path)
        except Exception as exc:
            raise Http404("РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ РїСѓС‚СЊ Рє С„Р°Р№Р»Сѓ.") from exc

        if not file_path.exists():
            raise Http404("Р¤Р°Р№Р» СЃРіРµРЅРµСЂРёСЂРѕРІР°РЅРЅРѕРіРѕ РґРѕРєСѓРјРµРЅС‚Р° РЅРµ РЅР°Р№РґРµРЅ.")

        content_type, _ = mimetypes.guess_type(file_path.name)
        return FileResponse(
            file_path.open("rb"),
            content_type=content_type or "application/octet-stream",
            as_attachment=False,
        )


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

        context["page_title"] = "РљРѕРјРїР»РµРєС‚С‹"
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
        return step if step in (1, 2, 3) else (2 if self.get_batch() else 1)

    def _build_preview_safe(self, *, batch: DocumentBatch) -> dict | None:
        builder = DocumentBatchPreviewBuilder()
        try:
            return builder.build(batch=batch)
        except DocumentBatchPreviewBuilderError as exc:
            messages.warning(
                self.request,
                f"РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕСЃС‚СЂРѕРёС‚СЊ preview РєРѕРјРїР»РµРєС‚Р°: {exc}",
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
            .select_related("project")
            .order_by("order", "id")
        )

        batches_qs = (
            DocumentBatch.objects
            .select_related("created_by")
            .prefetch_related(
                Prefetch("batch_projects", queryset=batch_projects_qs)
            )
            .order_by("-created_at", "-id")
        )

        if query:
            batches_qs = (
                batches_qs
                .filter(batch_projects__project__full_code__icontains=query)
                .distinct()
            )

        batches = list(batches_qs)

        for batch in batches:
            project_codes = [
                batch_project.project.full_code
                for batch_project in batch.batch_projects.all()
                if batch_project.project_id and batch_project.project
            ]

            batch.project_codes = project_codes
            batch.project_codes_full = ", ".join(project_codes)
            batch.project_codes_count = len(project_codes)

        context["page_title"] = "РљРѕРјРїР»РµРєС‚С‹ РґРѕРєСѓРјРµРЅС‚РѕРІ"
        context["batches"] = batches
        context["search_query"] = query
        context["total_count"] = len(batches)
        return context


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
            messages.error(request, f"РљРѕРјРїР»РµРєС‚ РЅРµ СЃРѕР·РґР°РЅ: {exc}")
            return self._render_invalid_form(form=form)
        except Exception as exc:
            form.add_error(None, f"Р’РЅСѓС‚СЂРµРЅРЅСЏСЏ РѕС€РёР±РєР° СЃРѕР·РґР°РЅРёСЏ РєРѕРјРїР»РµРєС‚Р°: {exc}")
            messages.error(request, f"РћС€РёР±РєР° СЃРѕР·РґР°РЅРёСЏ РєРѕРјРїР»РµРєС‚Р°: {exc}")
            return self._render_invalid_form(form=form)

        messages.success(request, "Р§РµСЂРЅРѕРІРёРє РєРѕРјРїР»РµРєС‚Р° СѓСЃРїРµС€РЅРѕ СЃРѕР·РґР°РЅ.")
        return redirect(
            f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
        )

    def _render_invalid_form(self, *, form: DocumentBatchMasterForm):
        return render(
            self.request,
            "documents_app/id_handover/batch_master.html",
            {
                "page_title": "РљРѕРјРїР»РµРєС‚С‹",
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
            messages.error(request, f"РќРµ СѓРґР°Р»РѕСЃСЊ РѕР±РЅРѕРІРёС‚СЊ РїР°СЂР°РјРµС‚СЂС‹ РєРѕРјРїР»РµРєС‚Р°: {exc}")
            return self._render_invalid_form(batch=batch, form=form)
        except Exception as exc:
            form.add_error(None, f"Р’РЅСѓС‚СЂРµРЅРЅСЏСЏ РѕС€РёР±РєР° РѕР±РЅРѕРІР»РµРЅРёСЏ РєРѕРјРїР»РµРєС‚Р°: {exc}")
            messages.error(request, f"РћС€РёР±РєР° РѕР±РЅРѕРІР»РµРЅРёСЏ РєРѕРјРїР»РµРєС‚Р°: {exc}")
            return self._render_invalid_form(batch=batch, form=form)

        messages.success(
            request,
            (
                "РџР°СЂР°РјРµС‚СЂС‹ РєРѕРјРїР»РµРєС‚Р° РѕР±РЅРѕРІР»РµРЅС‹. "
                f"РџСЂРѕРµРєС‚РѕРІ вЂ” {result['projects_count']}, "
                f"auto-Р°РєС‚РѕРІ вЂ” {result['auto_acts_count']}, "
                f"manual-Р°РєС‚РѕРІ СЃРѕС…СЂР°РЅРµРЅРѕ вЂ” {result['manual_acts_count']}."
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
            messages.error(request, f"РќРµ СѓРґР°Р»РѕСЃСЊ РѕР±РЅРѕРІРёС‚СЊ СЃРѕСЃС‚Р°РІ РєРѕРјРїР»РµРєС‚Р°: {exc}")
            return redirect(
                f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
            )
        except Exception as exc:
            messages.error(request, f"РћС€РёР±РєР° РѕР±РЅРѕРІР»РµРЅРёСЏ СЃРѕСЃС‚Р°РІР° РєРѕРјРїР»РµРєС‚Р°: {exc}")
            return redirect(
                f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=2"
            )

        messages.success(
            request,
            (
                "РЎРѕСЃС‚Р°РІ РєРѕРјРїР»РµРєС‚Р° РѕР±РЅРѕРІР»С‘РЅ: "
                f"РїСЂРѕРµРєС‚РѕРІ вЂ” {result['projects_count']}, "
                f"auto-Р°РєС‚РѕРІ вЂ” {result['auto_acts_count']}, "
                f"СЃРѕС…СЂР°РЅРµРЅРѕ manual-Р°РєС‚РѕРІ вЂ” {result['manual_acts_count']}."
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
        fresh_acts_by_project = composer._resolve_acts_by_project(projects=fresh_projects)

        existing_manual_acts = list(
            DocumentBatchAct.objects.filter(
                batch=batch,
                source=DocumentBatchActSource.MANUAL,
            )
            .select_related("act", "project")
            .order_by("project_id", "order", "id")
        )

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
            fresh_batch_projects.append(
                DocumentBatchProject(
                    batch=batch,
                    project=project,
                    order=order,
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


def _parse_month_code(month_code: str) -> tuple[int, int]:
    """
    РџСЂРµРѕР±СЂР°Р·СѓРµС‚ 'MM.YYYY' -> (year, month)

    month_code (str): СЃС‚СЂРѕРєР° С„РѕСЂРјР°С‚Р° MM.YYYY
    return (tuple[int, int]): (year, month)
    """
    month_str, year_str = month_code.split(".")
    return int(year_str), int(month_str)


def _build_period_q(batch: DocumentBatch) -> Q:
    """
    Р’РѕР·РІСЂР°С‰Р°РµС‚ Q РґР»СЏ Р°РєС‚РѕРІ, РїРѕРїР°РґР°СЋС‰РёС… РІ РїРµСЂРёРѕРґ batch.

    Р’РђР–РќРћ:
    Composer СЃРµР№С‡Р°СЃ СЂР°Р±РѕС‚Р°РµС‚ РїРѕ act_year / act_month,
    РїРѕСЌС‚РѕРјСѓ lookup РґРѕР»Р¶РµРЅ РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ С‚Сѓ Р¶Рµ Р»РѕРіРёРєСѓ.
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
        raise Http404("РЈРєР°Р·Р°РЅРЅС‹Р№ РїСЂРѕРµРєС‚ РЅРµ РІС…РѕРґРёС‚ РІ СЃРѕСЃС‚Р°РІ РґР°РЅРЅРѕРіРѕ РєРѕРјРїР»РµРєС‚Р°.")
    return batch_project


def _build_available_project_acts_payload(*, batch: DocumentBatch, project_id: int) -> dict:
    """
    Р•РґРёРЅР°СЏ Р»РѕРіРёРєР° lookup РґР»СЏ РјРѕРґР°Р»РєРё "Р”РѕР±Р°РІРёС‚СЊ Р°РєС‚".

    Р’РѕР·РІСЂР°С‰Р°РµС‚ С‚РѕР»СЊРєРѕ Р°РєС‚С‹:
    - РІС‹Р±СЂР°РЅРЅРѕРіРѕ РїСЂРѕРµРєС‚Р° batch
    - РєРѕС‚РѕСЂС‹С… РµС‰С‘ РЅРµС‚ РІ batch
    - Рё РµСЃР»Рё batch == RANGE, С‚Рѕ С‚РѕР»СЊРєРѕ Р’РќР• РїРµСЂРёРѕРґР°
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
                    "error": "РќРµ РїРµСЂРµРґР°РЅ project_id.",
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
                    "error": "РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ project_id.",
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
                    "error": "РЈРєР°Р·Р°РЅРЅС‹Р№ РїСЂРѕРµРєС‚ РЅРµ РІС…РѕРґРёС‚ РІ СЃРѕСЃС‚Р°РІ РґР°РЅРЅРѕРіРѕ РєРѕРјРїР»РµРєС‚Р°.",
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

    РћСЃС‚Р°РІР»РµРЅ РґР»СЏ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё СЃ С‚РµРєСѓС‰РёРјРё urls/С€Р°Р±Р»РѕРЅР°РјРё.
    """
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise Http404("AJAX only")

    batch = get_object_or_404(DocumentBatch, pk=batch_id)

    project_id_raw = (request.GET.get("project_id") or "").strip()
    if not project_id_raw:
        return JsonResponse(
            {
                "ok": False,
                "error": "РќРµ РїРµСЂРµРґР°РЅ project_id.",
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
                "error": "РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ project_id.",
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
                "error": "РџСЂРѕРµРєС‚ РЅРµ РІС…РѕРґРёС‚ РІ СЃРѕСЃС‚Р°РІ РґР°РЅРЅРѕРіРѕ РєРѕРјРїР»РµРєС‚Р°.",
                "results": [],
            },
            status=404,
        )

    return JsonResponse(payload)


class DocumentBatchProjectReviewView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "documents_app.view_documentbatch"
    raise_exception = True
    template_name = "documents_app/id_handover/batch_project_review.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        batch = get_object_or_404(DocumentBatch, pk=self.kwargs["batch_id"])
        project_id = self.kwargs["project_id"]
        project_context = _build_project_review_context(batch=batch, project_id=project_id)

        context.update(project_context)
        context["page_title"] = f"РџСЂРѕРІРµСЂРєР° С€РёС„СЂР°: {project_context['project_preview']['project_code']}"
        return context


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
        context = _build_project_review_context(batch=batch, project_id=project_id)
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
                "Р”Р»СЏ РґРѕР±Р°РІР»РµРЅРёСЏ Р°РєС‚Р° РЅСѓР¶РЅРѕ РІС‹Р±СЂР°С‚СЊ РїСЂРѕРµРєС‚ Рё Р°РєС‚.",
            )
            return self.redirect_to_master(batch=batch, step=2)

        try:
            project_id = int(project_id_raw)
            act_id = int(act_id_raw)
            order = int(order_raw) if order_raw else None
        except ValueError:
            messages.error(request, "РќРµРєРѕСЂСЂРµРєС‚РЅС‹Рµ РїР°СЂР°РјРµС‚СЂС‹ РґРѕР±Р°РІР»РµРЅРёСЏ Р°РєС‚Р°.")
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
            self.rebuild_preview_snapshot(batch=batch)
            self.refresh_generated_documents_actuality(batch=batch)
        except DocumentBatchEditingValidationError as exc:
            messages.error(request, f"РќРµ СѓРґР°Р»РѕСЃСЊ РґРѕР±Р°РІРёС‚СЊ Р°РєС‚: {exc}")
            return self.redirect_to_master(batch=batch, step=2, fragment=f"project-{project_id}")
        except Exception as exc:
            messages.error(request, f"РћС€РёР±РєР° РґРѕР±Р°РІР»РµРЅРёСЏ Р°РєС‚Р°: {exc}")
            return self.redirect_to_master(batch=batch, step=2, fragment=f"project-{project_id}")

        messages.success(
            request,
            f"РђРєС‚ в„–{batch_act.act.number} РґРѕР±Р°РІР»РµРЅ РІ РїСЂРѕРµРєС‚ {batch_act.project.full_code}.",
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

        context["preview_data"] = preview_data
        context["generated_documents"] = generated_documents
        context["page_title"] = f"РљРѕРјРїР»РµРєС‚ РР”: {batch.title}"
        return context

    def _build_preview_safe(self, *, batch: DocumentBatch) -> dict | None:
        builder = DocumentBatchPreviewBuilder()
        try:
            return builder.build(batch=batch)
        except DocumentBatchPreviewBuilderError as exc:
            messages.warning(
                self.request,
                f"РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕСЃС‚СЂРѕРёС‚СЊ preview РєРѕРјРїР»РµРєС‚Р°: {exc}",
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


class DocumentBatchGenerateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.change_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        batch = get_object_or_404(DocumentBatch, pk=kwargs["batch_id"])

        service = BatchGenerationService()

        try:
            result = service.generate(
                batch=batch,
                registry_template_path=self._get_registry_template_path(),
                regular_letter_template_path=self._get_regular_letter_template_path(),
                archive_letter_template_path=self._get_archive_letter_template_path(),
            )
        except BatchGenerationValidationError as exc:
            messages.error(request, f"Р“РµРЅРµСЂР°С†РёСЏ РЅРµ Р·Р°РїСѓС‰РµРЅР°: {exc}")
            return self._redirect_after_error(batch=batch)
        except Exception as exc:
            messages.error(request, f"РћС€РёР±РєР° РіРµРЅРµСЂР°С†РёРё РєРѕРјРїР»РµРєС‚Р°: {exc}")
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

    def _redirect_after_error(self, *, batch: DocumentBatch):
        return redirect(
            f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=3"
        )

    def _legacy_success_message_stub(self, *, result) -> str:
        parts: list[str] = []

        if result.registries_generated_count:
            parts.append(f"СЂРµРµСЃС‚СЂРѕРІ СЃС„РѕСЂРјРёСЂРѕРІР°РЅРѕ: {result.registries_generated_count}")
        else:
            parts.append("СЂРµРµСЃС‚СЂС‹ РЅРµ С„РѕСЂРјРёСЂРѕРІР°Р»РёСЃСЊ")

        if result.letter_generated:
            parts.append("РїРёСЃСЊРјРѕ СЃС„РѕСЂРјРёСЂРѕРІР°РЅРѕ")
        else:
            parts.append("РїРёСЃСЊРјРѕ РЅРµ С„РѕСЂРјРёСЂРѕРІР°Р»РѕСЃСЊ")

        if getattr(result, "registries_auto_generated_for_letter", False):
            parts.append("С‡Р°СЃС‚СЊ СЂРµРµСЃС‚СЂРѕРІ Р±С‹Р»Р° Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РґРѕРіРµРЅРµСЂРёСЂРѕРІР°РЅР° РґР»СЏ РїРёСЃСЊРјР°")

        messages.success(
            request,
            f"Р“РµРЅРµСЂР°С†РёСЏ Р·Р°РІРµСЂС€РµРЅР°: {', '.join(parts)}.",
        )

    def _get_registry_template_path(self) -> Path:
        return Path(settings.XLSX_TEMPLATES_DIR) / "id_handover_registry.xlsx"

    def _build_success_message(self, *, result) -> str:
        parts: list[str] = []

        if result.registries_generated_count:
            parts.append(f"РЎР‚Р ВµР ВµРЎРѓРЎвЂљРЎР‚Р С•Р Р† РЎРѓРЎвЂћР С•РЎР‚Р СР С‘РЎР‚Р С•Р Р†Р В°Р Р…Р С•: {result.registries_generated_count}")
        else:
            parts.append("РЎР‚Р ВµР ВµРЎРѓРЎвЂљРЎР‚РЎвЂ№ Р Р…Р Вµ РЎвЂћР С•РЎР‚Р СР С‘РЎР‚Р С•Р Р†Р В°Р В»Р С‘РЎРѓРЎРЉ")

        if result.letter_generated:
            parts.append("Р С—Р С‘РЎРѓРЎРЉР СР С• РЎРѓРЎвЂћР С•РЎР‚Р СР С‘РЎР‚Р С•Р Р†Р В°Р Р…Р С•")
        else:
            parts.append("Р С—Р С‘РЎРѓРЎРЉР СР С• Р Р…Р Вµ РЎвЂћР С•РЎР‚Р СР С‘РЎР‚Р С•Р Р†Р В°Р В»Р С•РЎРѓРЎРЉ")

        if getattr(result, "registries_auto_generated_for_letter", False):
            parts.append("РЎвЂЎР В°РЎРѓРЎвЂљРЎРЉ РЎР‚Р ВµР ВµРЎРѓРЎвЂљРЎР‚Р С•Р Р† Р В±РЎвЂ№Р В»Р В° Р В°Р Р†РЎвЂљР С•Р СР В°РЎвЂљР С‘РЎвЂЎР ВµРЎРѓР С”Р С‘ Р Т‘Р С•Р С–Р ВµР Р…Р ВµРЎР‚Р С‘РЎР‚Р С•Р Р†Р В°Р Р…Р В° Р Т‘Р В»РЎРЏ Р С—Р С‘РЎРѓРЎРЉР СР В°")

        return f"Р вЂњР ВµР Р…Р ВµРЎР‚Р В°РЎвЂ Р С‘РЎРЏ Р В·Р В°Р Р†Р ВµРЎР‚РЎв‚¬Р ВµР Р…Р В°: {', '.join(parts)}."

    def _is_ajax_request(self, request) -> bool:
        return request.headers.get("x-requested-with") == "XMLHttpRequest"

    def _build_success_message(self, *, result) -> str:
        parts: list[str] = []

        if result.registries_generated_count:
            parts.append(f"СЂРµРµСЃС‚СЂРѕРІ СЃС„РѕСЂРјРёСЂРѕРІР°РЅРѕ: {result.registries_generated_count}")
        else:
            parts.append("СЂРµРµСЃС‚СЂС‹ РЅРµ С„РѕСЂРјРёСЂРѕРІР°Р»РёСЃСЊ")

        if result.letter_generated:
            parts.append("РїРёСЃСЊРјРѕ СЃС„РѕСЂРјРёСЂРѕРІР°РЅРѕ")
        else:
            parts.append("РїРёСЃСЊРјРѕ РЅРµ С„РѕСЂРјРёСЂРѕРІР°Р»РѕСЃСЊ")

        if getattr(result, "registries_auto_generated_for_letter", False):
            parts.append("С‡Р°СЃС‚СЊ СЂРµРµСЃС‚СЂРѕРІ Р±С‹Р»Р° Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РґРѕРіРµРЅРµСЂРёСЂРѕРІР°РЅР° РґР»СЏ РїРёСЃСЊРјР°")

        return f"Р“РµРЅРµСЂР°С†РёСЏ Р·Р°РІРµСЂС€РµРЅР°: {', '.join(parts)}."

    def _get_regular_letter_template_path(self) -> Path:
        return Path(settings.DOCX_TEMPLATES_DIR) / "id_handover_letter_for_execution.docx"

    def _get_archive_letter_template_path(self) -> Path:
        return Path(settings.DOCX_TEMPLATES_DIR) / "id_handover_letter_to_archive.docx"

