from __future__ import annotations

from uuid import uuid4

from django.conf import settings
from django.db import models


# =========================
# СПРАВОЧНИКИ
# =========================

class Designer(models.Model):
    code = models.CharField(max_length=10, unique=True)
    full_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Проектировщик"
        verbose_name_plural = "Проектировщики"

    def __str__(self) -> str:
        return f"{self.code} — {self.full_name}"


class Line(models.Model):
    code = models.CharField(max_length=10, unique=True)
    full_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Линия"
        verbose_name_plural = "Линии"

    def __str__(self) -> str:
        return f"{self.code} — {self.full_name}"


class DesignStage(models.Model):
    code = models.CharField(max_length=5, unique=True)
    full_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Стадия проектирования"
        verbose_name_plural = "Стадии проектирования"

    def __str__(self) -> str:
        return f"{self.code} — {self.full_name}"


class Stage(models.Model):
    code = models.CharField(max_length=10, unique=True)
    full_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Этап"
        verbose_name_plural = "Этапы"

    def __str__(self) -> str:
        return self.code


class Plot(models.Model):
    code = models.CharField(max_length=10, unique=True)
    full_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Участок"
        verbose_name_plural = "Участки"

    def __str__(self) -> str:
        return f"{self.code} — {self.full_name}"


class Section(models.Model):
    code = models.CharField(max_length=5, unique=True)
    full_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Раздел проекта"
        verbose_name_plural = "Разделы проекта"

    def __str__(self) -> str:
        return self.code


# =========================
# ПРОЕКТ
# =========================

class Project(models.Model):
    """
    Project — паспорт проекта.
    Используется другими приложениями как единая точка идентификации.
    """

    full_code = models.CharField(
        max_length=128,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Полный шифр проекта (вводится вручную)",
    )

    construction = models.TextField(blank=True, default="")
    needs_review = models.BooleanField(default=True)

    # КЛАССИФИКАТОРЫ (важны для фильтрации и других приложений)
    designer = models.ForeignKey(Designer, on_delete=models.PROTECT, null=True, blank=True)
    line = models.ForeignKey(Line, on_delete=models.PROTECT, null=True, blank=True)
    design_stage = models.ForeignKey(DesignStage, on_delete=models.PROTECT, null=True, blank=True)
    stage = models.ForeignKey(Stage, on_delete=models.PROTECT, null=True, blank=True)
    plot = models.ForeignKey(Plot, on_delete=models.PROTECT, null=True, blank=True)
    section = models.ForeignKey(Section, on_delete=models.PROTECT, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"

    def __str__(self) -> str:
        return self.full_code or f"Черновик проекта #{self.pk}"


# =========================
# РЕВИЗИЯ ПРОЕКТА
# =========================

class ProjectRevision(models.Model):
    """
    Конкретная версия проекта (PDF).
    Именно ревизия может быть выдана в производство.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="revisions")

    revision = models.CharField(max_length=10, default="00")
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)

    sha256 = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    is_latest = models.BooleanField(default=False)

    # 🔴 ВАЖНОЕ НОВОЕ ПОЛЕ
    in_production = models.BooleanField(
        default=False,
        help_text="Выдана ли данная ревизия в производство работ",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["project", "-created_at"]
        unique_together = ("project", "revision")
        verbose_name = "Версия проекта"
        verbose_name_plural = "Версии проектов"

    def __str__(self) -> str:
        base = self.project.full_code or f"Черновик #{self.project_id}"
        return f"{base}-{self.revision}"


# =========================
# ВРЕМЕННАЯ ЗАГРУЗКА
# =========================

class TempUpload(models.Model):
    """
    Временная загрузка PDF до сохранения проекта.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    original_name = models.CharField(max_length=255)
    tmp_path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64, db_index=True)

    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Временная загрузка проекта"
        verbose_name_plural = "Временные загрузки проектов"

    def __str__(self) -> str:
        return f"{self.original_name} ({self.sha256[:8]})"
