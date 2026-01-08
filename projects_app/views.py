from __future__ import annotations

import hashlib
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import UpdateView
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .forms import ProjectCreateForm, ProjectUpdateForm
from .models import (
    DesignStage,
    Designer,
    Line,
    Plot,
    Project,
    ProjectRevision,
    Section,
    Stage,
    TempUpload,
)
from .services import (
    DuplicatePdfSha256Error,
    assign_full_code_to_draft,
    attach_revision_to_project,
    change_project_full_code,
    delete_project_revision,
    ensure_project_files_named,
    normalize_full_code,
    process_single_pdf,
    set_revision_in_production,
    sync_needs_review,
)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def str_to_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() in ("1", "true", "yes", "on")


class JsonPermissionRequiredMixin(PermissionRequiredMixin):
    def handle_no_permission(self):
        return JsonResponse({"ok": False, "error": "Нет доступа"}, status=403)


class ProjectListView(PermissionRequiredMixin, ListView):
    permission_required = "projects_app.view_projects_page"
    raise_exception = True

    model = ProjectRevision
    template_name = "projects_app/projects_list.html"
    context_object_name = "revisions"

    def get_queryset(self):
        qs = (
            ProjectRevision.objects
            .select_related("project")
            .filter(is_latest=True)
            .order_by("project_id")
        )
        if str_to_bool(self.request.GET.get("needs_review")):
            qs = qs.filter(project__needs_review=True)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["needs_review_filter_on"] = str_to_bool(self.request.GET.get("needs_review"))
        ctx["needs_review_count"] = Project.objects.filter(needs_review=True).count()
        ctx["title"] = "Проекты"
        return ctx


class ProjectDetailView(PermissionRequiredMixin, DetailView):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    model = Project
    template_name = "projects_app/project_detail.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        project = self.object
        ctx["revisions"] = ProjectRevision.objects.filter(project=self.object).order_by("-created_at")
        ctx["latest_revision"] = (
            ProjectRevision.objects
            .filter(project=self.object, is_latest=True)
            .order_by("-created_at")
            .first()
        )
        ctx["title"] = f"Данные проекта — {project.full_code}"

        return ctx


@method_decorator(xframe_options_sameorigin, name="dispatch")
class ProjectRevisionOpenView(PermissionRequiredMixin, View):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    def get(self, request, pk: int):
        revision = get_object_or_404(ProjectRevision.objects.select_related("project"), pk=pk)
        file_path = Path(revision.file_path)

        if not file_path.exists():
            with transaction.atomic():
                ensure_project_files_named(revision.project)
                revision.refresh_from_db()
                file_path = Path(revision.file_path)

        if not file_path.exists():
            raise Http404("Файл не найден")

        return FileResponse(open(file_path, "rb"), content_type="application/pdf", as_attachment=False)


class ProjectCreateStartView(PermissionRequiredMixin, TemplateView):
    permission_required = "projects_app.add_project"
    raise_exception = True
    template_name = "projects_app/project_create.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = ProjectCreateForm()
        ctx["title"] = "Добавить проект"
        return ctx


class TempUploadPdfView(JsonPermissionRequiredMixin, View):
    permission_required = "projects_app.add_project"

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            return JsonResponse({"ok": False, "error": "Файл не получен"}, status=400)
        if not f.name.lower().endswith(".pdf"):
            return JsonResponse({"ok": False, "error": "Допустимы только PDF"}, status=400)

        h = hashlib.sha256()
        for chunk in f.chunks():
            h.update(chunk)
        sha256 = h.hexdigest()

        # ✅ Ранняя проверка дубля для UX (без лишних действий)
        existing = (
            ProjectRevision.objects
            .select_related("project")
            .filter(sha256=sha256)
            .first()
        )
        if existing:
            return JsonResponse(
                {
                    "ok": False,
                    "status": "duplicate",
                    "sha256": sha256,
                    "existing_project_id": existing.project_id,
                    "existing_project": existing.project.full_code,
                    "existing_revision_id": existing.id,
                    "existing_revision": existing.revision,
                },
                status=409,
            )

        tmp_dir = Path(tempfile.gettempdir()) / "doc_helper_uploads"
        ensure_dir(tmp_dir)

        tmp_path = tmp_dir / f"{sha256}.pdf"
        with tmp_path.open("wb") as out:
            for chunk in f.chunks():
                out.write(chunk)

        upload = TempUpload.objects.create(
            user=request.user,
            original_name=f.name,
            tmp_path=str(tmp_path),
            sha256=sha256,
        )

        return JsonResponse({"ok": True, "upload_id": str(upload.id), "sha256": sha256})


class ProjectCreateWithPdfView(PermissionRequiredMixin, View):
    permission_required = "projects_app.add_project"
    raise_exception = True
    success_url = reverse_lazy("projects:projects_list")

    @transaction.atomic
    def post(self, request):
        form = ProjectCreateForm(request.POST)
        if not form.is_valid():
            return render(request, "projects_app/project_create.html", {"form": form})

        upload = get_object_or_404(
            TempUpload.objects.select_for_update(),
            id=form.cleaned_data["upload_id"],
            user=request.user,
            is_used=False,
        )

        full_code = normalize_full_code(form.cleaned_data["full_code"])

        project = Project.objects.filter(full_code=full_code).first()
        if project is None:
            project = Project.objects.create(
                full_code=full_code,
                construction=form.cleaned_data.get("construction") or "",
                needs_review=True,
            )

        sync_needs_review(project, save=True)

        try:
            rev, _ = attach_revision_to_project(
                project=project,
                file_name=upload.original_name,
                temp_file_path=upload.tmp_path,
                sha256=upload.sha256,
            )
        except DuplicatePdfSha256Error as e:
            # подчистим temp + покажем ошибку без создания дубля
            upload.is_used = True
            upload.save(update_fields=["is_used"])
            try:
                Path(upload.tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

            form.add_error(
                None,
                f"Этот PDF уже загружен: {e.existing.project.full_code} (ревизия {e.existing.revision})",
            )
            return render(request, "projects_app/project_create.html", {"form": form})

        upload.is_used = True
        upload.save(update_fields=["is_used"])

        ensure_project_files_named(project)

        messages.success(request, f"Проект сохранён, версия {rev.revision}")
        return redirect(self.success_url)


class ProjectAssignFullCodeView(PermissionRequiredMixin, View):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    @transaction.atomic
    def post(self, request, pk: int):
        full_code = (request.POST.get("full_code") or "").strip()
        if not full_code:
            messages.error(request, "Введите полный шифр проекта")
            return redirect("projects:project_detail", pk=pk)

        project = assign_full_code_to_draft(draft_project_id=pk, full_code_input=full_code)
        ensure_project_files_named(project)

        messages.success(request, "Шифр назначен")
        return redirect("projects:project_detail", pk=project.pk)


class ProjectRevisionSetInProductionView(JsonPermissionRequiredMixin, View):
    permission_required = "projects_app.view_project_detail_page"

    @transaction.atomic
    def post(self, request, pk: int):
        value = str_to_bool(request.POST.get("value"))
        rev = set_revision_in_production(revision_id=pk, value=value)
        return JsonResponse({"ok": True, "value": rev.in_production})


class ProjectUpdateView(PermissionRequiredMixin, UpdateView):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    model = Project
    form_class = ProjectUpdateForm
    template_name = "projects_app/project_update.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        project = self.object
        ctx["latest_revision"] = (
            ProjectRevision.objects
            .filter(project=self.object, is_latest=True)
            .order_by("-created_at")
            .first()
        )
        ctx["title"] = f"Редактирование — {project.full_code}"
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        project = Project.objects.select_for_update().get(pk=self.object.pk)

        new_full_code = form.cleaned_data.get("full_code")
        if new_full_code:
            project = change_project_full_code(project, new_full_code)

        for field in ("designer", "line", "design_stage", "stage", "plot", "section", "construction"):
            setattr(project, field, form.cleaned_data.get(field))

        project.save()

        sync_needs_review(project, save=True)
        ensure_project_files_named(project)

        messages.success(self.request, "Данные проекта сохранены")
        return redirect("projects:project_detail", pk=project.pk)


class ProjectRevisionDeleteView(PermissionRequiredMixin, View):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    def post(self, request, pk: int):
        revision = ProjectRevision.objects.select_related("project").get(pk=pk)
        project_id = revision.project_id

        delete_project_revision(revision_id=pk)

        messages.success(request, "Версия проекта удалена")

        if Project.objects.filter(id=project_id).exists():
            return redirect("projects:project_detail", pk=project_id)

        return redirect("projects:projects_list")


class DictItemCreateView(JsonPermissionRequiredMixin, View):
    permission_required = "projects_app.add_project"

    MODEL_MAP = {
        "designer": Designer,
        "line": Line,
        "design_stage": DesignStage,
        "stage": Stage,
        "plot": Plot,
        "section": Section,
    }

    def post(self, request, dict_name: str):
        model = self.MODEL_MAP.get(dict_name)
        if model is None:
            return JsonResponse({"ok": False, "error": "Неизвестный справочник"}, status=400)

        code = (request.POST.get("code") or "").strip()
        full_name = (request.POST.get("full_name") or "").strip()
        is_active = str_to_bool(request.POST.get("is_active"))

        if not code or not full_name:
            return JsonResponse({"ok": False, "error": "code и full_name обязательны"}, status=400)

        try:
            obj, created = model.objects.get_or_create(
                code=code,
                defaults={"full_name": full_name, "is_active": is_active},
            )
            if not created:
                obj.full_name = full_name
                obj.is_active = is_active
                obj.save(update_fields=["full_name", "is_active"])
        except IntegrityError:
            return JsonResponse({"ok": False, "error": "Конфликт уникальности"}, status=409)

        return JsonResponse({"ok": True, "created": created, "id": obj.id, "text": str(obj)})


class ProjectUploadArchiveView(PermissionRequiredMixin, View):
    permission_required = "projects_app.add_project"
    raise_exception = True

    def post(self, request):
        archive = request.FILES.get("archive")
        if not archive or not archive.name.lower().endswith(".zip"):
            return JsonResponse({"ok": False, "error": "Нужен ZIP архив"}, status=400)

        results = []
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            zip_path = tmpdir / archive.name

            with zip_path.open("wb") as f:
                for chunk in archive.chunks():
                    f.write(chunk)

            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmpdir)

            for pdf in tmpdir.rglob("*.pdf"):
                results.append(process_single_pdf(pdf_path=pdf, original_name=pdf.name, user=request.user))

        return JsonResponse({"ok": True, "processed": len(results), "results": results})


class ProjectRevisionDownloadView(PermissionRequiredMixin, View):
    permission_required = "projects_app.open_project_revision_pdf"
    raise_exception = True

    def get(self, request, pk: int):
        rev = get_object_or_404(ProjectRevision.objects.select_related("project"), pk=pk)
        file_path = Path(rev.file_path)

        if not file_path.exists():
            raise Http404("PDF файл не найден")

        filename = file_path.name
        if not filename.lower().endswith(".pdf"):
            filename = f"{filename}.pdf"

        resp = FileResponse(file_path.open("rb"), content_type="application/pdf", as_attachment=True)
        resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        return resp
