from __future__ import annotations

from pathlib import Path

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from backup_app.models import BackupRun
from backup_app.services import create_backup


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


class BackupListView(StaffRequiredMixin, ListView):
    model = BackupRun
    template_name = "backup_app/backup_list.html"
    context_object_name = "backups"
    paginate_by = 20


class BackupCreateView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest) -> HttpResponse:
        try:
            result = create_backup(
                trigger=BackupRun.Trigger.MANUAL,
                user=request.user,
                reason="Запуск из интерфейса",
            )
        except Exception as exc:
            messages.error(request, f"Не удалось создать бэкап: {exc}")
        else:
            messages.success(request, f"Бэкап #{result.run.pk} создан.")
        return redirect(reverse("backup_app:backup_list"))


class BackupDownloadView(StaffRequiredMixin, View):
    def get(self, request: HttpRequest, pk: int) -> FileResponse:
        backup = get_object_or_404(BackupRun, pk=pk, status=BackupRun.Status.SUCCESS)
        path = Path(backup.file_path)
        if not path.exists() or not path.is_file():
            raise Http404("Файл бэкапа не найден.")
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
