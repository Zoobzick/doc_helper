from django.urls import path

from .views import (
    PassportsListView,
    PassportUploadView,
    PassportOpenView,
    PassportSharedOpenView,
    PassportShareLinkCreateView,
    PassportDetailView,
    PassportDeleteView,
    delete_all_passports,
)

app_name = "passports"

urlpatterns = [
    path("", PassportsListView.as_view(), name="passports_list"),
    path("add/", PassportUploadView.as_view(), name="passports_add"),

    path("<int:pk>/open/", PassportOpenView.as_view(), name="passport_open"),
    path("<int:pk>/share-link/", PassportShareLinkCreateView.as_view(), name="passport_share_link"),
    path("shared/<str:token>/", PassportSharedOpenView.as_view(), name="passport_shared_open"),
    path("<int:pk>/delete/", PassportDeleteView.as_view(), name="passport_delete"),
    path("<int:pk>/", PassportDetailView.as_view(), name="passport_detail"),
    path("delete-all/", delete_all_passports, name="delete_all_passports"),
]
