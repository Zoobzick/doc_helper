# acts_app/urls.py
from django.urls import path

from acts_app.views import (
    ActCreateView,
    ActDetailView,
    ActListView,
    ActRebuildAppendixView,
    ActUpdateView,
    PassportsDatatableView,
)

app_name = "acts_app"

urlpatterns = [
    path("", ActListView.as_view(), name="act_list"),
    path("create/", ActCreateView.as_view(), name="act_create"),

    # DataTables modal passports
    path("passports/datatables/", PassportsDatatableView.as_view(), name="passports_datatable"),

    path("<uuid:uuid>/", ActDetailView.as_view(), name="act_detail"),
    path("<uuid:uuid>/edit/", ActUpdateView.as_view(), name="act_update"),
    path("<uuid:uuid>/rebuild-appendix/", ActRebuildAppendixView.as_view(), name="act_rebuild_appendix"),
]
