from django.urls import path
from . import views

app_name = "transport"
urlpatterns = [
    path("", views.request_list, name="list"),
    path("create/", views.edit, name="create"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.edit, name="edit"),
    path("<int:pk>/delete/", views.delete, name="delete"),
    path("<int:pk>/generate/", views.create_pdf, name="generate"),
    path("<int:pk>/pdf/<int:revision>/", views.download, name="download"),
]
