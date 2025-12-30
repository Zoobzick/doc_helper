from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.generic import TemplateView


def custom_login_view(request):
    """
    Кастомная страница входа с использованием NiceAdmin шаблона
    """
    # Если пользователь уже авторизован, перенаправляем на главную
    if request.user.is_authenticated:
        return redirect('passports_list')

    # Обработка POST запроса (форма входа)
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        # Аутентификация пользователя
        user = authenticate(request, username=username, password=password)

        if user is not None:
            # Успешная аутентификация
            login(request, user)

            # Проверяем чекбокс "Remember me"
            remember = request.POST.get('remember')
            if not remember:
                # Если не выбрано "Remember me", сессия истечет при закрытии браузера
                request.session.set_expiry(0)

            messages.success(request, 'Вы успешно вошли в систему!')
            return redirect('authapp:home')
        else:
            # Неверные учетные данные
            messages.error(request, 'Неверное имя пользователя или пароль.')

    # GET запрос или неудачный POST - показываем форму входа
    return render(request, 'authapp/login.html')


class HomeView(LoginRequiredMixin, TemplateView):
    """
    Главная страница после логина.
    LoginRequiredMixin -> неавторизованных отправит на LOGIN_URL.
    """
    template_name = "authapp/home.html"


def logout_view(request):
    """
    Выход из системы
    """
    from django.contrib.auth import logout
    logout(request)
    messages.info(request, 'Вы вышли из системы.')
    return redirect('authapp:login')