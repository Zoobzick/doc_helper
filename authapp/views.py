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


class ProfileView(LoginRequiredMixin, View):
    def context(self, sections):
        from django.apps import apps
        from django.conf import settings
        available = {config.label for config in apps.get_app_configs()}
        configured = {section["id"] for section in sections}
        catalog = [
            ("documents_app", "Документы", "Комплекты и сопроводительные документы", "bi-folder", "blue"),
            ("acts_app", "Акты", "Акты и исполнительная документация", "bi-file-earmark-text", "green"),
            ("passports_app", "Паспорта", "Паспорта материалов и оборудования", "bi-journal-text", "purple"),
            ("projects_app", "Проекты", "Проектная документация и ревизии", "bi-hdd-stack", "orange"),
            ("approvals_app", "Согласования", "Документы на согласовании", "bi-briefcase", "cyan"),
            ("directive_app", "Приказы", "Приказы и полномочия подписантов", "bi-person-square", "pink"),
        ]
        notifications = [
            ("Системные уведомления", "Информация о работе системы", "bi-bell", "blue"),
            ("Уведомления по документам", "Изменения документов", "bi-file-earmark-text", "green"),
            ("Уведомления по согласованиям", "Задачи и комментарии", "bi-people", "orange"),
            ("Email-уведомления", "Сообщения на электронную почту", "bi-envelope", "purple"),
        ]
        return {
            "sections": sections, "system_timezone": settings.TIME_ZONE,
            "modules": [dict(title=title, description=description, icon=icon, tone=tone) for key, title, description, icon, tone in catalog if key in available and key not in configured],
            "notification_options": [dict(title=title, description=description, icon=icon, tone=tone) for title, description, icon, tone in notifications],
        }

    def sections(self, request, selected=None):
        from django.apps import apps
        sections = []
        for config in apps.get_app_configs():
            factory = getattr(config, "get_profile_settings_form", None)
            if factory:
                form = factory(request.user, request.POST if selected == config.label else None)
                sections.append({"id": config.label, "title": config.verbose_name, "form": form})
        return sections

    def get(self, request):
        return render(request, "authapp/profile.html", self.context(self.sections(request)))

    def post(self, request):
        selected = request.POST.get("section")
        sections = self.sections(request, selected)
        for section in sections:
            if section["id"] == selected:
                if section["form"].is_valid():
                    section["form"].save()
                    messages.success(request, f'Настройки «{section["title"]}» сохранены.')
                    return redirect(reverse("authapp:profile") + "#application-settings")
                return render(request, "authapp/profile.html", self.context(sections))
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest("Неизвестный раздел настроек")


class LogoutView(View):
    def post(self, request):
        logout(request)
        messages.info(request, "Вы вышли из системы.", extra_tags="auth")
        return redirect("authapp:login")

    # чтобы не ломать существующую ссылку, оставим GET, но лучше POST
    def get(self, request):
        return self.post(request)
