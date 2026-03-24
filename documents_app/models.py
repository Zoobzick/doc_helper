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


import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from acts_app.models import Act
from projects_app.models import Project

MONTH_CODE_VALIDATOR = RegexValidator(
    regex=r"^(0[1-9]|1[0-2])\.\d{4}$",
    message='Используйте формат "MM.YYYY", например "03.2026".',
)


def batch_document_upload_to(instance, filename):
    """
    Путь хранения файла GeneratedDocument.

    instance (экземпляр GeneratedDocument)
    filename (исходное имя загружаемого файла)
    """
    extension = Path(filename).suffix.lower()
    batch_uuid = str(instance.batch.uuid)

    if instance.project_id:
        project_code = getattr(instance.project, "full_code", None) or f"project-{instance.project_id}"
        project_code = str(project_code).replace("/", "_").replace("\\", "_").strip()
        return (
            f"documents_app/id_handover/"
            f"batch_{batch_uuid}/"
            f"{project_code}/"
            f"{instance.document_type}/"
            f"{instance.uuid}{extension}"
        )

    return (
        f"documents_app/id_handover/"
        f"batch_{batch_uuid}/"
        f"common/"
        f"{instance.document_type}/"
        f"{instance.uuid}{extension}"
    )


class DocumentBatchSelectionMode(models.TextChoices):
    ALL_TIME = "all_time", "За весь период"
    RANGE = "range", "За период"


class DocumentBatchGenerationMode(models.TextChoices):
    REGISTRY_ONLY = "registry_only", "Только реестры"
    LETTER_ONLY = "letter_only", "Только письмо"
    FULL_SET = "full_set", "Полный комплект"


class DocumentBatchLetterType(models.TextChoices):
    FOR_EXECUTION = "for_execution", "Под выполнение"
    TO_ARCHIVE = "to_archive", "В архив"


class DocumentBatchProjectScope(models.TextChoices):
    ONE_PROJECT = "one_project", "Один проект"
    MULTI_PROJECT = "multi_project", "Несколько проектов"
    AUTO_BY_PERIOD = "auto_by_period", "Все проекты с актами за период"


class DocumentBatchDocumentationType(models.TextChoices):
    ID = "ID", "Исполнительная документация"
    RD = "RD", "Рабочая документация"
    ID_RD = "ID_RD", "Исполнительная и рабочая документация"


class DocumentBatchActSource(models.TextChoices):
    AUTO = "auto", "Автоматически"
    MANUAL = "manual", "Добавлен вручную"


class GeneratedDocumentType(models.TextChoices):
    REGISTRY_XLSX = "registry_xlsx", "Реестр XLSX"
    REGISTRY_PREVIEW_PDF = "registry_preview_pdf", "Preview реестра PDF"
    LETTER_DOCX = "letter_docx", "Письмо DOCX"
    LETTER_PREVIEW_PDF = "letter_preview_pdf", "Preview письма PDF"
    STAMPED_LETTER_PDF = "stamped_letter_pdf", "Письмо со штампом PDF"
    STAMPED_LETTER_IMAGE = "stamped_letter_image", "Письмо со штампом JPG/JPEG"


class GeneratedDocumentSourceKind(models.TextChoices):
    GENERATED = "generated", "Сгенерирован"
    UPLOADED = "uploaded", "Загружен"


class DocumentBatch(models.Model):
    """
    Контейнер комплекта сдачи ИД.

    Хранит:
    - параметры формирования
    - snapshot preview
    - состав проектов и актов через связанные модели
    """

    uuid = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name="UUID",
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Название комплекта",
        help_text="Необязательное пользовательское название комплекта.",
    )
    comment = models.TextField(
        blank=True,
        verbose_name="Комментарий",
    )

    selection_mode = models.CharField(
        max_length=20,
        choices=DocumentBatchSelectionMode.choices,
        default=DocumentBatchSelectionMode.ALL_TIME,
        verbose_name="Режим отбора",
    )
    month_from = models.CharField(
        max_length=7,
        blank=True,
        validators=[MONTH_CODE_VALIDATOR],
        verbose_name="Месяц от",
        help_text='Формат: "MM.YYYY".',
    )
    month_to = models.CharField(
        max_length=7,
        blank=True,
        validators=[MONTH_CODE_VALIDATOR],
        verbose_name="Месяц до",
        help_text='Формат: "MM.YYYY".',
    )

    generation_mode = models.CharField(
        max_length=20,
        choices=DocumentBatchGenerationMode.choices,
        default=DocumentBatchGenerationMode.FULL_SET,
        verbose_name="Режим генерации",
    )
    letter_type = models.CharField(
        max_length=20,
        choices=DocumentBatchLetterType.choices,
        default=DocumentBatchLetterType.FOR_EXECUTION,
        verbose_name="Тип письма",
    )

    letter_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Номер письма",
        help_text="Номер сопроводительного письма.",
    )
    letter_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Дата письма",
        help_text="Дата составления сопроводительного письма.",
    )

    documentation_type = models.CharField(
        max_length=10,
        choices=DocumentBatchDocumentationType.choices,
        default=DocumentBatchDocumentationType.ID,
        verbose_name="Тип документации реестра",
        help_text="Определяет текст шапки реестра: ИД / РД / ИД+РД.",
    )
    project_scope = models.CharField(
        max_length=20,
        choices=DocumentBatchProjectScope.choices,
        default=DocumentBatchProjectScope.AUTO_BY_PERIOD,
        verbose_name="Охват проектов",
    )

    preview_snapshot_json = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Snapshot preview",
        help_text="Кэш последнего собранного web-preview комплекта.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_document_batches",
        verbose_name="Создал",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создан",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Обновлён",
    )

    class Meta:
        verbose_name = "Комплект сдачи ИД"
        verbose_name_plural = "Комплекты сдачи ИД"
        ordering = ("-created_at", "-id")

    def __str__(self):
        title_part = f" — {self.title}" if self.title else ""
        return f"Комплект ИД #{self.id}{title_part}"

    @staticmethod
    def _month_code_to_tuple(month_code):
        """
        month_code (строка вида MM.YYYY) -> (year, month)
        """
        month_str, year_str = month_code.split(".")
        return int(year_str), int(month_str)

    def clean(self):
        super().clean()

        if self.selection_mode == DocumentBatchSelectionMode.RANGE:
            if not self.month_from or not self.month_to:
                raise ValidationError(
                    {
                        "month_from": "Для режима 'За период' нужно заполнить обе границы периода.",
                        "month_to": "Для режима 'За период' нужно заполнить обе границы периода.",
                    }
                )

            if self._month_code_to_tuple(self.month_from) > self._month_code_to_tuple(self.month_to):
                raise ValidationError(
                    {
                        "month_from": "Начальный месяц не может быть позже конечного.",
                        "month_to": "Конечный месяц не может быть раньше начального.",
                    }
                )

        if self.selection_mode == DocumentBatchSelectionMode.ALL_TIME:
            if self.month_from or self.month_to:
                raise ValidationError(
                    {
                        "month_from": 'Для режима "За весь период" поля периода должны быть пустыми.',
                        "month_to": 'Для режима "За весь период" поля периода должны быть пустыми.',
                    }
                )
        needs_letter_fields = self.generation_mode in {
            DocumentBatchGenerationMode.LETTER_ONLY,
            DocumentBatchGenerationMode.FULL_SET,
        }

        if needs_letter_fields:
            errors = {}

            if not (self.letter_number or "").strip():
                errors["letter_number"] = "Для режима с письмом нужно заполнить номер письма."

            if not self.letter_date:
                errors["letter_date"] = "Для режима с письмом нужно заполнить дату письма."

            if errors:
                raise ValidationError(errors)


class DocumentBatchProject(models.Model):
    """
    Проект, входящий в комплект.
    Порядок здесь — порядок самих проектов внутри batch.
    """

    uuid = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name="UUID",
    )
    batch = models.ForeignKey(
        DocumentBatch,
        on_delete=models.CASCADE,
        related_name="batch_projects",
        verbose_name="Комплект",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name="document_batch_links",
        verbose_name="Проект",
    )
    order = models.PositiveIntegerField(
        verbose_name="Порядок проекта",
        help_text="Позиция проекта внутри комплекта.",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создан",
    )

    class Meta:
        verbose_name = "Проект комплекта сдачи ИД"
        verbose_name_plural = "Проекты комплектов сдачи ИД"
        ordering = ("batch", "order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "project"),
                name="uniq_document_batch_project_batch_project",
            ),
            models.UniqueConstraint(
                fields=("batch", "order"),
                name="uniq_document_batch_project_batch_order",
            ),
        ]

    def __str__(self):
        project_name = getattr(self.project, "full_code", None) or str(self.project)
        return f"{project_name} (комплект #{self.batch_id}, порядок {self.order})"


class DocumentBatchAct(models.Model):
    """
    Акт, входящий в комплект.

    ВАЖНО:
    - один и тот же Act может относиться к нескольким Project через Act.projects
    - поэтому уникальность должна быть на связке (batch, project, act)
    - order (порядок) — всегда внутри проекта
    """

    uuid = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name="UUID",
    )
    batch = models.ForeignKey(
        DocumentBatch,
        on_delete=models.CASCADE,
        related_name="batch_acts",
        verbose_name="Комплект",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name="document_batch_acts",
        verbose_name="Проект",
    )
    act = models.ForeignKey(
        Act,
        on_delete=models.PROTECT,
        related_name="document_batch_items",
        verbose_name="Акт",
    )
    order = models.PositiveIntegerField(
        verbose_name="Порядок внутри проекта",
        help_text="Порядок акта внутри реестра конкретного проекта.",
    )
    source = models.CharField(
        max_length=10,
        choices=DocumentBatchActSource.choices,
        default=DocumentBatchActSource.AUTO,
        verbose_name="Источник",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="added_document_batch_acts",
        verbose_name="Добавил",
    )
    added_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Добавлен",
    )

    class Meta:
        verbose_name = "Акт комплекта сдачи ИД"
        verbose_name_plural = "Акты комплектов сдачи ИД"
        ordering = ("batch", "project", "order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "project", "act"),
                name="uniq_document_batch_act_batch_project_act",
            ),
            models.UniqueConstraint(
                fields=("batch", "project", "order"),
                name="uniq_document_batch_act_batch_project_order",
            ),
        ]

    def __str__(self):
        return (
            f"Акт #{self.act_id} в комплекте #{self.batch_id} "
            f"(проект #{self.project_id}, порядок {self.order})"
        )

    def clean(self):
        super().clean()

        if self.batch_id and self.project_id:
            project_in_batch_exists = DocumentBatchProject.objects.filter(
                batch_id=self.batch_id,
                project_id=self.project_id,
            ).exists()

            if not project_in_batch_exists:
                raise ValidationError(
                    {
                        "project": "Нельзя добавить акт в проект, которого нет в составе данного комплекта.",
                    }
                )

        if self.act_id and self.project_id:
            act_has_project = self.act.projects.filter(id=self.project_id).exists()
            if not act_has_project:
                raise ValidationError(
                    {
                        "project": "Выбранный проект не привязан к этому акту.",
                        "act": "Указанный акт не относится к выбранному проекту.",
                    }
                )


class GeneratedDocument(models.Model):
    """
    Файл, относящийся к комплекту.

    Логика:
    - project != null: проектный документ (например, реестр)
    - project == null: общий документ batch (например, письмо)
    """

    PROJECT_REQUIRED_TYPES = {
        GeneratedDocumentType.REGISTRY_XLSX,
        GeneratedDocumentType.REGISTRY_PREVIEW_PDF,
    }

    PROJECT_FORBIDDEN_TYPES = {
        GeneratedDocumentType.LETTER_DOCX,
        GeneratedDocumentType.LETTER_PREVIEW_PDF,
        GeneratedDocumentType.STAMPED_LETTER_PDF,
        GeneratedDocumentType.STAMPED_LETTER_IMAGE,
    }

    uuid = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name="UUID",
    )
    batch = models.ForeignKey(
        DocumentBatch,
        on_delete=models.CASCADE,
        related_name="generated_documents",
        verbose_name="Комплект",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="generated_documents",
        null=True,
        blank=True,
        verbose_name="Проект",
    )

    document_type = models.CharField(
        max_length=30,
        choices=GeneratedDocumentType.choices,
        verbose_name="Тип документа",
    )
    source_kind = models.CharField(
        max_length=10,
        choices=GeneratedDocumentSourceKind.choices,
        verbose_name="Источник файла",
    )

    file = models.FileField(
        upload_to=batch_document_upload_to,
        verbose_name="Файл",
    )
    original_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Исходное имя файла",
    )

    pages_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Количество страниц",
        help_text="Количество страниц, которое будет распечатано для данного документа.",
    )

    is_actual = models.BooleanField(
        default=True,
        verbose_name="Актуальный",
        help_text="Показывает, соответствует ли файл текущим зависимостям batch.",
    )
    dependency_signature = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="Подпись зависимостей",
        help_text="Хэш/подпись входных данных, по которым был собран документ.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_generated_documents",
        verbose_name="Создал",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создан",
    )
    generated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Дата генерации",
    )
    uploaded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Дата загрузки",
    )

    class Meta:
        verbose_name = "Файл комплекта сдачи ИД"
        verbose_name_plural = "Файлы комплектов сдачи ИД"
        ordering = ("batch", "project", "document_type", "-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "project", "document_type"),
                condition=Q(project__isnull=False),
                name="uniq_generated_document_batch_project_type",
            ),
            models.UniqueConstraint(
                fields=("batch", "document_type"),
                condition=Q(project__isnull=True),
                name="uniq_generated_document_batch_common_type",
            ),
        ]

    def __str__(self):
        if self.project_id:
            return f"{self.get_document_type_display()} / проект #{self.project_id} / batch #{self.batch_id}"
        return f"{self.get_document_type_display()} / batch #{self.batch_id}"

    def clean(self):
        super().clean()

        if self.document_type in self.PROJECT_REQUIRED_TYPES and not self.project_id:
            raise ValidationError(
                {
                    "project": "Для реестровых документов нужно обязательно указать проект.",
                }
            )

        if self.document_type in self.PROJECT_FORBIDDEN_TYPES and self.project_id:
            raise ValidationError(
                {
                    "project": "Для письма и stamped-letter проект указывать нельзя.",
                }
            )

        if self.project_id:
            project_in_batch_exists = DocumentBatchProject.objects.filter(
                batch_id=self.batch_id,
                project_id=self.project_id,
            ).exists()

            if not project_in_batch_exists:
                raise ValidationError(
                    {
                        "project": "Указанный проект не входит в состав данного комплекта.",
                    }
                )

        self._validate_file_extension()

    def save(self, *args, **kwargs):
        if self.file and not self.original_name:
            self.original_name = os.path.basename(self.file.name)
        super().save(*args, **kwargs)

    def _validate_file_extension(self):
        if not self.file:
            return

        extension = Path(self.file.name).suffix.lower()

        allowed_extensions_map = {
            GeneratedDocumentType.REGISTRY_XLSX: {".xlsx"},
            GeneratedDocumentType.REGISTRY_PREVIEW_PDF: {".pdf"},
            GeneratedDocumentType.LETTER_DOCX: {".docx"},
            GeneratedDocumentType.LETTER_PREVIEW_PDF: {".pdf"},
            GeneratedDocumentType.STAMPED_LETTER_PDF: {".pdf"},
            GeneratedDocumentType.STAMPED_LETTER_IMAGE: {".jpg", ".jpeg"},
        }

        allowed_extensions = allowed_extensions_map.get(self.document_type, set())
        if allowed_extensions and extension not in allowed_extensions:
            allowed_text = ", ".join(sorted(allowed_extensions))
            raise ValidationError(
                {
                    "file": f"Для типа '{self.get_document_type_display()}' допустимы только файлы: {allowed_text}",
                }
            )
