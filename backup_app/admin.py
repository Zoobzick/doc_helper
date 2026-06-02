from django.contrib import admin

from backup_app.models import BackupRun


@admin.register(BackupRun)
class BackupRunAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "trigger", "created_by", "size_bytes", "created_at", "completed_at")
    list_filter = ("status", "trigger", "created_at")
    readonly_fields = (
        "created_by",
        "trigger",
        "status",
        "reason",
        "file_path",
        "size_bytes",
        "error",
        "created_at",
        "completed_at",
    )
    search_fields = ("file_path", "reason", "error")
