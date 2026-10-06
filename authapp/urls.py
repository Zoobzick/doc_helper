from django.urls import path

from .views import HomeView, LoginView, LogoutView, RegisterView, ProfileView

app_name = "authapp"

urlpatterns = [
    path("profile/", ProfileView.as_view(), name="profile"),
    path("", HomeView.as_view(), name="home"),
    path("login/", LoginView.as_view(), name="login"),
    path("register/", RegisterView.as_view(), name="register"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
