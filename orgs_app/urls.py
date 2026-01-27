from __future__ import annotations

from django.urls import path

from . import views

app_name = "orgs_app"

urlpatterns = [
    # Organizations
    path("organizations/", views.OrganizationListView.as_view(), name="organization_list"),
    path("organizations/create/", views.OrganizationCreateView.as_view(), name="organization_create"),
    path("organizations/<uuid:uuid>/", views.OrganizationDetailView.as_view(), name="organization_detail"),
    path("organizations/<uuid:uuid>/edit/", views.OrganizationUpdateView.as_view(), name="organization_update"),
    path("organizations/<uuid:uuid>/delete/", views.OrganizationDeleteView.as_view(), name="organization_delete"),

    # Persons
    path("persons/", views.PersonListView.as_view(), name="person_list"),
    path("persons/create/", views.PersonCreateView.as_view(), name="person_create"),
    path("persons/<uuid:uuid>/", views.PersonDetailView.as_view(), name="person_detail"),
    path("persons/<uuid:uuid>/edit/", views.PersonUpdateView.as_view(), name="person_update"),
    path("persons/<uuid:uuid>/delete/", views.PersonDeleteView.as_view(), name="person_delete"),

    # Person NRS
    path("nrs/", views.PersonNRSListView.as_view(), name="personnrs_list"),
    path("nrs/create/", views.PersonNRSCreateView.as_view(), name="personnrs_create"),
    path("nrs/<uuid:uuid>/", views.PersonNRSDetailView.as_view(), name="personnrs_detail"),
    path("nrs/<uuid:uuid>/edit/", views.PersonNRSUpdateView.as_view(), name="personnrs_update"),
    path("nrs/<uuid:uuid>/delete/", views.PersonNRSDeleteView.as_view(), name="personnrs_delete"),
]
