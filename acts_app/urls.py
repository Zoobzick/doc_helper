# acts_app/urls.py
from __future__ import annotations

from django.urls import path

from acts_app import views

app_name = "acts_app"

urlpatterns = [
    # list/detail
    path("acts/", views.ActListView.as_view(), name="act_list"),
    path("acts/<uuid:uuid>/", views.ActDetailView.as_view(), name="act_detail"),

    # create/update
    path("acts/create/", views.ActCreateView.as_view(), name="act_create"),
    path("acts/<uuid:uuid>/edit/", views.ActUpdateView.as_view(), name="act_update"),

    # appendix rebuild
    path("acts/<uuid:uuid>/rebuild-appendix/", views.ActRebuildAppendixView.as_view(), name="act_rebuild_appendix"),

    # finalize
    path("acts/<uuid:uuid>/finalize/", views.ActFinalizeView.as_view(), name="act_finalize"),

    # passports ajax
    path("acts/passports/datatable/", views.PassportsDatatableView.as_view(), name="passports_datatable"),
    path("acts/passports/labels/", views.PassportsLabelsView.as_view(), name="passports_labels"),

    # parties: table + preview + row
    path("acts/<uuid:uuid>/parties/", views.ActPartiesTableView.as_view(), name="act_parties_table"),
    path("acts/<uuid:uuid>/parties/preview/", views.ActPartiesPreviewByDateView.as_view(),
         name="act_parties_preview_by_date"),
    path("acts/party/<uuid:party_uuid>/row/", views.ActPartyRowView.as_view(), name="act_party_row"),

    # parties: edits
    path("acts/party/<uuid:party_uuid>/toggle/", views.ActPartyToggleEnabledView.as_view(),
         name="act_party_toggle_enabled"),
    path("acts/party/<uuid:party_uuid>/set-org/", views.ActPartySetOrganizationView.as_view(),
         name="act_party_set_org"),

    # parties: conflicts (candidates + choose)
    path("acts/party/<uuid:party_uuid>/candidates/", views.ActPartyCandidatesView.as_view(),
         name="act_party_candidates"),
    path("acts/party/<uuid:party_uuid>/choose/", views.ActPartyChooseAuthorizationView.as_view(),
         name="act_party_choose_authorization"),

    # parties: other reps
    path("acts/<uuid:uuid>/party/other/add/", views.ActPartyAddOtherView.as_view(), name="act_party_add_other"),
    path("acts/party/<uuid:party_uuid>/other/delete/", views.ActPartyDeleteOtherView.as_view(),
         name="act_party_delete_other"),
    path("projects/search/", views.ProjectsSearchView.as_view(), name="projects_search"),

    path("approvals/datatable/", views.ApprovalsDatatableView.as_view(), name="approvals_datatable"),
    path("acts/<uuid:uuid>/docx/", views.ActDocxDownloadView.as_view(), name="act_docx_download"),

]
