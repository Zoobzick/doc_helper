from django.contrib import admin
from .models import Directive, Authorization


class AuthorizationInline(admin.TabularInline):
    """
    Полномочия, связанные с конкретным приказом.
    Редактируются прямо внутри Directive.
    """
    model = Authorization
    extra = 0
    autocomplete_fields = ("organization", "person")
    fields = (
        "organization",
        "person",
        "role",
        "position_text",
        "valid_from",
        "valid_to",
        "is_active",
    )
    ordering = ("organization", "role", "person")


@admin.register(Directive)
class DirectiveAdmin(admin.ModelAdmin):
    """
    Админка приказов / доверенностей.
    """
    list_display = (
        "number",
        "date",
        "doc_type",
        "issuer_organization",
        "is_active",
    )
    list_filter = (
        "doc_type",
        "issuer_organization",
        "is_active",
    )
    search_fields = (
        "number",
        "note",
    )
    ordering = ("-date", "number")
    inlines = (AuthorizationInline,)

    fieldsets = (
        (
            "Документ",
            {
                "fields": (
                    "doc_type",
                    "number",
                    "date",
                    "issuer_organization",
                    "pdf_file",
                )
            },
        ),
        (
            "Дополнительно",
            {
                "fields": (
                    "note",
                    "is_active",
                )
            },
        ),
    )


@admin.register(Authorization)
class AuthorizationAdmin(admin.ModelAdmin):
    """
    Отдельная админка полномочий (для поиска и массовых правок).
    """
    list_display = (
        "organization",
        "person",
        "role",
        "directive",
        "valid_from",
        "valid_to",
        "is_active",
    )
    list_filter = (
        "role",
        "organization",
        "is_active",
    )
    search_fields = (
        "person__last_name",
        "person__first_name",
        "person__middle_name",
        "organization__short_name",
        "directive__number",
    )
    autocomplete_fields = (
        "organization",
        "person",
        "directive",
    )
    ordering = (
        "organization",
        "role",
        "person",
    )

    fieldsets = (
        (
            "Кто и от кого",
            {
                "fields": (
                    "organization",
                    "person",
                    "role",
                    "position_text",
                )
            },
        ),
        (
            "Основание",
            {
                "fields": (
                    "directive",
                )
            },
        ),
        (
            "Срок действия",
            {
                "fields": (
                    "valid_from",
                    "valid_to",
                    "is_active",
                )
            },
        ),
    )
