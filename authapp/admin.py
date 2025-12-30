from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    """
    Даёт доступ к системным правам (auth.Permission) через админку,
    чтобы можно было удалять дубли и быстро искать permissions.
    """
    list_display = ("name", "codename", "content_type")
    search_fields = ("name", "codename", "content_type__app_label", "content_type__model")
    list_filter = ("content_type__app_label", "content_type__model")
    ordering = ("content_type__app_label", "content_type__model", "codename")


@admin.register(ContentType)
class ContentTypeAdmin(admin.ModelAdmin):
    """
    Обычно не нужно трогать, но полезно для диагностики.
    Если не хочешь — можно удалить этот класс.
    """
    list_display = ("app_label", "model")
    search_fields = ("app_label", "model")
    list_filter = ("app_label",)
    ordering = ("app_label", "model")
