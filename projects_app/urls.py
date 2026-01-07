from django.urls import path

from .views import (
    ProjectListView,
    ProjectDetailView,
    ProjectRevisionOpenView,
    ProjectCreateStartView,
    TempUploadPdfView,
    ProjectCreateWithPdfView,
    ProjectAssignFullCodeView,
    ProjectRevisionSetInProductionView,
    ProjectUpdateView,
    ProjectRevisionDeleteView,
)

app_name = "projects"

urlpatterns = [
    path("", ProjectListView.as_view(), name="projects_list"),

    path("project/<int:pk>/", ProjectDetailView.as_view(), name="project_detail"),
    path("project/<int:pk>/assign-full-code/", ProjectAssignFullCodeView.as_view(), name="project_assign_full_code"),
    path("project/<int:pk>/edit/", ProjectUpdateView.as_view(), name="project_update"),

    path("revision/<int:pk>/open/", ProjectRevisionOpenView.as_view(), name="project_revision_open"),
    path("revision/<int:pk>/set-in-production/", ProjectRevisionSetInProductionView.as_view(),
         name="project_revision_set_in_production"),
    path("revision/<int:pk>/delete/", ProjectRevisionDeleteView.as_view(), name="project_revision_delete"),

    # ручное добавление (web)
    path("create/", ProjectCreateStartView.as_view(), name="project_create"),
    path("create/upload-temp/", TempUploadPdfView.as_view(), name="project_upload_temp"),
    path("create/save/", ProjectCreateWithPdfView.as_view(), name="project_create_save"),
]
