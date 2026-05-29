# acts_app/urls.py
from django.urls import path
from . import views

app_name = "acts_app"

urlpatterns = [
    # список / создание
    path("", views.ActListView.as_view(), name="act_list"),
    path("create/", views.ActCreateView.as_view(), name="act_create"),

    # детали / редактирование
    path("<uuid:uuid>/", views.ActDetailView.as_view(), name="act_detail"),
    path("<uuid:uuid>/edit/", views.ActUpdateView.as_view(), name="act_update"),

    # загрузки
    path("<uuid:uuid>/docx/", views.ActDocxDownloadView.as_view(), name="act_docx_download"),
    path("<uuid:uuid>/pdf/", views.ActPdfPreviewView.as_view(), name="act_pdf_preview"),
    path("<uuid:uuid>/registry-p3/", views.ActRegistryP3DocxDownloadView.as_view(),
         name="act_registry_p3_docx_download"),
    path("<uuid:uuid>/registry-p3/pdf/", views.ActRegistryP3PdfPreviewView.as_view(),
         name="act_registry_p3_pdf_preview"),

    # пересборка / финализация
    path("<uuid:uuid>/rebuild-appendix/", views.ActRebuildAppendixView.as_view(), name="act_rebuild_appendix"),
    path("<uuid:uuid>/finalize/", views.ActFinalizeView.as_view(), name="act_finalize"),

    # ajax datatables
    path("projects/datatable/", views.ProjectsSearchView.as_view(), name="projects_datatable"),
    path("approvals/datatable/", views.ApprovalsDatatableView.as_view(), name="approvals_datatable"),
    path("passports/datatable/", views.PassportsDatatableView.as_view(), name="passports_datatable"),

    # открытие файлов
    path("passport/<int:pk>/open/", views.PassportOpenView.as_view(), name="passport_open"),
    path("approval/<int:pk>/open/", views.ApprovalOpenView.as_view(), name="approval_open"),
    path("attachment/<int:pk>/open/", views.ActAttachmentOpenView.as_view(), name="act_attachment_open"),

    # parties (htmx)
    path("<uuid:uuid>/parties/", views.ActPartiesTableView.as_view(), name="act_parties_table"),
    path("<uuid:uuid>/parties/preview-by-date/", views.ActPartiesPreviewByDateView.as_view(),
         name="act_parties_preview_by_date"),
    path("party/<uuid:party_uuid>/", views.ActPartyRowView.as_view(), name="act_party_row"),
    path("party/<uuid:party_uuid>/toggle-enabled/", views.ActPartyToggleEnabledView.as_view(),
         name="act_party_toggle_enabled"),
    path("party/<uuid:party_uuid>/set-organization/", views.ActPartySetOrganizationView.as_view(),
         name="act_party_set_organization"),
    path("party/<uuid:party_uuid>/candidates/", views.ActPartyCandidatesView.as_view(), name="act_party_candidates"),
    path("party/<uuid:party_uuid>/choose-authorization/", views.ActPartyChooseAuthorizationView.as_view(),
         name="act_party_choose_authorization"),
    path("<uuid:uuid>/party/add-other/", views.ActPartyAddOtherView.as_view(), name="act_party_add_other"),
    path("party/<uuid:party_uuid>/delete-other/", views.ActPartyDeleteOtherView.as_view(),
         name="act_party_delete_other"),

    # protocols
    path("protocols/", views.ProtocolListView.as_view(), name="protocol_list"),
]
