from django.contrib import admin

from .models import (
    Designer, Line, DesignStage, Stage, Plot, Section,
    Project, ProjectRevision, TempUpload
)


@admin.register(Designer)
class DesignerAdmin(admin.ModelAdmin):
    list_display = ("code", "full_name", "is_active")
    search_fields = ("code", "full_name")
    list_filter = ("is_active",)


@admin.register(Line)
class LineAdmin(admin.ModelAdmin):
    list_display = ("code", "full_name", "is_active")
    search_fields = ("code", "full_name")
    list_filter = ("is_active",)


@admin.register(DesignStage)
class DesignStageAdmin(admin.ModelAdmin):
    list_display = ("code", "full_name", "is_active")
    search_fields = ("code", "full_name")
    list_filter = ("is_active",)


@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ("code", "is_active")
    search_fields = ("code",)
    list_filter = ("is_active",)


@admin.register(Plot)
class PlotAdmin(admin.ModelAdmin):
    list_display = ("code", "full_name", "is_active")
    search_fields = ("code", "full_name")
    list_filter = ("is_active",)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("code",)
    search_fields = ("code",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("full_code_display", "needs_review", "created_at")
    list_filter = ("needs_review",)
    search_fields = ("internal_code",)

    def full_code_display(self, obj):
        return obj.full_code

    full_code_display.short_description = "Шифр"


@admin.register(ProjectRevision)
class ProjectRevisionAdmin(admin.ModelAdmin):
    list_display = ("project", "revision", "is_latest", "created_at", "sha256_short")
    list_filter = ("is_latest",)
    search_fields = ("file_name", "file_path", "sha256")

    def sha256_short(self, obj):
        return (obj.sha256 or "")[:12]

    sha256_short.short_description = "sha256"


@admin.register(TempUpload)
class TempUploadAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "original_name", "is_used", "created_at", "sha256")
    search_fields = ("original_name", "sha256", "tmp_path")
    list_filter = ("is_used", "created_at")
