from django.urls import path

from .views import (
    ProjectListView,
    ProjectDetailView,
    ProjectRevisionOpenView,
    ScanProjectsView,

    ProjectCreateStartView,
    TempUploadPdfView,
    ProjectCreateWithPdfView,
    AddCatalogItemView,
)

app_name = "projects"

urlpatterns = [
    path("", ProjectListView.as_view(), name="projects_list"),
    path("project/<int:pk>/", ProjectDetailView.as_view(), name="project_detail"),
    path("revision/<int:pk>/open/", ProjectRevisionOpenView.as_view(), name="project_revision_open"),
    path("scan/", ScanProjectsView.as_view(), name="scan_projects"),

    # ручное добавление
    path("create/", ProjectCreateStartView.as_view(), name="project_create"),
    path("create/upload-temp/", TempUploadPdfView.as_view(), name="project_upload_temp"),
    path("create/save/", ProjectCreateWithPdfView.as_view(), name="project_create_save"),

    # добавление справочников на месте
    path("catalog/add/", AddCatalogItemView.as_view(), name="catalog_add"),
]
