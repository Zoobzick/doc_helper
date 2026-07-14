# acts_app/urls.py
from django.urls import path
from . import views

app_name = "acts_app"

urlpatterns = [
    # список / создание
    path("", views.ActListView.as_view(), name="act_list"),
    path("create/", views.ActCreateView.as_view(), name="act_create"),
    path("aook/create/", views.AookCreateView.as_view(), name="aook_create"),
    path("aook/<uuid:uuid>/", views.AookDetailView.as_view(), name="aook_detail"),
    path("aook/<uuid:uuid>/edit/", views.AookUpdateView.as_view(), name="aook_update"),
    path("aook/<uuid:uuid>/delete/", views.AookDeleteView.as_view(), name="aook_delete"),
    path("aook/<uuid:uuid>/rebuild/", views.AookRebuildFilesView.as_view(), name="aook_rebuild"),
    path("aook/<uuid:uuid>/pdf/", views.AookPdfPreviewView.as_view(), name="aook_pdf_preview"),
    path("aook/<uuid:uuid>/registry-acts/pdf/", views.AookRegistryPdfPreviewView.as_view(registry_type="acts"), name="aook_acts_registry_pdf_preview"),
    path("aook/<uuid:uuid>/registry-protocols/pdf/", views.AookRegistryPdfPreviewView.as_view(registry_type="protocols"), name="aook_protocols_registry_pdf_preview"),
    path("aook/<uuid:uuid>/zip/", views.AookZipDownloadView.as_view(), name="aook_zip_download"),
    path("export/", views.ActBulkExportView.as_view(), name="act_bulk_export"),
    path("appendix-search/", views.AppendixSearchView.as_view(), name="appendix_search"),

    # детали / редактирование
    path("<uuid:uuid>/", views.ActDetailView.as_view(), name="act_detail"),
    path("<uuid:uuid>/edit/", views.ActUpdateView.as_view(), name="act_update"),
    path("<uuid:uuid>/duplicate/", views.ActDuplicateView.as_view(), name="act_duplicate"),
    path("<uuid:uuid>/delete/", views.ActDeleteView.as_view(), name="act_delete"),

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
