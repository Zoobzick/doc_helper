from django.http import JsonResponse
from django.db import connection
from django.db.utils import OperationalError


def health_check(request):
    """
    /health/
    Возвращает понятный статус:
    - django: ok всегда, если дошли до view
    - db: ok/error + текст ошибки
    HTTP 200 если всё ок, иначе 503 (чтобы мониторинг видел проблему)
    """
    data = {"status": "ok", "django": "ok", "db": "ok"}

    try:
        connection.ensure_connection()
    except OperationalError as e:
        data["status"] = "error"
        data["db"] = "error"
        data["db_error"] = str(e)

    return JsonResponse(data, status=200 if data["status"] == "ok" else 503)
