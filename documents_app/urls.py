from django.urls import path
from .views import BoxLabelPageView, BoxLabelProjectSearchView, BoxLabelGenerateView

app_name = 'documents'

urlpatterns = [
    path("box-label/", BoxLabelPageView.as_view(), name="box_label_page"),
    path("box-label/search/", BoxLabelProjectSearchView.as_view(), name="box_label_search"),
    path("box-label/generate/", BoxLabelGenerateView.as_view(), name="box_label_generate"),
]
