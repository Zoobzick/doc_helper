"""
URL configuration for settings project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include(("authapp.urls", "authapp"), namespace="authapp")),
    path('passports/', include('passports_app.urls')),
    path('checker/', include('checker_app.urls')),
    path('directives/', include('directive_app.urls')),
    path("projects/", include(("projects_app.urls", "projects"), namespace="projects")),
    path("approvals/", include(("approvals_app.urls", "approvals"), namespace="approvals")),
    path("documents/", include(('documents_app.urls', 'documents'), namespace='documents')),
    path("", include("health_app.urls")),
]
