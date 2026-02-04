from __future__ import annotations

import uuid
from django.core.exceptions import ValidationError
from django.db import models
from orgs_app.models import Organization, Person


class ActRole(models.TextChoices):
    """
    Роли подписантов для АОСР.
    """
    TECH_CUSTOMER_CONTROL = "TECH_CUSTOMER_CONTROL", "Стройконтроль застройщика/техзаказчика"
    BUILDER_REP = "BUILDER_REP", "Представитель лица, осуществляющего строительство"
    BUILDER_CONTROL = "BUILDER_CONTROL", "Стройконтроль лица, осуществляющего строительство"
    DESIGN_REP = "DESIGN_REP", "Представитель проектировщика / авторский надзор"
    CONTRACTOR_REP = "CONTRACTOR_REP", "Лицо, выполнившее работы"
    OTHER_REP = "OTHER_REP", "Иные лица"


class DirectiveType(models.TextChoices):
    ORDER = "ORDER", "Приказ"
    POA = "POA", "Доверенность"
    OTHER = "OTHER", "Иной документ"


def directive_upload_to(instance: "Directive", filename: str) -> str:
    return f"directives/{instance.uuid}/{filename}"


class Directive(models.Model):
    """
    Документ-основание полномочий (приказ/доверенность/иное).
    Важно: Directive НЕ хранит "кому выдано" — это хранит Authorization.
    """

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name="Публичный идентификатор",
    )

    doc_type = models.CharField(
        max_length=16,
        choices=DirectiveType.choices,
        default=DirectiveType.ORDER,
        verbose_name="Тип документа",
    )

    number = models.CharField(
        max_length=64,
        verbose_name="Номер",
    )

    date = models.DateField(
        verbose_name="Дата документа",
    )

    issuer_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="issued_directives",
        verbose_name="Кем выдан (организация)",
    )

    pdf_file = models.FileField(
        upload_to=directive_upload_to,
        blank=True,
        null=True,
        verbose_name="Файл (PDF)",
    )

    note = models.CharField(
        max_length=512,
        blank=True,
        default="",
        verbose_name="Примечание",
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Документ активен",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создано",
    )

    class Meta:
        verbose_name = "Документ-основание (приказ/доверенность)"
        verbose_name_plural = "Документы-основания"
        ordering = ["-date", "number"]
        indexes = [
            models.Index(fields=["date", "number"]),
            models.Index(fields=["issuer_organization"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_doc_type_display()} №{self.number} от {self.date:%d.%m.%Y}"


class Authorization(models.Model):
    """
    Полномочие подписанта.
    Связь: Person ↔ Organization ↔ Directive (+ роль + период).
    """

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name="Публичный идентификатор",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="authorizations",
        verbose_name="Организация (от чьего имени подписывает)",
    )

    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="authorizations",
        verbose_name="Человек (кто подписывает)",
    )

    role = models.CharField(
        max_length=32,
        choices=ActRole.choices,
        verbose_name="Роль в акте",
    )

    directive = models.ForeignKey(
        Directive,
        on_delete=models.PROTECT,
        related_name="authorizations",
        verbose_name="Основание (документ)",
    )

    position_text = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Должность (для подстановки в акт)",
    )

    valid_from = models.DateField(
        verbose_name="Действует с",
    )

    valid_to = models.DateField(
        blank=True,
        null=True,
        verbose_name="Действует по (если бессрочно — пусто)",
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Полномочие активно",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создано",
    )

    class Meta:
        verbose_name = "Полномочие подписанта"
        verbose_name_plural = "Полномочия подписантов"
        ordering = ["organization", "role", "person"]
        indexes = [
            models.Index(fields=["organization", "role", "valid_from", "valid_to"]),
            models.Index(fields=["person", "role"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "person", "role", "directive", "valid_from"],
                name="uniq_auth_org_person_role_directive_from",
            )
        ]

    def clean(self):
        # (valid_to) не может быть раньше (valid_from)
        if self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError({"valid_to": "Дата окончания не может быть раньше даты начала."})

    def __str__(self) -> str:
        return f"{self.organization.short_name}: {self.person.short_name} ({self.get_role_display()})"
