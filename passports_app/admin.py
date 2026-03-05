# passports_app/admin.py
from __future__ import annotations

import os

from django.contrib import admin
from django.utils.html import format_html

from .models import Material, Passport


@admin.action(description="Отметить: Требует проверки")
def passports_mark_needs_review(modeladmin, request, queryset):
    queryset.update(needs_review=True)


@admin.action(description="Снять: Требует проверки")
def passports_unmark_needs_review(modeladmin, request, queryset):
    queryset.update(needs_review=False)


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("name", "passports_count")
    search_fields = ("name",)
    ordering = ("name",)

    @admin.display(description="Паспортов")
    def passports_count(self, obj: Material) -> int:
        # related_name="passports"
        return obj.passports.count()


@admin.register(Passport)
class PassportAdmin(admin.ModelAdmin):
    # --- Производительность ---
    list_select_related = ("material", "uploaded_by")

    # --- Список ---
    list_display = (
        "created_at",
        "needs_review",
        "document_name",
        "material",
        "document_number",
        "document_date",
        "file_ext",
        "file_basename",
        "uploaded_by",
    )
    list_filter = (
        "needs_review",
        "file_ext",
        "material",
        "created_at",
        "document_date",
    )
    search_fields = (
        "document_name",
        "document_number",
        "material__name",
        "original_name",
        "file",
        "uploaded_by__username",
        "uploaded_by__email",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    # --- FK UX ---
    autocomplete_fields = ("material", "uploaded_by")  # важно: у UserAdmin/MaterialAdmin должны быть search_fields

    # --- Форма ---
    readonly_fields = ("created_at", "file_ext", "original_name", "file_info")
    fieldsets = (
        ("Документ", {"fields": ("document_name", "document_number", "document_date", "material")}),
        ("Файл", {"fields": ("file", "file_info", "original_name", "file_ext")}),
        ("Статус", {"fields": ("needs_review",)}),
        ("Служебное", {"fields": ("uploaded_by", "created_at")}),
        ("Парсинг", {"fields": ("parsed_meta",), "classes": ("collapse",)}),
    )

    actions = (passports_mark_needs_review, passports_unmark_needs_review)

    @admin.display(description="Файл")
    def file_basename(self, obj: Passport) -> str:
        if not obj.file:
            return "—"
        return os.path.basename(obj.file.name)

    @admin.display(description="Путь в storage")
    def file_info(self, obj: Passport) -> str:
        """
        У тебя storage.base_url=None, поэтому в админке файл обычно НЕ будет кликабельным.
        Показываем относительный путь внутри passports_storage.
        """
        if not obj.file:
            return "—"
        return format_html("<code>{}</code>", obj.file.name)