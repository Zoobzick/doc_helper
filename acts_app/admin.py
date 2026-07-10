# acts_app/admin.py
from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from acts_app.models import (
    Act,
    Aook,
    AookProtocolItem,
    AookSourceAct,
    ActParty,
    ActSignatorySnapshot,
    ActMaterialItem,
    ActAttachment,
    ActAppendixLine,
    ActApprovalItem,
)


@admin.register(Act)
class ActAdmin(admin.ModelAdmin):
    list_display = ("number", "act_date", "status", "created_by", "act_year", "act_month", "created_at")
    list_filter = ("status", "created_by", "act_year", "act_month")
    search_fields = ("number", "work_name")
    date_hierarchy = "act_date"
    ordering = ("-act_date", "-id")
    list_editable = ("created_by",)
    filter_horizontal = ("projects", "approvals")
    readonly_fields = ("uuid", "act_year", "act_month", "created_at", "updated_at")

    fieldsets = (
        ("Основное", {"fields": ("uuid", "number", "act_date", "status", "projects")}),
        ("Работы", {
            "fields": (
                "work_name",
                "work_start_date",
                "work_end_date",
                "work_norms_text",
                "allow_next_works_text",
                "extra_info_text",
            )
        }),
        ("Дополнительно", {"fields": ("approvals", "copies_count", "sheets_total")}),
        ("Системные поля", {"fields": ("act_year", "act_month", "created_at", "updated_at")}),
    )

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        label, options = fieldsets[-1]
        fields = tuple(options.get("fields", ()))
        if "created_by" not in fields:
            options = {**options, "fields": ("created_by", *fields)}
            fieldsets[-1] = (label, options)
        return tuple(fieldsets)


class AookSourceActInline(admin.TabularInline):
    model = AookSourceAct
    extra = 0
    autocomplete_fields = ("act",)


class AookProtocolItemInline(admin.TabularInline):
    model = AookProtocolItem
    extra = 0


@admin.register(Aook)
class AookAdmin(admin.ModelAdmin):
    list_display = ("number", "act_date", "project", "created_by", "created_at")
    list_filter = ("project", "created_by", "act_date")
    search_fields = ("number", "work_name", "project__full_code")
    ordering = ("-act_date", "-id")
    autocomplete_fields = ("project",)
    readonly_fields = ("uuid", "created_at", "updated_at")
    inlines = (AookSourceActInline, AookProtocolItemInline)


@admin.register(AookSourceAct)
class AookSourceActAdmin(admin.ModelAdmin):
    list_display = ("id", "aook", "position", "act")
    search_fields = ("aook__number", "act__number", "act__work_name")
    ordering = ("aook", "position", "id")
    autocomplete_fields = ("aook", "act")


@admin.register(AookProtocolItem)
class AookProtocolItemAdmin(admin.ModelAdmin):
    list_display = ("id", "aook", "position", "document_name", "document_number", "document_date")
    search_fields = ("aook__number", "document_name", "document_number", "organization_name")
    ordering = ("aook", "position", "id")
    autocomplete_fields = ("aook",)


@admin.register(ActAttachment)
class ActAttachmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "act_link",
        "title",
        "doc_no",
        "doc_date",
        "type",
        "original_badge",
        "has_file",
        "created_at",
    )
    list_filter = ("original_state", "type", "created_at")
    search_fields = ("title", "doc_no", "act__number", "act__projects__full_code")
    ordering = ("-created_at", "-id")
    autocomplete_fields = ("act",)
    readonly_fields = ("uuid", "created_at")
    list_select_related = ("act",)

    actions = (
        "action_mark_original",
        "action_mark_copy",
        "action_mark_ignore",
    )

    def act_link(self, obj: ActAttachment):
        if not obj.act_id:
            return "—"
        return format_html("<a href='../act/{}/change/'>№{} ({})</a>", obj.act_id, obj.act.number, obj.act.act_date)
    act_link.short_description = "Акт"

    def has_file(self, obj: ActAttachment):
        return bool(getattr(obj, "file", None))
    has_file.boolean = True
    has_file.short_description = "Файл"

    def original_badge(self, obj: ActAttachment):
        st = getattr(obj, "original_state", None)
        if st == ActAttachment.OriginalState.ORIGINAL:
            return format_html("<span style='color:#198754;font-weight:600;'>Оригинал</span>")
        if st == ActAttachment.OriginalState.COPY:
            return format_html("<span style='color:#dc3545;font-weight:600;'>Копия</span>")
        return format_html("<span style='color:#6c757d;font-weight:600;'>Не отслеживать</span>")
    original_badge.short_description = "Статус"

    @admin.action(description="Пометить как ОРИГИНАЛ")
    def action_mark_original(self, request, queryset):
        queryset.update(original_state=ActAttachment.OriginalState.ORIGINAL)

    @admin.action(description="Пометить как КОПИЯ")
    def action_mark_copy(self, request, queryset):
        queryset.update(original_state=ActAttachment.OriginalState.COPY)

    @admin.action(description="Не отслеживать (серый)")
    def action_mark_ignore(self, request, queryset):
        queryset.update(original_state=ActAttachment.OriginalState.IGNORE)


@admin.register(ActMaterialItem)
class ActMaterialItemAdmin(admin.ModelAdmin):
    list_display = ("id", "act", "position", "passport", "manual_name", "sheets_count", "created_at")
    list_filter = ("created_at",)
    search_fields = ("act__number", "manual_name", "manual_doc_no", "passport__material__name")
    ordering = ("-created_at", "-id")
    autocomplete_fields = ("act", "passport")


@admin.register(ActAppendixLine)
class ActAppendixLineAdmin(admin.ModelAdmin):
    list_display = ("id", "act", "position", "label", "sheets_count", "source_attachment")
    list_filter = ("act",)
    search_fields = ("act__number", "label")
    ordering = ("act", "position", "id")
    autocomplete_fields = ("act", "source_attachment")


@admin.register(ActApprovalItem)
class ActApprovalItemAdmin(admin.ModelAdmin):
    list_display = ("id", "act", "position", "approval", "sheets_count", "created_at")
    list_filter = ("created_at",)
    search_fields = ("act__number", "approval__description", "label_override")
    ordering = ("act", "position", "id")
    autocomplete_fields = ("act", "approval")


@admin.register(ActParty)
class ActPartyAdmin(admin.ModelAdmin):
    list_display = ("id", "act", "role", "organization", "is_enabled", "position", "created_at")
    list_filter = ("role", "is_enabled")
    search_fields = ("act__number", "organization__short_name")
    ordering = ("act", "position", "id")
    autocomplete_fields = ("act", "organization", "chosen_authorization")


@admin.register(ActSignatorySnapshot)
class ActSignatorySnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "act", "role", "organization_name", "person_fio", "position", "created_at")
    list_filter = ("role",)
    search_fields = ("act__number", "organization_name", "person_fio", "directive_repr")
    ordering = ("act", "position", "id")
    autocomplete_fields = ("act",)
