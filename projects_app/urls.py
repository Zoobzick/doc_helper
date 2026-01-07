from django.urls import path

from .views import (
    ProjectListView,
    ProjectDetailView,
    ProjectRevisionOpenView,

    ProjectCreateStartView,
    TempUploadPdfView,
    ProjectCreateWithPdfView,
    ProjectRevisionSetInProductionView, ProjectUpdateView, ProjectRevisionDeleteView,
)

app_name = "projects"

urlpatterns = [
    path("", ProjectListView.as_view(), name="projects_list"),
    path("project/<int:pk>/", ProjectDetailView.as_view(), name="project_detail"),
    path("revision/<int:pk>/open/", ProjectRevisionOpenView.as_view(), name="project_revision_open"),

    # ручное добавление
    path("create/", ProjectCreateStartView.as_view(), name="project_create"),
    path("create/upload-temp/", TempUploadPdfView.as_view(), name="project_upload_temp"),
    path("create/save/", ProjectCreateWithPdfView.as_view(), name="project_create_save"),

    path(
        "revision/<int:pk>/set-in-production/",
        ProjectRevisionSetInProductionView.as_view(),
        name="project_revision_set_in_production",
    ),

    path(
        "project/<int:pk>/edit/",
        ProjectUpdateView.as_view(),
        name="project_update",
    ),
    path(
        "revision/<int:pk>/delete/",
        ProjectRevisionDeleteView.as_view(),
        name="project_revision_delete",
    ),

]
