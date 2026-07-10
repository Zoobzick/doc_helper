# acts_app/models.py
from __future__ import annotations

import uuid
from datetime import date

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from directive_app.models import ActRole  # единый источник ролей
from directive_app.models import Authorization  # FK для ручного выбора (override)


class ActStatus(models.TextChoices):
    DRAFT = "DRAFT", "Черновик"
    FINAL = "FINAL", "Финальный"


class AttachmentType(models.TextChoices):
    EXEC_SCHEME = "EXEC_SCHEME", "Исполнительная схема"
    MATERIALS_REGISTRY = "MATERIALS_REGISTRY", "Реестр материалов"
    DOCS_REGISTRY = "DOCS_REGISTRY", "Реестр документов соответствия"
    APPROVALS_REGISTRY = "APPROVALS_REGISTRY", "Реестр согласований (П-8)"
    CONCRETE_SAMPLES_ACT = "CONCRETE_SAMPLES_ACT", "Акт контрольных образцов бетона"
    TEST_PROTOCOL = "TEST_PROTOCOL", "Протокол испытаний"
    OTHER_QUALITY_DOC = "OTHER_QUALITY_DOC", "Документ качества (прочее)"


class Act(models.Model):
    uuid = models.UUIDField("UUID", default=uuid.uuid4, editable=False, unique=True, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_acts",
        verbose_name="Кто делал",
    )

    projects = models.ManyToManyField(
        "projects_app.Project",
        related_name="acts",
        verbose_name="Шифры проектов",
        blank=True,
    )

    number = models.CharField("№ Акта", max_length=64)
    act_date = models.DateField("Дата акта")

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

    approvals = models.ManyToManyField(
        "approvals_app.Approval",
        related_name="acts",
        verbose_name="Согласования (Доп. сведения)",
        blank=True,
    )

    copies_count = models.PositiveSmallIntegerField(
        "Акт составлен в (экземплярах)",
        default=3,
        validators=[MinValueValidator(1)],
    )

    status = models.CharField(
        "Статус",
        max_length=16,
        choices=ActStatus.choices,
        default=ActStatus.DRAFT,
        db_index=True,
    )

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
        d: date = self.work_end_date or self.act_date
        self.act_year = int(d.year)
        self.act_month = int(d.month)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Акт №{self.number} от {self.act_date:%d.%m.%Y}"


class ActParty(models.Model):
    """
    Строка таблицы "Подписанты" в акте (то, что выбирает пользователь):
    [✓] Роль | Организация | Приказ | Персона

    - Пользователь задаёт: role + organization + is_enabled
    - Система резолвит Authorization на дату act.act_date
    - При конфликте (несколько Authorization) пользователь обязан выбрать chosen_authorization
    """

    uuid = models.UUIDField("UUID", default=uuid.uuid4, editable=False, unique=True, db_index=True)

    act = models.ForeignKey(
        "acts_app.Act",
        on_delete=models.CASCADE,
        related_name="parties",
        verbose_name="Акт",
    )

    role = models.CharField(
        "Роль",
        max_length=32,
        choices=ActRole.choices,
        db_index=True,
    )

    organization = models.ForeignKey(
        "orgs_app.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="act_parties",
        verbose_name="Организация",
        help_text="Для выключенной роли можно оставить пустым.",
    )

    is_enabled = models.BooleanField(
        "Учитывать в акте",
        default=True,
        db_index=True,
        help_text="Если выключено — роль не печатается и не валидируется при финализации.",
    )

    position = models.PositiveIntegerField(
        "Позиция",
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Порядок строк (важно для нескольких OTHER_REP).",
    )

    chosen_authorization = models.ForeignKey(
        Authorization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chosen_in_act_parties",
        verbose_name="Выбранное полномочие (ручной выбор при конфликте)",
        help_text="Заполняется ТОЛЬКО когда на дату акта найдено несколько полномочий.",
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Сторона/роль акта"
        verbose_name_plural = "Стороны/роли акта"
        ordering = ("position", "id")
        indexes = [
            models.Index(fields=["act", "role"], name="actparty_act_role_idx"),
            models.Index(fields=["act", "is_enabled"], name="actparty_act_enabled_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["act", "role"],
                condition=~Q(role=ActRole.OTHER_REP),
                name="uniq_actparty_role_except_other",
            ),
            models.UniqueConstraint(
                fields=["act", "position"],
                name="uniq_actparty_position",
            ),
        ]

    def __str__(self) -> str:
        org = self.organization.short_name if self.organization_id else "—"
        return f"{self.get_role_display()} → {org}"


class ActSignatorySnapshot(models.Model):
    """
    Снапшот подписанта на момент FINAL.
    Печать должна опираться на эти данные, чтобы история не "плыла".
    """

    uuid = models.UUIDField("UUID", default=uuid.uuid4, editable=False, unique=True, db_index=True)

    act = models.ForeignKey(
        "acts_app.Act",
        on_delete=models.CASCADE,
        related_name="signatory_snapshots",
        verbose_name="Акт",
    )

    role = models.CharField(
        "Роль",
        max_length=32,
        choices=ActRole.choices,
        db_index=True,
    )

    position = models.PositiveIntegerField(
        "Позиция",
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Порядок строк в печати (важно для нескольких OTHER_REP).",
    )

    source_authorization_uuid = models.UUIDField("UUID полномочия (источник)", null=True, blank=True, db_index=True)
    source_directive_uuid = models.UUIDField(
        "UUID документа-основания (источник)",
        null=True,
        blank=True,
        db_index=True,
    )

    organization_name = models.CharField("Организация (как печатаем)", max_length=512)
    person_fio = models.CharField("Подписант (ФИО)", max_length=255)
    position_text = models.CharField("Должность", max_length=255, blank=True, default="")
    directive_repr = models.CharField("Основание (как печатаем)", max_length=512)

    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Снапшот подписанта"
        verbose_name_plural = "Снапшоты подписантов"
        ordering = ("position", "id")
        indexes = [
            models.Index(fields=["act", "role"], name="actsnap_act_role_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_role_display()}: {self.person_fio}"


class ActMaterialItem(models.Model):
    """
    Материалы:
    - либо ссылка на Passport из БД
    - либо ручной ввод (material_name + document_name + document_date)

    ВАЖНО:
    manual_doc_date_text — "как печатаем" (поддерживает мм.гггг и любые диапазоны),
    manual_doc_date — нормальная дата (для случаев dd.mm.yyyy).
    """
    act = models.ForeignKey("acts_app.Act", on_delete=models.CASCADE, related_name="materials", verbose_name="Акт")

    position = models.PositiveIntegerField("Позиция", validators=[MinValueValidator(1)], default=1)

    passport = models.ForeignKey(
        "passports_app.Passport",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="act_material_items",
        verbose_name="Паспорт (из БД)",
    )

    manual_name = models.CharField("Наименование материала (ручной ввод)", max_length=255, blank=True, default="")
    manual_doc_name = models.CharField("Наименование документа (ручной ввод)", max_length=255, blank=True, default="")

    manual_doc_no = models.CharField("Наименование/№ документа (ручной ввод)", max_length=255, blank=True, default="")

    manual_doc_date = models.DateField("Дата документа (ручной ввод)", null=True, blank=True)

    # ✅ НОВОЕ: поддержка "02.2026", "26-27.01.2026" и т.п.
    manual_doc_date_text = models.CharField(
        "Дата документа (как печатаем)",
        max_length=32,
        blank=True,
        default="",
        help_text="Можно вводить: 02.2026 или 26-27.01.2026. Если заполнено — оно важнее даты dd.mm.yyyy.",
    )

    concrete_volume_m3 = models.PositiveIntegerField(
        "Объём, м3",
        null=True,
        blank=True,
        help_text="Заполняется для бетонной смеси/раствора (ручной ввод).",
    )

    sheets_count = models.PositiveIntegerField("Листов", validators=[MinValueValidator(1)])

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


def aook_file_upload_to(instance: "Aook", filename: str) -> str:
    return f"aook/{instance.uuid}/{filename}"


class ActAttachment(models.Model):
    uuid = models.UUIDField("UUID", default=uuid.uuid4, editable=False, unique=True, db_index=True)

    act = models.ForeignKey("acts_app.Act", on_delete=models.CASCADE, related_name="attachments", verbose_name="Акт")

    type = models.CharField("Тип документа", max_length=32, choices=AttachmentType.choices, db_index=True)

    title = models.CharField("Наименование", max_length=255, blank=True, default="")
    doc_no = models.CharField("№", max_length=255, blank=True, default="")
    doc_date = models.DateField("Дата (с)", null=True, blank=True)  # (doc_date) начало периода
    doc_date_to = models.DateField("Дата (по)", null=True,
                                   blank=True)  # (doc_date_to) конец периода (может быть пустым)

    sheets_count = models.PositiveIntegerField("Листов", validators=[MinValueValidator(1)])

    file = models.FileField("Файл", upload_to=act_attachment_upload_to, blank=True, null=True)

    class OriginalState(models.IntegerChoices):
        COPY = 0, "Копия"
        ORIGINAL = 1, "Оригинал"
        IGNORE = 2, "Не отслеживать"

    original_state = models.PositiveSmallIntegerField(
        choices=OriginalState.choices,
        default=OriginalState.IGNORE,
        db_index=True,
    )

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
            if self.doc_date_to and self.doc_date_to != self.doc_date:
                parts.append(f"от {self.doc_date:%d.%m.%Y} по {self.doc_date_to:%d.%m.%Y}")
            else:
                parts.append(f"от {self.doc_date:%d.%m.%Y}")

        return " ".join(parts)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)


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


class ActApprovalItem(models.Model):
    """
    Согласования (Доп. сведения) в акте, построчно.

    Зачем:
    - хранить "как отображать в приложении" (label_override)
    - хранить sheets_count по каждой позиции
    - хранить порядок
    """
    uuid = models.UUIDField("UUID", default=uuid.uuid4, editable=False, unique=True, db_index=True)

    act = models.ForeignKey(
        "acts_app.Act",
        on_delete=models.CASCADE,
        related_name="approval_items",
        verbose_name="Акт",
    )

    approval = models.ForeignKey(
        "approvals_app.Approval",
        on_delete=models.PROTECT,
        related_name="act_items",
        verbose_name="Согласование",
    )

    position = models.PositiveIntegerField(
        "Позиция",
        validators=[MinValueValidator(1)],
        default=1,
    )

    label_override = models.CharField(
        "Текст для приложения (как печатаем)",
        max_length=512,
        blank=True,
        default="",
        help_text="Если пусто — используем авто-лейбл из Approval.",
    )

    sheets_count = models.PositiveIntegerField(
        "Листов",
        validators=[MinValueValidator(1)],
        default=1,
    )

    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Согласование в акте"
        verbose_name_plural = "Согласования в акте"
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(fields=["act", "approval"], name="uniq_act_approval_item"),
            models.UniqueConstraint(fields=["act", "position"], name="uniq_act_approval_item_pos"),
        ]
        indexes = [
            models.Index(fields=["act", "position"], name="actappr_act_pos_idx"),
            models.Index(fields=["approval"], name="actappr_approval_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.act} → {self.approval}"


class Aook(models.Model):
    uuid = models.UUIDField("UUID", default=uuid.uuid4, editable=False, unique=True, db_index=True)

    project = models.ForeignKey(
        "projects_app.Project",
        on_delete=models.PROTECT,
        related_name="aooks",
        verbose_name="Шифр проекта",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_aooks",
        verbose_name="Кто делал",
    )

    number = models.CharField("№ АООК", max_length=64)
    act_date = models.DateField("Дата АООК")
    work_name = models.CharField("Наименование работ", max_length=512)
    work_start_date = models.DateField("Дата начала работ", null=True, blank=True)
    work_end_date = models.DateField("Дата окончания работ", null=True, blank=True)
    work_norms_text = models.TextField("Работы выполнены в соответствии с", blank=True, default="")
    copies_count = models.PositiveSmallIntegerField(
        "Акт составлен в (экземплярах)",
        default=3,
        validators=[MinValueValidator(1)],
    )

    aosr_registry_number = models.CharField("№ реестра АОСР", max_length=64, blank=True, default="")
    protocols_registry_number = models.CharField("№ реестра протоколов", max_length=64, blank=True, default="")

    xlsx_file = models.FileField("XLSX", upload_to=aook_file_upload_to, blank=True, null=True)
    pdf_file = models.FileField("PDF", upload_to=aook_file_upload_to, blank=True, null=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "АООК"
        verbose_name_plural = "АООК"
        ordering = ("-act_date", "number")
        indexes = [
            models.Index(fields=["project", "act_date"], name="aook_project_date_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.aosr_registry_number:
            self.aosr_registry_number = f"П-3.{self.number}".strip()
        if not self.protocols_registry_number:
            self.protocols_registry_number = f"П-6.{self.number}".strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"АООК №{self.number} от {self.act_date:%d.%m.%Y}"


class AookSourceAct(models.Model):
    aook = models.ForeignKey("acts_app.Aook", on_delete=models.CASCADE, related_name="source_act_items")
    act = models.ForeignKey("acts_app.Act", on_delete=models.PROTECT, related_name="aook_source_items")
    position = models.PositiveIntegerField("Позиция", validators=[MinValueValidator(1)], default=1)

    class Meta:
        verbose_name = "АОСР в АООК"
        verbose_name_plural = "АОСР в АООК"
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(fields=["aook", "act"], name="uniq_aook_source_act"),
            models.UniqueConstraint(fields=["aook", "position"], name="uniq_aook_source_pos"),
        ]
        indexes = [
            models.Index(fields=["aook", "position"], name="aooksrc_aook_pos_idx"),
            models.Index(fields=["act"], name="aooksrc_act_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.position}. {self.act}"


class AookProtocolItem(models.Model):
    aook = models.ForeignKey("acts_app.Aook", on_delete=models.CASCADE, related_name="protocol_items")
    position = models.PositiveIntegerField("Позиция", validators=[MinValueValidator(1)], default=1)
    document_name = models.CharField("Наименование документа", max_length=512)
    document_number = models.CharField("№", max_length=255, blank=True, default="")
    document_date = models.DateField("Дата", null=True, blank=True)
    organization_name = models.CharField("Организация", max_length=255, blank=True, default="")
    sheets_count = models.PositiveIntegerField("Листов", validators=[MinValueValidator(1)], default=1)

    class Meta:
        verbose_name = "Протокол АООК"
        verbose_name_plural = "Протоколы АООК"
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(fields=["aook", "position"], name="uniq_aook_protocol_pos"),
        ]
        indexes = [
            models.Index(fields=["aook", "position"], name="aookprot_aook_pos_idx"),
        ]

    def __str__(self) -> str:
        parts = [self.document_name]
        if self.document_number:
            parts.append(f"№{self.document_number}")
        if self.document_date:
            parts.append(f"от {self.document_date:%d.%m.%Y}")
        return " ".join(parts)
