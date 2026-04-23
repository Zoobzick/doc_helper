from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
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
        return step if step in (1, 2, 3) else (2 if self.get_batch() else 1)

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

        context["page_title"] = "Комплекты документов"
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

                result = DocumentBatchRefreshCompositionView()._refresh_batch_composition(batch=batch)
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
    def _refresh_batch_composition(self, *, batch: DocumentBatch) -> dict[str, int]:
        project_ids_from_batch = list(
            batch.batch_projects.order_by("order", "id").values_list("project_id", flat=True)
        )

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
        fragment = f"batch-act-{batch_act.id}"

        if batch_act.order <= 1:
            messages.info(request, "Этот акт уже находится на первом месте в проекте.")
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
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)
        except Exception as exc:
            messages.error(request, f"Ошибка перемещения акта вверх: {exc}")
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)

        messages.success(request, "Акт перемещён вверх.")
        return self.redirect_to_master(batch=batch, step=2, fragment=fragment)


class DocumentBatchMoveActDownView(DocumentBatchStep2BaseActionView):
    def post(self, request, *args, **kwargs):
        batch = self.get_batch(batch_id=kwargs["batch_id"])
        batch_act = self.get_batch_act(batch=batch, batch_act_id=kwargs["batch_act_id"])
        fragment = f"batch-act-{batch_act.id}"

        project_items_count = DocumentBatchAct.objects.filter(
            batch=batch,
            project_id=batch_act.project_id,
        ).count()

        if batch_act.order >= project_items_count:
            messages.info(request, "Этот акт уже находится на последнем месте в проекте.")
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
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)
        except Exception as exc:
            messages.error(request, f"Ошибка перемещения акта вниз: {exc}")
            return self.redirect_to_master(batch=batch, step=2, fragment=fragment)

        messages.success(request, "Акт перемещён вниз.")
        return self.redirect_to_master(batch=batch, step=2, fragment=fragment)


class DocumentBatchRemoveActView(DocumentBatchStep2BaseActionView):
    def post(self, request, *args, **kwargs):
        batch = self.get_batch(batch_id=kwargs["batch_id"])
        batch_act = self.get_batch_act(batch=batch, batch_act_id=kwargs["batch_act_id"])
        fallback_fragment = f"project-{batch_act.project_id}"

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
            return self.redirect_to_master(batch=batch, step=2, fragment=fallback_fragment)
        except Exception as exc:
            messages.error(request, f"Ошибка удаления акта: {exc}")
            return self.redirect_to_master(batch=batch, step=2, fragment=fallback_fragment)

        source_label = "manual" if source == DocumentBatchActSource.MANUAL else "auto"
        messages.success(
            request,
            f"Акт №{act_number} удалён из проекта {project_code} ({source_label}).",
        )
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
        return redirect(
            f"{reverse('documents:id_handover_batch_master', kwargs={'batch_id': batch.id})}?step=3"
        )

    def _add_success_message(self, *, request, result) -> None:
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

    def _get_regular_letter_template_path(self) -> Path:
        return Path(settings.DOCX_TEMPLATES_DIR) / "id_handover_letter_for_execution.docx"

    def _get_archive_letter_template_path(self) -> Path:
        return Path(settings.DOCX_TEMPLATES_DIR) / "id_handover_letter_to_archive.docx"
