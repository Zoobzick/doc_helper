from django.urls import path

from .views import (
    PassportsListView,
    PassportUploadView,
    PassportOpenView,
    PassportDetailView,
    PassportDeleteView,
)

app_name = "passports"

urlpatterns = [
    path("", PassportsListView.as_view(), name="passports_list"),
    path("add/", PassportUploadView.as_view(), name="passports_add"),

    path("<int:pk>/open/", PassportOpenView.as_view(), name="passport_open"),
    path("<int:pk>/delete/", PassportDeleteView.as_view(), name="passport_delete"),
    path("<int:pk>/", PassportDetailView.as_view(), name="passport_detail"),
]
