from django.urls import path

from documents_app.views import (
    BatchAttachmentDeleteView,
    BatchAttachmentOpenView,
    BatchAttachmentUploadView,
    BoxLabelGenerateView,
    BoxLabelPageView,
    BoxLabelProjectSearchView,
    DocumentBatchAddManualActView,
    DocumentBatchActAddReviewNoteView,
    DocumentBatchCreateDraftView,
    DocumentBatchDeleteView,
    DocumentBatchDetailView,
    DocumentBatchDownloadRegistriesView,
    DocumentBatchGenerateView,
    DocumentBatchListView,
    DocumentBatchMasterView,
    DocumentBatchMoveActDownView,
    DocumentBatchMoveActUpView,
    DocumentBatchProjectMarkReviewedView,
    DocumentBatchProjectReviewView,
    DocumentBatchProjectActsLookupView,
    DocumentBatchRemoveProjectView,
    DocumentBatchRefreshProjectCompositionView,
    DocumentBatchRefreshCompositionView,
    DocumentBatchRemoveActView,
    DocumentBatchUpdateDraftView,
    GeneratedDocumentOpenView,
    TitleSheetOpenPdfView,
    id_handover_batch_acts_lookup,
)

app_name = "documents"

urlpatterns = [
    path(
        "box-label/",
        BoxLabelPageView.as_view(),
        name="box_label_page",
    ),
    path(
        "box-label/search/",
        BoxLabelProjectSearchView.as_view(),
        name="box_label_search",
    ),
    path(
        "box-label/generate/",
        BoxLabelGenerateView.as_view(),
        name="box_label_generate",
    ),
    path(
        "title-sheet/<int:project_id>/<str:doc_type>/open/",
        TitleSheetOpenPdfView.as_view(),
        name="title_sheet_open",
    ),
    path(
        "generated-documents/<uuid:document_uuid>/open/",
        GeneratedDocumentOpenView.as_view(),
        name="generated_document_open",
    ),
    path(
        "batch-attachments/<uuid:attachment_uuid>/open/",
        BatchAttachmentOpenView.as_view(),
        name="batch_attachment_open",
    ),

    # -----------------------------
    # ID handover batch list / master
    # -----------------------------
    path(
        "id-handover/batches/",
        DocumentBatchListView.as_view(),
        name="id_handover_batch_list",
    ),
    path(
        "id-handover/master/",
        DocumentBatchMasterView.as_view(),
        name="id_handover_master",
    ),
    path(
        "id-handover/master/create-draft/",
        DocumentBatchCreateDraftView.as_view(),
        name="id_handover_create_draft",
    ),

    path(
        "id-handover/batches/<int:batch_id>/master/",
        DocumentBatchMasterView.as_view(),
        name="id_handover_batch_master",
    ),
    path(
        "id-handover/batches/<int:batch_id>/update-draft/",
        DocumentBatchUpdateDraftView.as_view(),
        name="id_handover_batch_update_draft",
    ),
    path(
        "id-handover/batches/<int:batch_id>/refresh-composition/",
        DocumentBatchRefreshCompositionView.as_view(),
        name="id_handover_batch_refresh_composition",
    ),
    path(
        "id-handover/batches/<int:batch_id>/projects/<int:project_id>/refresh-composition/",
        DocumentBatchRefreshProjectCompositionView.as_view(),
        name="id_handover_batch_refresh_project_composition",
    ),
    path(
        "id-handover/batches/<int:batch_id>/projects/<int:project_id>/review/",
        DocumentBatchProjectReviewView.as_view(),
        name="id_handover_batch_project_review",
    ),
    path(
        "id-handover/batches/<int:batch_id>/projects/<int:project_id>/mark-reviewed/",
        DocumentBatchProjectMarkReviewedView.as_view(),
        name="id_handover_batch_project_mark_reviewed",
    ),
    path(
        "id-handover/batches/<int:batch_id>/projects/<int:project_id>/remove/",
        DocumentBatchRemoveProjectView.as_view(),
        name="id_handover_batch_remove_project",
    ),

    # -----------------------------
    # Step 2 actions
    # -----------------------------
    path(
        "id-handover/batches/<int:batch_id>/projects/acts-lookup/",
        DocumentBatchProjectActsLookupView.as_view(),
        name="id_handover_batch_project_acts_lookup",
    ),
    path(
        "id-handover/batch/<int:batch_id>/acts-lookup/",
        id_handover_batch_acts_lookup,
        name="id_handover_batch_acts_lookup",
    ),
    path(
        "id-handover/batches/<int:batch_id>/acts/add-manual/",
        DocumentBatchAddManualActView.as_view(),
        name="id_handover_batch_add_manual_act",
    ),
    path(
        "id-handover/batches/<int:batch_id>/acts/<int:batch_act_id>/move-up/",
        DocumentBatchMoveActUpView.as_view(),
        name="id_handover_batch_move_act_up",
    ),
    path(
        "id-handover/batches/<int:batch_id>/acts/<int:batch_act_id>/move-down/",
        DocumentBatchMoveActDownView.as_view(),
        name="id_handover_batch_move_act_down",
    ),
    path(
        "id-handover/batches/<int:batch_id>/acts/<int:batch_act_id>/remove/",
        DocumentBatchRemoveActView.as_view(),
        name="id_handover_batch_remove_act",
    ),
    path(
        "id-handover/batches/<int:batch_id>/acts/<int:batch_act_id>/review-notes/add/",
        DocumentBatchActAddReviewNoteView.as_view(),
        name="id_handover_batch_act_add_review_note",
    ),

    # -----------------------------
    # Step 3 / result
    # -----------------------------
    path(
        "id-handover/batches/<int:batch_id>/generate/",
        DocumentBatchGenerateView.as_view(),
        name="id_handover_batch_generate",
    ),
    path(
        "id-handover/batches/<int:batch_id>/download-registries/",
        DocumentBatchDownloadRegistriesView.as_view(),
        name="id_handover_batch_download_registries",
    ),
    path(
        "id-handover/batches/<int:batch_id>/attachments/upload/",
        BatchAttachmentUploadView.as_view(),
        name="id_handover_batch_attachment_upload",
    ),
    path(
        "id-handover/batches/<int:batch_id>/attachments/<uuid:attachment_uuid>/delete/",
        BatchAttachmentDeleteView.as_view(),
        name="id_handover_batch_attachment_delete",
    ),
    path(
        "id-handover/batches/<int:batch_id>/delete/",
        DocumentBatchDeleteView.as_view(),
        name="id_handover_batch_delete",
    ),
    path(
        "id-handover/batches/<int:batch_id>/",
        DocumentBatchDetailView.as_view(),
        name="id_handover_batch_detail",
    ),
]
