from __future__ import annotations

from django.shortcuts import render


ERROR_CONTEXT = {
    400: {
        "title": "Запрос помялся по дороге",
        "headline": "Запрос пришел в таком виде, что сервер вежливо отступил.",
        "description": "Обновите страницу и попробуйте еще раз. Если ошибка повторится, лучше начать действие заново.",
        "image": "assets/img/errors/error-404.png",
        "tone": "warning",
        "primary_label": "На главную",
        "primary_icon": "bi-house-door",
        "primary_href": "/",
        "secondary_label": "Назад",
        "secondary_icon": "bi-arrow-left",
        "secondary_action": "back",
    },
    403: {
        "title": "Доступ не выдали",
        "headline": "Дверь закрыта, ключи у администратора.",
        "description": "У вас нет доступа к этой странице. Если это ошибка - обратитесь к администратору.",
        "image": "assets/img/errors/error-403.png",
        "tone": "warning",
        "primary_label": "На главную",
        "primary_icon": "bi-house-door",
        "primary_href": "/",
        "secondary_label": "Назад",
        "secondary_icon": "bi-arrow-left",
        "secondary_action": "back",
    },
    404: {
        "title": "Не нашли",
        "headline": "Кажется, этой страницы нет даже в параллельной вселенной.",
        "description": "Мы все проверили - пусто. Попробуйте поискать что-то другое.",
        "image": "assets/img/errors/error-404.png",
        "tone": "info",
        "primary_label": "На главную",
        "primary_icon": "bi-house-door",
        "primary_href": "/",
        "secondary_label": "Назад",
        "secondary_icon": "bi-arrow-left",
        "secondary_action": "back",
    },
    500: {
        "title": "Сервер прилег",
        "headline": "Сервер споткнулся и упал, но скоро встанет.",
        "description": "Что-то пошло не так на нашей стороне. Мы уже чиним, а вы пока попробуйте позже.",
        "image": "assets/img/errors/error-500.png",
        "tone": "danger",
        "primary_label": "Попробовать снова",
        "primary_icon": "bi-arrow-clockwise",
        "primary_action": "reload",
        "secondary_label": "На главную",
        "secondary_icon": "bi-house-door",
        "secondary_href": "/",
    },
}


def _render_error(request, status: int):
    context = {
        "status_code": status,
        **ERROR_CONTEXT[status],
    }
    return render(request, "errors/status.html", context, status=status)


def bad_request(request, exception=None):
    return _render_error(request, 400)


def permission_denied(request, exception=None):
    return _render_error(request, 403)


def page_not_found(request, exception=None):
    return _render_error(request, 404)


def server_error(request):
    return _render_error(request, 500)


def csrf_failure(request, reason=""):
    return _render_error(request, 403)
