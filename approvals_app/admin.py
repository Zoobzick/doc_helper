# approvals_app/admin.py
from __future__ import annotations

import os

from django.contrib import admin
from django.utils.html import format_html

from .models import Approval


@admin.action(description="Пометить как: Согласовано")
def approvals_mark_done(modeladmin, request, queryset):
    queryset.update(status=Approval.Status.DONE)


@admin.action(description="Пометить как: На согласовании")
def approvals_mark_pending(modeladmin, request, queryset):
    queryset.update(status=Approval.Status.PENDING)


@admin.register(Approval)
class ApprovalAdmin(admin.ModelAdmin):
    # --- Производительность ---
    list_select_related = ("project",)

    # --- Список ---
    list_display = (
        "created_at",
        "status",
        "project_display",
        "construction",
        "file_basename",
        "uuid",
    )
    list_filter = ("status", "created_at", "project")
    search_fields = (
        "uuid",
        "construction",
        "description",
        "project__full_code",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    # --- FK UX ---
    autocomplete_fields = ("project",)  # важно: у ProjectAdmin должны быть search_fields
    # Если у ProjectAdmin нет search_fields и автокомплит не заведётся — временно замени на:
    # raw_id_fields = ("project",)

    # --- Форма ---
    readonly_fields = ("uuid", "created_at", "file_info")
    fieldsets = (
        ("Основное", {"fields": ("status", "project", "construction")}),
        ("Описание", {"fields": ("description",)}),
        ("Файл", {"fields": ("file", "file_info")}),
        ("Служебное", {"fields": ("uuid", "created_at")}),
    )

    actions = (approvals_mark_done, approvals_mark_pending)

    @admin.display(description="Проект", ordering="project__full_code")
    def project_display(self, obj: Approval) -> str:
        return obj.project.full_code if obj.project else "Общие"

    @admin.display(description="Файл")
    def file_basename(self, obj: Approval) -> str:
        if not obj.file:
            return "—"
        return os.path.basename(obj.file.name)

    @admin.display(description="Путь в storage")
    def file_info(self, obj: Approval) -> str:
        """
        У тебя storage.base_url=None, поэтому в админке файл обычно НЕ будет кликабельным.
        Здесь показываем относительный путь в approvals_storage.
        """
        if not obj.file:
            return "—"
        # obj.file.name — относительный путь внутри approvals_storage
        return format_html("<code>{}</code>", obj.file.name)