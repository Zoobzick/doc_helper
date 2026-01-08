from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from .forms import LoginForm, RegisterForm


def _auth_messages_only(request):
    """
    На странице логина/регистрации показываем только сообщения, относящиеся к authapp,
    чтобы не вываливались "сотни" сообщений из других модулей.

    Одновременно сообщения считаются прочитанными (Django messages storage).
    """
    out = []
    for m in messages.get_messages(request):
        extra = (getattr(m, "extra_tags", "") or "").split()
        if "auth" in extra:
            out.append(m)
    return out


class LoginView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect("authapp:home")
        form = LoginForm()
        auth_messages = _auth_messages_only(request)
        return render(request, "authapp/login.html", {"form": form, "auth_messages": auth_messages})

    def post(self, request):
        if request.user.is_authenticated:
            return redirect("authapp:home")

        form = LoginForm(request.POST)
        if not form.is_valid():
            auth_messages = _auth_messages_only(request)
            return render(request, "authapp/login.html", {"form": form, "auth_messages": auth_messages})

        email = form.cleaned_data["email"].strip().lower()
        password = form.cleaned_data["password"]
        remember = form.cleaned_data["remember"]

        User = get_user_model()
        u = User.objects.filter(email=email).first()
        if u and not u.is_active:
            messages.error(request, "Доступ ещё не выдан администратором.", extra_tags="auth")
            return redirect("authapp:login")

        user = authenticate(request, email=email, password=password)
        if user is None:
            messages.error(request, "Неверный email или пароль.", extra_tags="auth")
            return redirect("authapp:login")

        login(request, user)
        if not remember:
            request.session.set_expiry(0)

        messages.success(request, "Вы успешно вошли в систему!", extra_tags="auth")
        return redirect("authapp:home")


class RegisterView(View):
    """
    Регистрация создаёт пользователя, но НЕ даёт вход, пока is_active=False.
    SU потом включает is_active в админке.
    """
    def get(self, request):
        if request.user.is_authenticated:
            return redirect("authapp:home")
        form = RegisterForm()
        auth_messages = _auth_messages_only(request)
        return render(request, "authapp/register.html", {"form": form, "auth_messages": auth_messages})

    def post(self, request):
        if request.user.is_authenticated:
            return redirect("authapp:home")

        form = RegisterForm(request.POST)
        if not form.is_valid():
            auth_messages = _auth_messages_only(request)
            return render(request, "authapp/register.html", {"form": form, "auth_messages": auth_messages})

        User = get_user_model()
        email = form.cleaned_data["email"]
        password = form.cleaned_data["password1"]

        # username оставляем техническим: равен email
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            first_name=form.cleaned_data["first_name"].strip(),
            last_name=form.cleaned_data["last_name"].strip(),
            is_active=False,
        )

        messages.success(
            request,
            "Аккаунт создан. Ожидайте подтверждения доступа администратором.",
            extra_tags="auth",
        )
        return redirect("authapp:login")


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "authapp/home.html"


class LogoutView(View):
    def post(self, request):
        logout(request)
        messages.info(request, "Вы вышли из системы.", extra_tags="auth")
        return redirect("authapp:login")

    # чтобы не ломать существующую ссылку, оставим GET, но лучше POST
    def get(self, request):
        return self.post(request)
