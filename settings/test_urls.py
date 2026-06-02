from django.urls import include, path


urlpatterns = [
    path("", include(("authapp.urls", "authapp"), namespace="authapp")),
    path("projects/", include(("projects_app.urls", "projects"), namespace="projects")),
]
