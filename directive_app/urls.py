from django.urls import path
from . import views

app_name = "directive_app"

urlpatterns = [
    path("", views.DirectiveListView.as_view(), name="directive_list"),
    path("create/", views.DirectiveCreateView.as_view(), name="directive_create"),
    path("<uuid:uuid>/", views.DirectiveDetailView.as_view(), name="directive_detail"),
    path("<uuid:uuid>/edit/", views.DirectiveUpdateView.as_view(), name="directive_edit"),

    # file actions
    path("<uuid:uuid>/open/", views.DirectiveOpenView.as_view(), name="directive_open"),
    path("<uuid:uuid>/download/", views.DirectiveDownloadView.as_view(), name="directive_download"),
    path("<uuid:uuid>/share-link/", views.DirectiveShareLinkCreateView.as_view(), name="directive_share_link"),
    path("shared/<str:token>/", views.DirectiveSharedOpenView.as_view(), name="directive_shared_open"),

    # delete (POST)
    path("<uuid:uuid>/delete/", views.DirectiveDeleteView.as_view(), name="directive_delete"),

    # authorizations
    path(
        "<uuid:directive_uuid>/authorizations/create/",
        views.AuthorizationCreateView.as_view(),
        name="authorization_create",
    ),
    path("authorizations/<uuid:uuid>/edit/", views.AuthorizationUpdateView.as_view(), name="authorization_edit"),

    # Legacy routes: keep old /directives/directives/... URLs working for
    # previously shared links, bookmarks, and external integrations.
    path("directives/", views.DirectiveListView.as_view()),
    path("directives/create/", views.DirectiveCreateView.as_view()),
    path("directives/<uuid:uuid>/", views.DirectiveDetailView.as_view()),
    path("directives/<uuid:uuid>/edit/", views.DirectiveUpdateView.as_view()),
    path("directives/<uuid:uuid>/open/", views.DirectiveOpenView.as_view()),
    path("directives/<uuid:uuid>/download/", views.DirectiveDownloadView.as_view()),
    path("directives/<uuid:uuid>/share-link/", views.DirectiveShareLinkCreateView.as_view()),
    path("directives/shared/<str:token>/", views.DirectiveSharedOpenView.as_view()),
    path("directives/<uuid:uuid>/delete/", views.DirectiveDeleteView.as_view()),
    path(
        "directives/<uuid:directive_uuid>/authorizations/create/",
        views.AuthorizationCreateView.as_view(),
    ),
]
