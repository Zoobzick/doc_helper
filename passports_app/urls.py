from django.urls import path
from . import views

urlpatterns = [
    path("", views.PassportsListView.as_view(), name="passports_list"),
    path("import-passports/", views.import_passports_view, name="import_passports"),
    path("open/<int:pk>/", views.view_pdf, name="view_pdf"),
    path("<int:pk>/update-consumption/", views.update_consumption, name="update_consumption"),
]
