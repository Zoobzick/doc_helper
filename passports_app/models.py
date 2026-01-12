from __future__ import annotations

import os
import re
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils.text import slugify


class PassportsStorage(FileSystemStorage):
    """
    Файлы паспортов лежат физически в settings.PASSPORTS_DIR.
    Публичных URL нет, доступ только через view.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(location=str(settings.PASSPORTS_DIR), base_url=None, *args, **kwargs)


passports_storage = PassportsStorage()


def _safe_folder_name(s: str) -> str:
    """
    Делаем безопасное имя папки.
    slugify по-русски обычно даёт пусто, поэтому:
    - пробелы/слэши/двойные пробелы убираем
    - запрещённые символы заменяем на "_"
    """
    s = " ".join((s or "").strip().split())
    if not s:
        return "_unparsed"

    # slugify может вернуть пусто на кириллице — оставим оригинал, но зачистим
    cleaned = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", s)
    cleaned = cleaned.strip(" .")
    return cleaned or "_unparsed"


def passport_upload_to(instance: "Passport", filename: str) -> str:
    """
    Кладём по папкам материала.
    Если материала нет — в _unparsed.
    """
    safe_name = os.path.basename(filename)

    if instance.material_id and instance.material and instance.material.name:
        folder = _safe_folder_name(instance.material.name)
    else:
        folder = "_unparsed"

    return os.path.join(folder, safe_name)


class Material(models.Model):
    name = models.CharField("Материал", max_length=255, unique=True)

    class Meta:
        verbose_name = "Материал"
        verbose_name_plural = "Материалы"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Passport(models.Model):
    # --- Метаданные документа ---
    document_name = models.CharField("Наименование документа", max_length=255)
    document_number = models.CharField("Номер документа", max_length=128, blank=True, default="")
    document_date = models.DateField("Дата документа", null=True, blank=True)

    material = models.ForeignKey(
        Material,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="passports",
        verbose_name="Материал",
    )

    # --- Файл ---
    file = models.FileField(
        "Файл",
        upload_to=passport_upload_to,
        storage=passports_storage,
        max_length=500,
    )
    original_name = models.CharField("Исходное имя файла", max_length=255, blank=True, default="")
    file_ext = models.CharField("Расширение", max_length=10, blank=True, default="", db_index=True)

    # --- Статус ---
    needs_review = models.BooleanField("Требует проверки", default=False, db_index=True)

    # --- Служебное ---
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_passports",
        verbose_name="Загрузил",
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    parsed_meta = models.JSONField("Данные парсинга", default=dict, blank=True)

    class Meta:
        verbose_name = "Паспорт"
        verbose_name_plural = "Паспорта"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["needs_review"]),
            models.Index(fields=["document_number"]),
            models.Index(fields=["document_date"]),
            models.Index(fields=["file_ext"]),
            models.Index(fields=["material", "document_number"]),
        ]

        permissions = [
            ("upload_passport_zip", "Может загружать паспорта ZIP архивом"),
            ("open_passport_file", "Может открывать файл паспорта"),
            ("delete_all_passports", "Может удалять ВСЕ паспорта"),
            ("view_passports_page", "Может просматривать страницу паспортов"),
        ]

    def save(self, *args, **kwargs):
        if self.file and not self.original_name:
            self.original_name = os.path.basename(self.file.name)

        if self.file and not self.file_ext:
            _, ext = os.path.splitext(self.file.name)
            self.file_ext = (ext or "").lower().lstrip(".")[:10]

        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.document_name
