from django.urls import path

from backup_app import views


app_name = "backup_app"

urlpatterns = [
    path("", views.BackupListView.as_view(), name="backup_list"),
    path("create/", views.BackupCreateView.as_view(), name="backup_create"),
    path("<int:pk>/download/", views.BackupDownloadView.as_view(), name="backup_download"),
    path("<int:pk>/delete/", views.BackupDeleteView.as_view(), name="backup_delete"),
]
