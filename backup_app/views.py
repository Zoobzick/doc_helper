from __future__ import annotations

import threading
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from backup_app.models import BackupRun
from backup_app.services import create_backup_for_run_in_background


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


class BackupListView(StaffRequiredMixin, ListView):
    model = BackupRun
    template_name = "backup_app/backup_list.html"
    context_object_name = "backups"
    paginate_by = 20

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


class BackupDeleteView(StaffRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        backup = get_object_or_404(BackupRun, pk=pk)
        path = Path(backup.file_path) if backup.file_path else None
        if path and path.exists() and path.is_file():
            path.unlink()
        backup.delete()
        messages.success(request, "Запись бэкапа удалена.")
        return redirect(reverse("backup_app:backup_list"))
