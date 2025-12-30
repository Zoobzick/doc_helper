from django.urls import path
from .views import (
    ApprovalDoneListView,
    ApprovalPendingListView,
    ApprovalOpenPdfView,
    ApprovalDeleteView,
    ProjectSearchView,
    ApprovalMarkDoneRedirectView,
)

app_name = "approvals"

urlpatterns = [
    path("", ApprovalDoneListView.as_view(), name="done"),
    path("pending/", ApprovalPendingListView.as_view(), name="pending"),

    path("pending/<int:pk>/mark-done/", ApprovalMarkDoneRedirectView.as_view(), name="mark_done"),

    path("projects/search/", ProjectSearchView.as_view(), name="project_search"),
    path("<int:pk>/open/", ApprovalOpenPdfView.as_view(), name="open_pdf"),
    path("<int:pk>/delete/", ApprovalDeleteView.as_view(), name="delete"),
]
