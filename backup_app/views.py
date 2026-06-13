from __future__ import annotations

import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from backup_app.models import BackupRun
from backup_app.services import create_backup_for_run_in_background, get_backup_root


@dataclass(frozen=True)
class BackupListItem:
    key: str
    status: str
    status_label: str
    trigger_label: str
    created_at: datetime | None
    size_bytes: int
    error: str
    filename: str
    db_id: int | None = None
    file_exists: bool = False
    source_label: str = "БД"

    @property
    def display_id(self) -> str:
        if self.db_id:
            return f"#{self.db_id}"
        return "—"

    @property
    def can_download(self) -> bool:
        return self.status == BackupRun.Status.SUCCESS and self.file_exists

    @property
    def can_delete(self) -> bool:
        return bool(self.db_id) or self.file_exists


def _safe_backup_file_path(filename: str) -> Path:
    if filename != Path(filename).name:
        raise Http404("Некорректное имя файла.")

    path = (get_backup_root() / filename).resolve()
    backup_root = get_backup_root().resolve()
    if (
        path.parent != backup_root
        or path.suffix.lower() != ".zip"
        or path.name.endswith(".tmp.zip")
        or not path.is_file()
    ):
        raise Http404("Файл бэкапа не найден.")
    return path


def _backup_file_created_at(path: Path) -> datetime:
    try:
        with zipfile.ZipFile(path) as archive:
            with archive.open("meta.json") as meta_file:
                import json

                payload = json.load(meta_file)
                created_at = payload.get("created_at")
                if created_at:
                    parsed = datetime.fromisoformat(created_at)
                    if timezone.is_naive(parsed):
                        return timezone.make_aware(parsed, timezone.get_current_timezone())
                    return parsed
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone())


def _backup_file_trigger_label(path: Path) -> str:
    if path.name == "doc_helper_deploy_latest.zip":
        return BackupRun.Trigger.DEPLOY.label
    return "Файл на диске"


def _iter_backup_files() -> list[Path]:
    backup_root = get_backup_root()
    if not backup_root.exists():
        return []
    return sorted(
        (
            path
            for path in backup_root.glob("*.zip")
            if path.is_file() and not path.name.endswith(".tmp.zip")
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


class BackupListView(StaffRequiredMixin, ListView):
    model = BackupRun
    template_name = "backup_app/backup_list.html"
    context_object_name = "backups"
    paginate_by = 20

    def get_queryset(self):
        items: list[BackupListItem] = []
        seen_paths: set[Path] = set()

        for backup in BackupRun.objects.select_related("created_by").order_by("-created_at", "-id"):
            path = Path(backup.file_path).resolve() if backup.file_path else None
            file_exists = bool(path and path.is_file())
            if path and file_exists:
                seen_paths.add(path)

            items.append(
                BackupListItem(
                    key=f"db:{backup.pk}",
                    db_id=backup.pk,
                    status=backup.status,
                    status_label=backup.get_status_display(),
                    trigger_label=backup.get_trigger_display(),
                    created_at=backup.created_at,
                    size_bytes=backup.size_bytes if backup.size_bytes else (path.stat().st_size if file_exists else 0),
                    error=backup.error,
                    filename=path.name if path else "",
                    file_exists=file_exists,
                    source_label="БД",
                )
            )

        for path in _iter_backup_files():
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            items.append(
                BackupListItem(
                    key=f"file:{path.name}",
                    status=BackupRun.Status.SUCCESS,
                    status_label=BackupRun.Status.SUCCESS.label,
                    trigger_label=_backup_file_trigger_label(path),
                    created_at=_backup_file_created_at(path),
                    size_bytes=path.stat().st_size,
                    error="",
                    filename=path.name,
                    file_exists=True,
                    source_label="Файл",
                )
            )

        return sorted(items, key=lambda item: item.created_at or datetime.min, reverse=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_running_backup"] = BackupRun.objects.filter(status=BackupRun.Status.RUNNING).exists()
        return context


class BackupCreateView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        running_exists = BackupRun.objects.filter(status=BackupRun.Status.RUNNING).exists()
        if running_exists:
            messages.warning(request, "Бэкап уже создаётся. Дождитесь завершения текущей сборки.")
            return redirect(reverse("backup_app:backup_list"))

        run = BackupRun.objects.create(
            created_by=request.user,
            trigger=BackupRun.Trigger.MANUAL,
            reason="Запуск из интерфейса",
        )
        thread = threading.Thread(
            target=create_backup_for_run_in_background,
            args=(run.pk,),
            daemon=True,
        )
        thread.start()
        messages.info(request, f"Бэкап #{run.pk} запущен. Страница будет обновляться до завершения.")
        return redirect(reverse("backup_app:backup_list"))


class BackupDownloadView(StaffRequiredMixin, View):
    def get(self, request: HttpRequest, pk: int) -> FileResponse:
        backup = get_object_or_404(BackupRun, pk=pk, status=BackupRun.Status.SUCCESS)
        path = Path(backup.file_path)
        if not path.exists() or not path.is_file():
            raise Http404("Файл бэкапа не найден.")
        return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)


class BackupFileDownloadView(StaffRequiredMixin, View):
    def get(self, request: HttpRequest, filename: str) -> FileResponse:
        path = _safe_backup_file_path(filename)
        return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)


class BackupDeleteView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        backup = get_object_or_404(BackupRun, pk=pk)
        path = Path(backup.file_path) if backup.file_path else None
        if path and path.exists() and path.is_file():
            path.unlink()
        backup.delete()
        messages.success(request, "Запись бэкапа удалена.")
        return redirect(reverse("backup_app:backup_list"))


class BackupFileDeleteView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, filename: str) -> HttpResponse:
        path = _safe_backup_file_path(filename)
        path.unlink()
        messages.success(request, "Файл бэкапа удалён.")
        return redirect(reverse("backup_app:backup_list"))
