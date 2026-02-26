from django.urls import path
from .views import BoxLabelPageView, BoxLabelProjectSearchView, BoxLabelGenerateView, TitleSheetOpenPdfView

app_name = 'documents'

urlpatterns = [
    path("box-label/", BoxLabelPageView.as_view(), name="box_label_page"),
    path("box-label/search/", BoxLabelProjectSearchView.as_view(), name="box_label_search"),
    path("box-label/generate/", BoxLabelGenerateView.as_view(), name="box_label_generate"),
    path(
        "title-sheet/<int:project_id>/<str:doc_type>/open/",
        TitleSheetOpenPdfView.as_view(),
        name="title_sheet_open",
    ),
]
