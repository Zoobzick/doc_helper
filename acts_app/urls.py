# acts_app/urls.py
from django.urls import path

from acts_app.views import (
    ActListView,
    ActDetailView,
    ActCreateView,
    ActUpdateView,
    ActRebuildAppendixView,
)

app_name = "acts_app"

urlpatterns = [
    path("acts/", ActListView.as_view(), name="act_list"),
    path("acts/create/", ActCreateView.as_view(), name="act_create"),
    path("acts/<uuid:uuid>/", ActDetailView.as_view(), name="act_detail"),
    path("acts/<uuid:uuid>/edit/", ActUpdateView.as_view(), name="act_update"),
    path("acts/<uuid:uuid>/rebuild-appendix/", ActRebuildAppendixView.as_view(), name="act_rebuild_appendix"),
]
