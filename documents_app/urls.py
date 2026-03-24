from django.urls import path

from documents_app.views import (
    BoxLabelGenerateView,
    BoxLabelPageView,
    BoxLabelProjectSearchView,
    DocumentBatchCreateDraftView,
    DocumentBatchDetailView,
    DocumentBatchGenerateView,
    DocumentBatchMasterView,
    TitleSheetOpenPdfView,
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
        "id-handover/batches/<int:batch_id>/generate/",
        DocumentBatchGenerateView.as_view(),
        name="id_handover_batch_generate",
    ),
    path(
        "id-handover/batches/<int:batch_id>/",
        DocumentBatchDetailView.as_view(),
        name="id_handover_batch_detail",
    ),
]