from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class BackupRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Выполняется"
        SUCCESS = "SUCCESS", "Успешно"
        FAILED = "FAILED", "Ошибка"

    class Trigger(models.TextChoices):
        MANUAL = "MANUAL", "Вручную"
        DEPLOY = "DEPLOY", "Перед деплоем"
        SCHEDULED = "SCHEDULED", "По расписанию"

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="backup_runs",
        verbose_name="Кто запустил",
    )
    trigger = models.CharField("Тип запуска", max_length=16, choices=Trigger.choices, default=Trigger.MANUAL)
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.RUNNING, db_index=True)
    reason = models.CharField("Причина", max_length=255, blank=True, default="")
    file_path = models.CharField("Файл", max_length=1024, blank=True, default="")
    s3_key = models.CharField("S3 key", max_length=1024, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField("Размер", default=0)
    error = models.TextField("Ошибка", blank=True, default="")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    completed_at = models.DateTimeField("Завершён", null=True, blank=True)

    class Meta:
        verbose_name = "Бэкап"
        verbose_name_plural = "Бэкапы"
        ordering = ("-created_at", "-id")

    def mark_success(self, *, file_path: str, size_bytes: int, s3_key: str = "") -> None:
        self.status = self.Status.SUCCESS
        self.file_path = file_path
        self.s3_key = s3_key
        self.size_bytes = size_bytes
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "file_path", "s3_key", "size_bytes", "completed_at"])

    def mark_failed(self, error: str) -> None:
        self.status = self.Status.FAILED
        self.error = error
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "error", "completed_at"])

    def __str__(self) -> str:
        return f"Бэкап #{self.pk} — {self.get_status_display()}"
