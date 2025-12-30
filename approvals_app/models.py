from django.db import models
from django.utils import timezone
from django.core.files.storage import FileSystemStorage
import os

from projects_app.models import Project

APPROVALS_ROOT = r"\\Marina\ид участок №5 (липовая роща)\Согласования"
approvals_storage = FileSystemStorage(location=APPROVALS_ROOT)


def approval_upload_to(instance: "Approval", filename: str) -> str:
    # (status_dir) — папка для "на согласовании"
    status_dir = "На согласовании" if instance.status == Approval.Status.PENDING else ""

    # (project_dir) — папка проекта или "Общие"
    project_dir = instance.project.full_code if instance.project else "Общие"
    project_dir = project_dir.replace("/", "_").replace("\\", "_").strip()

    # итог: "На согласовании/<шифр>/file.pdf" или "<шифр>/file.pdf"
    return os.path.join(status_dir, project_dir, filename)


class Approval(models.Model):
    class Status(models.TextChoices):
        DONE = "DONE", "Согласовано"
        PENDING = "PENDING", "На согласовании"

    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approvals",
        verbose_name="Проект",
    )

    description = models.TextField("Описание", blank=True)

    file = models.FileField(
        "PDF файл",
        upload_to=approval_upload_to,
        storage=approvals_storage,
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,  # логично: новое — обычно "на согласовании"
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

    def __str__(self):
        return self.project.full_code if self.project else "Общее согласование"
