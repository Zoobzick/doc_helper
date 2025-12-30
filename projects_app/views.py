from __future__ import annotations

import hashlib
import os
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin, LoginRequiredMixin
from django.core.management import call_command
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView

from .forms import ProjectCreateForm
from .models import (
    Project, ProjectRevision, TempUpload,
    Designer, Line, DesignStage, Stage, Plot, Section
)


# ---------- helpers ----------

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def next_revision_code(project: Project) -> str:
    last = (
        ProjectRevision.objects
        .filter(project=project)
        .order_by("-revision")
        .values_list("revision", flat=True)
        .first()
    )
    if not last:
        return "01"
    try:
        n = int(last)
    except ValueError:
        n = 1
    return f"{n + 1:02d}"


class JsonPermissionRequiredMixin(PermissionRequiredMixin):
    """
    ВАЖНО: чтобы fetch() не получал HTML-редирект/страницу, а всегда JSON.
    """
    raise_exception = True

    def handle_no_permission(self):
        return JsonResponse({"ok": False, "error": "Нет прав или вы не авторизованы"}, status=403)


# ---------- existing pages (минимально оставил) ----------

class ProjectListView(PermissionRequiredMixin, ListView):
    permission_required = "projects_app.view_projects_page"
    raise_exception = True

    model = ProjectRevision
    template_name = "projects_app/projects_list.html"
    context_object_name = "revisions"

    def get_queryset(self):
        return (
            ProjectRevision.objects
            .select_related(
                "project",
                "project__designer",
                "project__line",
                "project__design_stage",
                "project__stage",
                "project__plot",
                "project__section",
            )
            .order_by("project_id", "revision")
        )


class ProjectDetailView(PermissionRequiredMixin, DetailView):
    permission_required = "projects_app.view_project_detail_page"
    raise_exception = True

    model = Project
    template_name = "projects_app/project_detail.html"
    context_object_name = "project"


class ProjectRevisionOpenView(PermissionRequiredMixin, View):
    permission_required = "projects_app.open_project_revision_pdf"
    raise_exception = True

    def get(self, request, pk: int):
        rev = ProjectRevision.objects.filter(pk=pk).first()
        if not rev:
            raise Http404("Версия проекта не найдена")

        p = Path(rev.file_path) if rev.file_path else None
        if not p or not p.exists():
            raise Http404("PDF файл не найден на диске")

        return FileResponse(open(p, "rb"), content_type="application/pdf", as_attachment=False)


class ScanProjectsView(PermissionRequiredMixin, View):
    permission_required = "projects_app.scan_projects"
    raise_exception = True

    def post(self, request):
        try:
            call_command("scan_projects")
            messages.success(request, "Проекты успешно обновлены")
        except Exception as e:
            messages.error(request, f"Ошибка при обновлении проектов: {e}")
        return redirect("projects:projects_list")


# ---------- manual add project with drag&drop ----------

class ProjectCreateStartView(PermissionRequiredMixin, TemplateView):
    permission_required = "projects_app.add_project"
    raise_exception = True
    template_name = "projects_app/project_create.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = ProjectCreateForm()
        return ctx


class TempUploadPdfView(JsonPermissionRequiredMixin, View):
    """
    POST: file=pdf -> кладём в PROJECTS_ROOT/_tmp_uploads, считаем sha256.
    ВАЖНО: всегда отвечаем JSON, чтобы фронт не падал.
    """
    permission_required = "projects_app.add_project"

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            return JsonResponse({"ok": False, "error": "Файл не получен"}, status=400)

        name = f.name or "upload.pdf"
        if not name.lower().endswith(".pdf"):
            return JsonResponse({"ok": False, "error": "Разрешены только PDF"}, status=400)

        projects_root = Path(settings.PROJECTS_ROOT)
        tmp_dir = projects_root / "_tmp_uploads"
        ensure_dir(tmp_dir)

        tmp_path = tmp_dir / f"{request.user.id}_{os.urandom(6).hex()}.pdf"
        with tmp_path.open("wb") as out:
            for chunk in f.chunks():
                out.write(chunk)

        file_hash = sha256_file(tmp_path)

        # глобальный дедуп
        if ProjectRevision.objects.filter(sha256=file_hash).exists():
            tmp_path.unlink(missing_ok=True)
            return JsonResponse({
                "ok": True,
                "duplicate": True,
                "sha256": file_hash,
                "message": "Такой PDF уже есть в системе (дубликат по sha256)."
            })

        existing = TempUpload.objects.filter(user=request.user, sha256=file_hash, is_used=False).first()
        if existing:
            tmp_path.unlink(missing_ok=True)
            return JsonResponse({
                "ok": True,
                "duplicate": False,
                "upload_id": str(existing.id),
                "filename": existing.original_name,
                "sha256": existing.sha256,
            })

        upload = TempUpload.objects.create(
            user=request.user,
            original_name=name,
            tmp_path=str(tmp_path),
            sha256=file_hash,
            is_used=False,
        )

        return JsonResponse({
            "ok": True,
            "duplicate": False,
            "upload_id": str(upload.id),
            "filename": upload.original_name,
            "sha256": upload.sha256,
        })


class ProjectCreateWithPdfView(PermissionRequiredMixin, CreateView):
    permission_required = "projects_app.add_project"
    raise_exception = True

    model = Project
    form_class = ProjectCreateForm
    template_name = "projects_app/project_create.html"
    success_url = reverse_lazy("projects:projects_list")

    @transaction.atomic
    def form_valid(self, form):
        upload_id = form.cleaned_data["upload_id"]

        upload = TempUpload.objects.select_for_update().filter(id=upload_id, user=self.request.user).first()
        if not upload or upload.is_used:
            form.add_error(None, "Загрузка PDF не найдена или уже использована. Перезагрузите файл.")
            return self.form_invalid(form)

        if ProjectRevision.objects.filter(sha256=upload.sha256).exists():
            Path(upload.tmp_path).unlink(missing_ok=True)
            upload.is_used = True
            upload.save(update_fields=["is_used"])
            messages.warning(self.request, "Этот PDF уже есть в системе (дубликат по sha256).")
            return redirect("projects:projects_list")

        project: Project = form.save(commit=False)

        if not (project.construction or "").strip():
            project.needs_review = True

        project.save()
        form.save_m2m()

        rev_code = next_revision_code(project)
        ProjectRevision.objects.filter(project=project, is_latest=True).update(is_latest=False)

        projects_root = Path(settings.PROJECTS_ROOT)
        target_dir = projects_root / project.full_code
        ensure_dir(target_dir)

        target_name = f"{project.full_code}-{rev_code}.pdf"
        target_path = target_dir / target_name

        tmp_path = Path(upload.tmp_path)
        try:
            tmp_path.replace(target_path)
        except Exception:
            target_path.write_bytes(tmp_path.read_bytes())
            tmp_path.unlink(missing_ok=True)

        ProjectRevision.objects.create(
            project=project,
            revision=rev_code,
            file_name=upload.original_name,
            file_path=str(target_path),
            is_latest=True,
            sha256=upload.sha256,
        )

        upload.is_used = True
        upload.save(update_fields=["is_used"])

        messages.success(self.request, f"Проект сохранён. Версия {rev_code}")
        return redirect(self.success_url)


class AddCatalogItemView(JsonPermissionRequiredMixin, View):
    """
    JSON endpoint для модалки +.
    """
    permission_required = "projects_app.add_project"

    def post(self, request):
        kind = (request.POST.get("kind") or "").strip()
        code = (request.POST.get("code") or "").strip()
        full_name = (request.POST.get("full_name") or "").strip()

        if not kind or not code:
            return JsonResponse({"ok": False, "error": "kind и code обязательны"}, status=400)

        model_map = {
            "designer": (Designer, "projects_app.add_designer", True),
            "line": (Line, "projects_app.add_line", True),
            "design_stage": (DesignStage, "projects_app.add_designstage", True),
            "stage": (Stage, "projects_app.add_stage", False),
            "plot": (Plot, "projects_app.add_plot", True),
            "section": (Section, "projects_app.add_section", False),
        }
        if kind not in model_map:
            return JsonResponse({"ok": False, "error": "Неизвестный kind"}, status=400)

        Model, perm, needs_full_name = model_map[kind]
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return JsonResponse({"ok": False, "error": "Нет прав на добавление справочника"}, status=403)

        if needs_full_name and not full_name:
            return JsonResponse({"ok": False, "error": "full_name обязателен"}, status=400)

        if kind in ("stage", "section"):
            obj, _ = Model.objects.get_or_create(code=code)
            text = obj.code
        else:
            obj, created = Model.objects.get_or_create(code=code, defaults={"full_name": full_name, "is_active": True})
            if not created:
                if obj.full_name != full_name:
                    obj.full_name = full_name
                if hasattr(obj, "is_active") and obj.is_active is False:
                    obj.is_active = True
                obj.save()
            text = f"{obj.code} — {obj.full_name}"

        return JsonResponse({"ok": True, "id": obj.id, "text": text})
