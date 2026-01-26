from django.urls import path
from checker_app import views
from checker_app.views import act_structure_page

urlpatterns = [
    path('path_and_projects/', views.CheckActsView.as_view(), name='checker_app_check_projects_and_folder'),
    path('check-doc-acts/', views.CheckDocActsView.as_view(), name='check_doc_acts'),
    path("act-structure/", act_structure_page, name="act_structure_page"),
]
