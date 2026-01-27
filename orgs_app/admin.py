from django.contrib import admin
from .models import Organization, Person, PersonNRS


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("short_name", "inn", "ogrn", "is_active")
    search_fields = ("short_name", "full_name", "inn", "ogrn")
    list_filter = ("is_active",)
    ordering = ("short_name",)


class PersonNRSInline(admin.TabularInline):
    model = PersonNRS
    extra = 0
    fields = ("nrs_id", "valid_from", "valid_to", "is_active")
    ordering = ("-valid_from",)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("short_name", "last_name", "first_name", "middle_name", "is_active")
    search_fields = ("last_name", "first_name", "middle_name")
    list_filter = ("is_active",)
    ordering = ("last_name", "first_name", "middle_name")
    inlines = (PersonNRSInline,)


@admin.register(PersonNRS)
class PersonNRSAdmin(admin.ModelAdmin):
    list_display = ("person", "nrs_id", "valid_from", "valid_to", "is_active")
    search_fields = (
        "nrs_id",
        "person__last_name",
        "person__first_name",
        "person__middle_name",
    )
    list_filter = ("is_active",)
    ordering = ("person", "-valid_from")
    autocomplete_fields = ("person",)
