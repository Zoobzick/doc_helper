from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.generic import DetailView, TemplateView

from documents_app.forms import BoxLabelForm, DocumentBatchMasterForm
from documents_app.models import DocumentBatch, GeneratedDocument, TitleSheet
from documents_app.services.box_label_docx import render_box_label_docx
from documents_app.services.id_handover.batch_composer import (
    BatchCreateParams,
    DocumentBatchComposer,
    DocumentBatchComposerValidationError,
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

DEFAULT_DSM = 'ГУП "Московский метрополитен"'
DEFAULT_MIP = 'АО "Мосинжпроект"'
DEFAULT_SMU = 'ООО "СМУ-12 Мосметростроя"'

MAX_LINES = 50


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
        form = self.get_form(batch=batch)

        context["page_title"] = "Комплекты"
        context["batch"] = batch
        context["form"] = form
        context["projects_count"] = Project.objects.count()
        context["preview_data"] = self._build_preview_safe(batch=batch) if batch else None
        context["generated_documents"] = self._get_generated_documents(batch=batch) if batch else []
        context["is_edit_mode"] = batch is not None

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

    def _build_preview_safe(self, *, batch: DocumentBatch) -> dict | None:
        builder = DocumentBatchPreviewBuilder()
        try:
            return builder.build(batch=batch)
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


class DocumentBatchCreateDraftView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "documents_app.add_documentbatch"
    raise_exception = True

    def post(self, request, *args, **kwargs):
        form = DocumentBatchMasterForm(request.POST)

        if not form.is_valid():
            return self._render_invalid_form(form=form)

        params = BatchCreateParams(
            created_by=request.user,
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
        return redirect("documents:id_handover_batch_master", batch_id=batch.id)

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
            },
            status=400,
        )


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
        context["page_title"] = f"Комплект ИД: {batch.title}"
        return context

    def _build_preview_safe(self, *, batch: DocumentBatch) -> dict | None:
        builder = DocumentBatchPreviewBuilder()
        try:
            return builder.build(batch=batch)
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
            messages.error(request, f"Генерация не запущена: {exc}")
            return self._redirect_after_error(batch=batch)
        except Exception as exc:
            messages.error(request, f"Ошибка генерации комплекта: {exc}")
            return self._redirect_after_error(batch=batch)

        self._add_success_message(request=request, result=result)
        return redirect("documents:id_handover_batch_detail", batch_id=batch.id)

    def _redirect_after_error(self, *, batch: DocumentBatch):
        return redirect("documents:id_handover_batch_master", batch_id=batch.id)

    def _add_success_message(self, *, request, result) -> None:
        parts: list[str] = []

        if result.registries_generated_count:
            parts.append(f"реестров: {result.registries_generated_count}")

        if result.letter_generated:
            parts.append("письмо: да")

        if not parts:
            parts.append("документы не были сформированы")

        messages.success(
            request,
            f"Генерация завершена ({', '.join(parts)}).",
        )

    def _get_registry_template_path(self) -> Path:
        return Path(settings.XLSX_TEMPLATES_DIR) / "id_handover_registry.xlsx"

    def _get_regular_letter_template_path(self) -> Path:
        return Path(settings.DOCX_TEMPLATES_DIR) / "id_handover_letter_for_execution.docx"

    def _get_archive_letter_template_path(self) -> Path:
        return Path(settings.DOCX_TEMPLATES_DIR) / "id_handover_letter_to_archive.docx"