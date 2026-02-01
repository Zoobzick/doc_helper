# acts_app/models.py
from __future__ import annotations

import uuid
from datetime import date

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ActStatus(models.TextChoices):
    DRAFT = "DRAFT", "Черновик"
    FINAL = "FINAL", "Финальный"


class AttachmentType(models.TextChoices):
    EXEC_SCHEME = "EXEC_SCHEME", "Исполнительная схема"
    MATERIALS_REGISTRY = "MATERIALS_REGISTRY", "Реестр материалов"
    CONCRETE_SAMPLES_ACT = "CONCRETE_SAMPLES_ACT", "Акт контрольных образцов бетона"
    TEST_PROTOCOL = "TEST_PROTOCOL", "Протокол испытаний"
    OTHER_QUALITY_DOC = "OTHER_QUALITY_DOC", "Документ качества (прочее)"


class Act(models.Model):
    uuid = models.UUIDField("UUID", default=uuid.uuid4, editable=False, unique=True, db_index=True)

    projects = models.ManyToManyField(
        "projects_app.Project",
        related_name="acts",
        verbose_name="Шифры проектов",
        blank=True,
    )

    number = models.CharField("№ Акта", max_length=64)
    act_date = models.DateField("Дата акта", default=timezone.localdate)

    work_name = models.CharField("Наименование работ", max_length=512)

    work_start_date = models.DateField("Дата начала работ", null=True, blank=True)
    work_end_date = models.DateField("Дата окончания работ", null=True, blank=True)

    work_norms_text = models.TextField("Работы выполнены в соответствии с", blank=True, default="")
    allow_next_works_text = models.TextField("Разрешается производство последующих работ", blank=True, default="")
    extra_info_text = models.TextField(
        "Доп. сведения",
        blank=True,
        default="",
        help_text="Позже подключим approvals_app + свободный текст.",
    )

    copies_count = models.PositiveSmallIntegerField(
        "Акт составлен в (экземплярах)",
        default=3,
        validators=[MinValueValidator(1)],
    )

    status = models.CharField("Статус", max_length=16, choices=ActStatus.choices, default=ActStatus.DRAFT, db_index=True)

    act_year = models.PositiveSmallIntegerField("Год акта", editable=False, db_index=True)
    act_month = models.PositiveSmallIntegerField("Месяц акта", editable=False, db_index=True)

    sheets_total = models.PositiveIntegerField("Всего листов (кеш)", default=0)

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Акт скрытых работ"
        verbose_name_plural = "Акты скрытых работ"
        ordering = ("-act_date", "number")
        indexes = [
            models.Index(fields=["act_year", "act_month"], name="act_ym_idx"),
            models.Index(fields=["act_date"], name="act_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(check=Q(act_month__gte=1) & Q(act_month__lte=12), name="act_month_1_12"),
        ]

    def save(self, *args, **kwargs):
        d: date = self.act_date or timezone.localdate()
        self.act_year = int(d.year)
        self.act_month = int(d.month)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Акт №{self.number} от {self.act_date:%d.%m.%Y}"


class ActMaterialItem(models.Model):
    """
    Материалы:
    - либо ссылка на Passport из БД
    - либо ручной ввод (material_name + document_name + document_date)
    """
    act = models.ForeignKey("acts_app.Act", on_delete=models.CASCADE, related_name="materials", verbose_name="Акт")

    # порядок для печати/стабильности, но в UI мы не показываем — проставляем автоматически
    position = models.PositiveIntegerField("Позиция", validators=[MinValueValidator(1)], default=1)

    passport = models.ForeignKey(
        "passports_app.Passport",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="act_material_items",
        verbose_name="Паспорт (из БД)",
    )

    # ручной ввод
    manual_name = models.CharField("Наименование материала (ручной ввод)", max_length=255, blank=True, default="")
    manual_doc_no = models.CharField("Наименование/№ документа (ручной ввод)", max_length=255, blank=True, default="")
    manual_doc_date = models.DateField("Дата документа (ручной ввод)", null=True, blank=True)

    sheets_count = models.PositiveIntegerField("Листов", validators=[MinValueValidator(1)])
    note = models.CharField("Примечание", max_length=255, blank=True, default="")

    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Материал акта"
        verbose_name_plural = "Материалы акта"
        ordering = ("position", "created_at")
        constraints = [
            models.UniqueConstraint(fields=["act", "position"], name="uniq_act_material_pos"),
            models.CheckConstraint(
                check=Q(passport__isnull=False) | (Q(manual_name__isnull=False) & ~Q(manual_name="")),
                name="act_material_passport_or_manual",
            ),
        ]
        indexes = [
            models.Index(fields=["passport"], name="actmat_passport_idx"),
            models.Index(fields=["act", "passport"], name="actmat_act_passport_idx"),
        ]

    def __str__(self) -> str:
        if self.passport_id:
            return f"Паспорт {self.passport}"
        return self.manual_name or "Материал (ручной)"


def act_attachment_upload_to(instance: "ActAttachment", filename: str) -> str:
    return f"acts/{instance.act.uuid}/attachments/{instance.uuid}/{filename}"


class ActAttachment(models.Model):
    uuid = models.UUIDField("UUID", default=uuid.uuid4, editable=False, unique=True, db_index=True)

    act = models.ForeignKey("acts_app.Act", on_delete=models.CASCADE, related_name="attachments", verbose_name="Акт")

    # тип оставляем (для AppendixBuilder), но пользователю не показываем
    type = models.CharField("Тип документа", max_length=32, choices=AttachmentType.choices, db_index=True)

    title = models.CharField("Наименование", max_length=255, blank=True, default="")
    doc_no = models.CharField("№", max_length=255, blank=True, default="")
    doc_date = models.DateField("Дата", null=True, blank=True)

    sheets_count = models.PositiveIntegerField("Листов", validators=[MinValueValidator(1)])

    file = models.FileField("Файл", upload_to=act_attachment_upload_to, blank=True, null=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Документ акта"
        verbose_name_plural = "Документы акта"
        ordering = ("created_at",)
        indexes = [models.Index(fields=["act", "type"], name="attach_act_type_idx")]

    def __str__(self) -> str:
        parts = [self.title or "Документ"]
        if self.doc_no:
            parts.append(f"№{self.doc_no}")
        if self.doc_date:
            parts.append(f"от {self.doc_date:%d.%m.%Y}")
        return " ".join(parts)


class ActAppendixLine(models.Model):
    act = models.ForeignKey("acts_app.Act", on_delete=models.CASCADE, related_name="appendix_lines", verbose_name="Акт")

    position = models.PositiveIntegerField("Позиция", validators=[MinValueValidator(1)])

    source_attachment = models.ForeignKey(
        "acts_app.ActAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appendix_lines",
        verbose_name="Источник (документ)",
    )

    label = models.CharField("Строка приложения", max_length=512)
    sheets_count = models.PositiveIntegerField("Листов", validators=[MinValueValidator(1)])
    is_label_overridden = models.BooleanField("Label изменён вручную", default=False)

    class Meta:
        verbose_name = "Строка приложений"
        verbose_name_plural = "Строки приложений"
        ordering = ("position",)
        constraints = [models.UniqueConstraint(fields=["act", "position"], name="uniq_act_appendix_pos")]
        indexes = [models.Index(fields=["act", "position"], name="appendix_act_pos_idx")]

    def __str__(self) -> str:
        return f"{self.position}. {self.label} ({self.sheets_count} л.)"
