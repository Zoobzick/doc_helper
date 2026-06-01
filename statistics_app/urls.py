from django.urls import path

from statistics_app import views


app_name = "statistics_app"

urlpatterns = [
    path("", views.StatisticsDashboardView.as_view(), name="dashboard"),
]
