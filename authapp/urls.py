from django.urls import path
from . import views
from .views import HomeView

app_name = "authapp"

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path('login/', views.custom_login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
]