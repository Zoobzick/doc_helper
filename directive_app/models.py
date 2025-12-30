import os
import re

from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import MinLengthValidator
from django.utils import timezone
from .storage import DirectivesStorage

directives_storage = DirectivesStorage()


class SignerRole(models.TextChoices):
    DSM_SK = "DSM_SK", "ДСМ СК"
    MIP_RS = "MIP_RS", "МИП РС"
    MIP_SK = "MIP_SK", "МИП СК"
    AN = "AN", "АН"
    SMU = "SMU", "СМУ"
    MMS_SK = "MMS_SK", "ММС СК"
    OTHER = "OTHER", "ИНЫЕ"


def _sanitize_component(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = value.rstrip(" .")
    return value or "Без_имени"


def directive_upload_to(instance, filename: str) -> str:
    ext = os.path.splitext(filename)[1] or ".pdf"
    date = instance.date or timezone.now().date()

    org = _sanitize_component(instance.organization)
    position = _sanitize_component(instance.employee_position)
    num = _sanitize_component(instance.number)
    fio = _sanitize_component(instance.employee_full_name)

    new_filename = f"{num}_от_{date:%d.%m.%Y}_{fio}{ext}"
    return os.path.join(org, position, new_filename)


class Directive(models.Model):
    number = models.CharField(
        verbose_name='Номер приказа',
        max_length=50,
        unique=True,
        validators=[MinLengthValidator(1)],
        help_text='Пример: 125-к, 2024-09-01, ПР-125'
    )

    date = models.DateField(
        verbose_name='Дата приказа',
        default=timezone.now,
        help_text='Дата подписания приказа'
    )

    effective_date = models.DateField(
        verbose_name='Дата вступления в силу',
        null=True,
        blank=True,
        help_text='Если отличается от даты подписания'
    )

    def clean(self):
        if self.effective_date and self.date and self.effective_date < self.date:
            raise ValidationError({
                "effective_date": "Дата вступления в силу не может быть раньше даты подписания."
            })

    @property
    def effective_date_resolved(self):
        return self.effective_date or self.date

    employee_full_name = models.CharField(
        verbose_name='Ф.И.О. сотрудника',
        max_length=200
    )

    employee_position = models.CharField(
        verbose_name='Должность сотрудника',
        max_length=200
    )

    organization = models.CharField(
        verbose_name='Организация',
        max_length=300,
        default='АО "Мосинжпроект"'
    )

    pdf_file = models.FileField(
        verbose_name='Файл приказа',
        storage=directives_storage,
        upload_to=directive_upload_to,
        blank=True,
        null=True,
        max_length=500
    )

    original_filename = models.CharField(
        verbose_name='Оригинальное имя файла',
        max_length=255,
        blank=True
    )

    file_size = models.IntegerField(
        verbose_name='Размер файла (КБ)',
        default=0
    )

    uploaded_at = models.DateTimeField(
        verbose_name='Время загрузки файла',
        auto_now_add=True,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        verbose_name='Дата создания записи',
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        verbose_name='Дата обновления',
        auto_now=True
    )

    is_active = models.BooleanField(
        verbose_name='Действующий',
        default=True
    )

    signer_role = models.CharField(
        verbose_name="Роль подписанта",
        max_length=20,
        choices=SignerRole.choices,
        default=SignerRole.OTHER,
        db_index=True,
    )

    class Meta:
        verbose_name = 'Приказ'
        verbose_name_plural = 'Приказы'
        ordering = ['-date', '-number']
        indexes = [
            models.Index(fields=['number']),
            models.Index(fields=['date']),
            models.Index(fields=['employee_full_name']),
        ]

        # ✅ Кастомные права НЕ должны называться add_/delete_/change_/view_
        # потому что эти 4 Django создаёт автоматически для модели.
        permissions = [
            ("view_directives_page", "Может открывать страницу списка приказов"),
            ("create_directive_page", "Может открывать страницу добавления приказа"),
            ("delete_directive_page", "Может удалять приказ (через интерфейс сайта)"),
            ("open_directive_pdf", "Может открывать PDF приказа"),
        ]

    def save(self, *args, **kwargs):
        if self.pdf_file and not self.original_filename:
            self.original_filename = os.path.basename(self.pdf_file.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Приказ №{self.number} от {self.date} - {self.employee_full_name}'


class SignerRoleConfig(models.Model):
    role = models.CharField(
        verbose_name="Роль",
        max_length=20,
        choices=SignerRole.choices,
        unique=True,
        db_index=True,
    )

    source_paragraph = models.PositiveSmallIntegerField(
        verbose_name="Параграф в акте",
        null=True,
        blank=True,
        help_text="Если не задан — проверка этой роли не выполняется.",
    )

    is_enabled = models.BooleanField(
        verbose_name="Включено",
        default=True,
        help_text="Можно временно отключать проверку для этой роли.",
    )

    updated_at = models.DateTimeField(
        verbose_name="Обновлено",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Настройка роли подписанта"
        verbose_name_plural = "Настройки ролей подписанта"
        ordering = ("role",)

    def __str__(self):
        par = self.source_paragraph if self.source_paragraph is not None else "—"
        status = "ON" if self.is_enabled else "OFF"
        return f"{self.get_role_display()} | par={par} | {status}"
