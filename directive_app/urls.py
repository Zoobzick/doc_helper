from django.urls import path
from . import views

app_name = "directive_app"

urlpatterns = [
    path("directives/", views.DirectiveListView.as_view(), name="directive_list"),
    path("directives/create/", views.DirectiveCreateView.as_view(), name="directive_create"),
    path("directives/<uuid:uuid>/", views.DirectiveDetailView.as_view(), name="directive_detail"),
    path("directives/<uuid:uuid>/edit/", views.DirectiveUpdateView.as_view(), name="directive_edit"),

    # file actions
    path("directives/<uuid:uuid>/open/", views.DirectiveOpenView.as_view(), name="directive_open"),
    path("directives/<uuid:uuid>/download/", views.DirectiveDownloadView.as_view(), name="directive_download"),
    path("directives/<uuid:uuid>/share-link/", views.DirectiveShareLinkCreateView.as_view(), name="directive_share_link"),
    path("directives/shared/<str:token>/", views.DirectiveSharedOpenView.as_view(), name="directive_shared_open"),

    # delete (POST)
    path("directives/<uuid:uuid>/delete/", views.DirectiveDeleteView.as_view(), name="directive_delete"),

    # authorizations
    path(
        "directives/<uuid:directive_uuid>/authorizations/create/",
        views.AuthorizationCreateView.as_view(),
        name="authorization_create",
    ),
    path("authorizations/<uuid:uuid>/edit/", views.AuthorizationUpdateView.as_view(), name="authorization_edit"),
]
