from django.urls import include, path


urlpatterns = [
    path("projects/", include(("projects_app.urls", "projects"), namespace="projects")),
]
