from __future__ import annotations

import os
import re

import uuid
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone

from projects_app.models import Project

# (approvals_storage) — корень хранения именно для approvals:
# физически: <BASE_ID_DIR>/Согласования/...
approvals_storage = FileSystemStorage(
    location=str(settings.APPROVALS_DIR),
    base_url=None,  # обычно файлы согласований не должны раздаваться как MEDIA
)

_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\n\r\t]')


def _safe_segment(value: str, fallback: str = "_unparsed") -> str:
    """
    value (str) — название папки (например full_code проекта)
    fallback (str) — что вернуть, если value пустое/плохое

    Делает строку безопасной для имени папки (Windows/Linux).
    """
    s = " ".join((value or "").strip().split())
    if not s:
        return fallback
    s = _INVALID_CHARS_RE.sub("_", s).strip(" .")
    return s or fallback


def _safe_filename(filename: str) -> str:
    """
    filename (str) — имя файла от клиента
    Берём только basename, чтобы исключить подмешивание путей.
    """
    return os.path.basename(filename)


def approval_upload_to(instance: "Approval", filename: str) -> str:
    """
    Возвращает ОТНОСИТЕЛЬНЫЙ путь внутри approvals_storage (storage/Согласования).
    """
    # (status_dir) — папка статуса
    status_dir = "На согласовании" if instance.status == Approval.Status.PENDING else "Согласовано"

    # (project_dir) — папка проекта или "Общие"
    project_code = instance.project.full_code if instance.project else "Общие"
    project_dir = _safe_segment(project_code, fallback="Общие")

    return os.path.join(status_dir, project_dir, _safe_filename(filename))


class Approval(models.Model):
    class Status(models.TextChoices):
        DONE = "DONE", "Согласовано"
        PENDING = "PENDING", "На согласовании"

    uuid = models.UUIDField("UUID", default=uuid.uuid4, editable=False, unique=True, db_index=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approvals",
        verbose_name="Проект",
    )

    construction = models.CharField("Сооружение", max_length=255, blank=True, default="")

    description = models.TextField("Описание", blank=True)

    file = models.FileField(
        "PDF файл",
        upload_to=approval_upload_to,
        storage=approvals_storage,  # <-- ключевое изменение
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Согласование"
        verbose_name_plural = "Согласования"
        ordering = ["-created_at"]
        permissions = [
            ("view_approvals_done_page", "Can view DONE approvals page"),
            ("view_approvals_pending_page", "Can view PENDING approvals page"),
            ("add_approvals_done", "Can add DONE approvals"),
            ("add_approvals_pending", "Can add PENDING approvals"),
            ("delete_approvals", "Can delete approvals"),
        ]

    def __str__(self) -> str:
        return self.project.full_code if self.project else "Общее согласование"
