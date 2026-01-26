from __future__ import annotations

import uuid
from django.db import models


class Organization(models.Model):
    """
    Организация-участник (застройщик, подрядчик, проектировщик и т.д.)
    """

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name="Публичный идентификатор",
    )

    # (full_name) полное наименование
    full_name = models.CharField(
        max_length=512,
        verbose_name="Полное наименование",
    )
    # (short_name) сокращённое наименование
    short_name = models.CharField(
        max_length=255,
        verbose_name="Сокращённое наименование",
    )

    # (ogrn) ОГРН организации
    ogrn = models.CharField(
        max_length=15,
        unique=True,
        verbose_name="ОГРН",
    )
    # (inn) ИНН организации (не unique из-за кейсов с обособками и т.п.)
    inn = models.CharField(
        max_length=12,
        verbose_name="ИНН",
    )

    # (address) адрес организации
    address = models.TextField(
        verbose_name="Юридический адрес",
    )

    # (tel_fax) телефон/факс одной строкой (можно позже нормализовать)
    tel_fax = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Телефон / факс",
    )

    # (sro) наименование СРО (опционально)
    sro = models.CharField(
        max_length=512,
        blank=True,
        verbose_name="Наименование СРО",
    )
    # (sro_ogrn) ОГРН СРО (опционально)
    sro_ogrn = models.CharField(
        max_length=15,
        blank=True,
        verbose_name="ОГРН СРО",
    )
    # (sro_inn) ИНН СРО (опционально)
    sro_inn = models.CharField(
        max_length=12,
        blank=True,
        verbose_name="ИНН СРО",
    )

    # (is_active) активна ли организация для выбора в новых документах
    is_active = models.BooleanField(
        default=True,
        verbose_name="Организация активна",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создано",
    )

    class Meta:
        verbose_name = "Организация"
        verbose_name_plural = "Организации"
        ordering = ["short_name"]
        indexes = [
            models.Index(fields=["short_name"]),
            models.Index(fields=["inn"]),
        ]

    def __str__(self) -> str:
        return self.short_name


class Person(models.Model):
    """
    Человек (подписант). Один человек может фигурировать в разных организациях/ролях.
    """

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name="Публичный идентификатор",
    )

    # (last_name) фамилия
    last_name = models.CharField(
        max_length=64,
        verbose_name="Фамилия",
    )
    # (first_name) имя
    first_name = models.CharField(
        max_length=64,
        verbose_name="Имя",
    )
    # (middle_name) отчество (может отсутствовать)
    middle_name = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Отчество",
    )

    # (is_active) активен ли человек для выбора в новых документах
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активен",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создано",
    )

    class Meta:
        verbose_name = "Человек"
        verbose_name_plural = "Люди"
        ordering = ["last_name", "first_name", "middle_name"]
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
        ]

    @property
    def full_name(self) -> str:
        """
        Полное ФИО: (фамилия) (имя) (отчество)
        """
        parts = [self.last_name, self.first_name, self.middle_name]
        return " ".join([p for p in parts if p]).strip()

    @property
    def short_name(self) -> str:
        """
        Короткое ФИО: (фамилия) (инициал имени).(инициал отчества).
        Примеры:
          Соколов Алексей Андреевич -> "Соколов А.А."
          Петров Иван -> "Петров И."
        """
        initials = []
        if self.first_name:
            initials.append(f"{self.first_name[0].upper()}.")
        if self.middle_name:
            initials.append(f"{self.middle_name[0].upper()}.")
        return f"{self.last_name} {' '.join(initials)}".strip()

    def __str__(self) -> str:
        return self.short_name


class PersonNRS(models.Model):
    """
    История НРС по человеку.
    Нужна, чтобы на дату акта подставлялось корректное значение,
    и изменение НРС НЕ переписывало прошлые акты.
    """

    # (uuid) публичный идентификатор записи НРС
    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name="Публичный идентификатор",
    )

    # (person) человек, которому принадлежит НРС
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name="nrs_records",
        verbose_name="Человек",
    )

    # (nrs_id) номер в НРС (строкой, потому что формат бывает с буквами/дефисами)
    nrs_id = models.CharField(
        max_length=64,
        verbose_name="Номер НРС",
    )

    # (valid_from) дата начала действия этой записи НРС
    valid_from = models.DateField(
        verbose_name="Действует с",
    )

    # (valid_to) дата окончания (если пусто — действует до замены/бессрочно)
    valid_to = models.DateField(
        null=True,
        blank=True,
        verbose_name="Действует по (если бессрочно — пусто)",
    )

    # (is_active) быстрое выключение записи (если нужно)
    is_active = models.BooleanField(
        default=True,
        verbose_name="Запись активна",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Создано",
    )

    class Meta:
        verbose_name = "НРС (история)"
        verbose_name_plural = "НРС (история)"
        ordering = ["person", "-valid_from"]
        indexes = [
            models.Index(fields=["person", "valid_from", "valid_to"]),
            models.Index(fields=["nrs_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "nrs_id", "valid_from"],
                name="uniq_person_nrs_from",
            )
        ]

    def __str__(self) -> str:
        return f"{self.person.short_name}: {self.nrs_id} (с {self.valid_from:%d.%m.%Y})"
