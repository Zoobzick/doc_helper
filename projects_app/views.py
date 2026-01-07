from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView, UpdateView

from .forms import ProjectUpdateForm, ProjectCreateForm
from .models import Project, ProjectRevision, TempUpload
from .services import (
    attach_revision_to_project,
    assign_full_code_to_draft,
    normalize_full_code,
    set_revision_in_production,
    delete_project_revision,
)


# =========================================================
# ВСПОМОГАТЕЛЬНОЕ
# =========================================================

def ensure_dir(path: Path) -> None:
    # (path) директория, которую нужно гарантированно создать
    path.mkdir(parents=True, exist_ok=True)


def str_to_bool(value: str | None) -> bool:
    # (value) строка из GET/POST
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "on", "yes", "y"}


class JsonPermissionRequiredMixin(PermissionRequiredMixin):
    """
    Чтобы fetch/AJAX всегда получал JSON, а не HTML-редирект.
    """
    raise_exception = True

    def handle_no_permission(self):
        return JsonResponse(
            {"ok": False, "error": "Недостаточно прав"},
            status=403,
        )


# =========================================================
# СПИСОК ПРОЕКТОВ
# =========================================================

class ProjectListView(PermissionRequiredMixin, ListView):
    permission_required = "projects_app.view_projects_page"
    raise_exception = True

    model = ProjectRevision
    template_name = "projects_app/projects_list.html"
    context_object_name = "revisions"

    def get_queryset(self):
        """
        В списке показываем ТОЛЬКО актуальные ревизии проектов.
        + (needs_review) фильтр "Требует уточнения": ?needs_review=1
        """
        qs = (
            ProjectRevision.objects
            .select_related("project")
            .filter(is_latest=True)
            .order_by("project_id")
        )

        # (needs_review) пришло из переключателя на странице списка
        needs_review = str_to_bool(self.request.GET.get("needs_review"))
        if needs_review:
            qs = qs.filter(project__needs_review=True)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # (needs_review_count) число проектов, у которых needs_review=True
        ctx["needs_review_count"] = Project.objects.filter(needs_review=True).count()

        # (needs_review_filter_on) чтобы чекбокс на странице был в правильном состоянии
        ctx["needs_review_filter_on"] = str_to_bool(self.request.GET.get("needs_review"))

        return ctx


# =========================================================
# ДЕТАЛИ ПРОЕКТА
# =========================================================

class ProjectDetailView(PermissionRequiredMixin, DetailView):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    model = Project
    template_name = "projects_app/project_detail.html"
    context_object_name = "project"

    def get_queryset(self):
        return Project.objects.prefetch_related("revisions")


# =========================================================
# ОТКРЫТИЕ PDF
# =========================================================

class ProjectRevisionOpenView(PermissionRequiredMixin, View):
    permission_required = "projects_app.open_project_revision_pdf"
    raise_exception = True

    def get(self, request, pk: int):
        rev = get_object_or_404(ProjectRevision, pk=pk)

        file_path = Path(rev.file_path)
        if not file_path.exists():
            raise Http404("PDF файл не найден")

        return FileResponse(
            open(file_path, "rb"),
            content_type="application/pdf",
            as_attachment=False,
        )


# =========================================================
# СОЗДАНИЕ ПРОЕКТА (WEB)
# =========================================================

class ProjectCreateStartView(PermissionRequiredMixin, TemplateView):
    permission_required = "projects_app.add_project"
    raise_exception = True
    template_name = "projects_app/project_create.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # (form) форма создания проекта
        ctx["form"] = ProjectCreateForm()

        # (show_needs_review_badge) чтобы на create-странице НЕ показывать лишнее "Требует уточнения"
        ctx["show_needs_review_badge"] = False

        return ctx


class TempUploadPdfView(JsonPermissionRequiredMixin, View):
    """
    Приём PDF → TempUpload.
    """
    permission_required = "projects_app.add_project"

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            return JsonResponse({"ok": False, "error": "Файл не получен"}, status=400)

        if not f.name.lower().endswith(".pdf"):
            return JsonResponse({"ok": False, "error": "Допустимы только PDF"}, status=400)

        projects_root = Path(settings.PROJECTS_DIR)
        tmp_dir = projects_root / "_tmp_uploads"
        ensure_dir(tmp_dir)

        tmp_path = tmp_dir / f"{request.user.id}_{os.urandom(6).hex()}.pdf"
        with tmp_path.open("wb") as out:
            for chunk in f.chunks():
                out.write(chunk)

        # sha256
        import hashlib
        h = hashlib.sha256()
        with tmp_path.open("rb") as rr:
            for chunk in iter(lambda: rr.read(1024 * 1024), b""):
                h.update(chunk)
        sha256 = h.hexdigest()

        # глобальный дедуп
        if ProjectRevision.objects.filter(sha256=sha256).exists():
            tmp_path.unlink(missing_ok=True)
            return JsonResponse({
                "ok": True,
                "duplicate": True,
                "message": "Такой PDF уже есть в системе",
            })

        upload = TempUpload.objects.create(
            user=request.user,
            original_name=f.name,
            tmp_path=str(tmp_path),
            sha256=sha256,
        )

        return JsonResponse({
            "ok": True,
            "duplicate": False,
            "upload_id": str(upload.id),
            "filename": upload.original_name,
            "sha256": upload.sha256,
        })


class ProjectCreateWithPdfView(PermissionRequiredMixin, View):
    """
    Сохранение проекта после загрузки PDF.
    """
    permission_required = "projects_app.add_project"
    raise_exception = True
    success_url = reverse_lazy("projects:projects_list")

    @transaction.atomic
    def post(self, request):
        form = ProjectCreateForm(request.POST)
        if not form.is_valid():
            return TemplateView.as_view(
                template_name="projects_app/project_create.html"
            )(request, form=form)

        upload = get_object_or_404(
            TempUpload.objects.select_for_update(),
            id=form.cleaned_data["upload_id"],
            user=request.user,
            is_used=False,
        )

        full_code = normalize_full_code(form.cleaned_data["full_code"])
        project = Project.objects.filter(full_code=full_code).first()

        projects_root = Path(settings.PROJECTS_DIR)

        if project is None:
            project = form.save(commit=False)
            project.full_code = full_code

            # (needs_review) при создании через create/save считаем, что уточнение не требуется
            project.needs_review = False
            project.save()
        else:
            project.needs_review = False
            project.save(update_fields=["needs_review"])

        project_dir = projects_root / full_code
        ensure_dir(project_dir)

        tmp_target = project_dir / f"__incoming__{upload.sha256}.pdf"
        Path(upload.tmp_path).replace(tmp_target)

        rev, created = attach_revision_to_project(
            project=project,
            file_name=upload.original_name,
            file_path=str(tmp_target),
            sha256=upload.sha256,
        )

        if not created:
            tmp_target.unlink(missing_ok=True)
            upload.is_used = True
            upload.save(update_fields=["is_used"])
            messages.warning(request, "Дубликат PDF")
            return redirect(self.success_url)

        final_path = project_dir / f"{full_code}-{rev.revision}.pdf"
        tmp_target.replace(final_path)
        rev.file_path = str(final_path)
        rev.save(update_fields=["file_path"])

        upload.is_used = True
        upload.save(update_fields=["is_used"])

        messages.success(request, f"Проект сохранён, версия {rev.revision}")
        return redirect(self.success_url)


# =========================================================
# НАЗНАЧЕНИЕ FULL_CODE ЧЕРНОВИКУ
# =========================================================

class ProjectAssignFullCodeView(PermissionRequiredMixin, View):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    @transaction.atomic
    def post(self, request, pk: int):
        full_code = (request.POST.get("full_code") or "").strip()
        if not full_code:
            messages.error(request, "Введите полный шифр проекта")
            return redirect("projects:project_detail", pk=pk)

        project = assign_full_code_to_draft(
            draft_project_id=pk,
            full_code_input=full_code,
        )

        messages.success(request, "Шифр проекта сохранён")
        return redirect("projects:project_detail", pk=project.pk)


# =========================================================
# В ПРОИЗВОДСТВО РАБОТ (AJAX)
# =========================================================

class ProjectRevisionSetInProductionView(JsonPermissionRequiredMixin, View):
    """
    POST: { value: true/false }
    """
    permission_required = "projects_app.view_project_detail_page"

    def post(self, request, pk: int):
        value = request.POST.get("value") == "true"

        try:
            revision = set_revision_in_production(
                revision_id=pk,
                value=value,
            )
        except ProjectRevision.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Ревизия не найдена"}, status=404)

        return JsonResponse({
            "ok": True,
            "in_production": revision.in_production,
        })


class ProjectUpdateView(PermissionRequiredMixin, UpdateView):
    """
    Редактирование проекта:
    заполнение классификаторов.
    """
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    model = Project
    form_class = ProjectUpdateForm
    template_name = "projects_app/project_update.html"
    context_object_name = "project"

    def form_valid(self, form):
        project = form.save()
        messages.success(self.request, "Данные проекта сохранены")
        return redirect("projects:project_detail", pk=project.pk)


class ProjectRevisionDeleteView(PermissionRequiredMixin, View):
    """
    Удаление ревизии проекта.
    """
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    def post(self, request, pk: int):
        revision = ProjectRevision.objects.select_related("project").get(pk=pk)
        project_id = revision.project_id

        delete_project_revision(revision_id=pk)

        messages.success(request, "Версия проекта удалена")

        # если проект ещё существует — вернёмся в детали
        if Project.objects.filter(id=project_id).exists():
            return redirect("projects:project_detail", pk=project_id)

        # иначе — в список
        return redirect("projects:projects_list")
