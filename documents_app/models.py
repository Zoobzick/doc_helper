# documents_app/models.py
from __future__ import annotations

from django.db import models


class DocumentsPermissions(models.Model):
    """
    Техническая модель: нужна только для хранения кастомных permissions приложения.
    """

    class Meta:
        managed = False  # таблица в БД не создаётся
        default_permissions = ()  # стандартные add/change/delete/view не нужны
        permissions = [
            ("view_box_label_page", "Can view box label page"),
        ]


class TitleSheet(models.Model):
    class DocType(models.TextChoices):
        ID = "ID", "Исполнительная документация"
        RD = "RD", "Рабочая документация"
        ID_RD = "ID_RD", "Исполнительная и рабочая документация"

    project = models.ForeignKey(
        "projects_app.Project",
        on_delete=models.CASCADE,
        related_name="title_sheets",
        db_index=True,
        verbose_name="Проект",
    )

    doc_type = models.CharField(
        max_length=10,
        choices=DocType.choices,
        db_index=True,
        verbose_name="Тип титульного листа",
    )

    pdf_path = models.CharField(
        max_length=1024,
        blank=True,
        default="",
        verbose_name="Путь к PDF",
    )

    docx_path = models.CharField(  # на будущее
        max_length=1024,
        blank=True,
        default="",
        verbose_name="Путь к DOCX",
    )

    context_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="SHA256 данных",
    )

    template_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="SHA256 шаблона",
    )

    is_locked = models.BooleanField(
        default=False,
        verbose_name="Заморожено",
        help_text="Если True — титульник НЕ пересобирается автоматически",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Титульный лист"
        verbose_name_plural = "Титульные листы"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "doc_type"],
                name="uq_title_sheet_project_doc_type",
            ),
        ]
        indexes = [
            models.Index(fields=["doc_type"]),
            models.Index(fields=["is_locked"]),
        ]

    def __str__(self) -> str:
        return f"Титул {self.get_doc_type_display()} — {self.project.full_code}"
