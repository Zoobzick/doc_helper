from __future__ import annotations

from uuid import uuid4

from django.conf import settings
from django.db import models


class Designer(models.Model):
    code = models.CharField(max_length=10, unique=True)
    is_active = models.BooleanField(default=True)
    full_name = models.CharField(max_length=255)

    class Meta:
        ordering = ["code"]
        verbose_name = "Проектировщик"
        verbose_name_plural = "Проектировщики"

    def __str__(self):
        return f"{self.code} — {self.full_name}"


class Line(models.Model):
    code = models.CharField(max_length=10, unique=True)
    full_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Линия"
        verbose_name_plural = "Линии"

    def __str__(self):
        return f"{self.code} — {self.full_name}"


class DesignStage(models.Model):
    code = models.CharField(max_length=2, unique=True)
    full_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Стадия проектирования"
        verbose_name_plural = "Стадии проектирования"

    def __str__(self):
        return f"{self.code} — {self.full_name}"


class Stage(models.Model):
    code = models.CharField(max_length=10, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Этап"
        verbose_name_plural = "Этапы"

    def __str__(self):
        return self.code


class Plot(models.Model):
    code = models.CharField(max_length=10, unique=True)
    is_active = models.BooleanField(default=True)
    full_name = models.CharField(max_length=255)

    class Meta:
        ordering = ["code"]
        verbose_name = "Участок"
        verbose_name_plural = "Участки"

    def __str__(self):
        return f"{self.code} — {self.full_name}"


class Section(models.Model):
    code = models.CharField(max_length=5, unique=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Раздел проекта"
        verbose_name_plural = "Разделы проекта"

    def __str__(self):
        return self.code


class Project(models.Model):
    number = models.PositiveIntegerField()
    internal_code = models.CharField(max_length=20, default="")
    construction = models.TextField(default="")
    needs_review = models.BooleanField(default=False)

    design_stage = models.ForeignKey(DesignStage, on_delete=models.PROTECT)
    designer = models.ForeignKey(Designer, on_delete=models.PROTECT)
    line = models.ForeignKey(Line, on_delete=models.PROTECT)
    plot = models.ForeignKey(Plot, on_delete=models.PROTECT)
    section = models.ForeignKey(Section, on_delete=models.PROTECT)
    stage = models.ForeignKey(Stage, on_delete=models.PROTECT)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"
        unique_together = (
            "designer",
            "line",
            "design_stage",
            "stage",
            "plot",
            "section",
            "number",
            "internal_code",
        )
        permissions = [
            ("view_projects_page", "Может открывать страницу списка проектов"),
            ("view_project_detail_page", "Может открывать страницу проекта (детали)"),
            ("open_project_revision_pdf", "Может открывать PDF версии проекта"),
            ("scan_projects", "Может сканировать папку и обновлять проекты"),
        ]

    @property
    def full_code(self) -> str:
        parts = [
            self.designer.code,
            self.line.code,
            self.design_stage.code,
            self.stage.code,
            self.plot.code,
        ]
        if self.internal_code:
            parts.append(self.internal_code)
        parts.append(f"{self.section.code}{self.number}")
        return "-".join(parts)

    def __str__(self):
        return self.full_code


class ProjectRevision(models.Model):
    revision = models.CharField(max_length=10, default="00")
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    is_latest = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # NEW: sha256 для дедупа (пока nullable, чтобы не ломать старые записи)
    sha256 = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="revisions")

    class Meta:
        ordering = ["project", "-created_at"]
        verbose_name = "Версия проекта"
        verbose_name_plural = "Версии проектов"
        unique_together = ("project", "revision")

    def __str__(self):
        return f"{self.project.full_code}-{self.revision}"


class TempUpload(models.Model):
    """
    Временная загрузка PDF до сохранения проекта.
    Файл лежит в PROJECTS_ROOT/_tmp_uploads/<uuid>.pdf
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

    def __str__(self):
        return f"{self.original_name} ({self.sha256[:8]})"
