import os

from django.conf import settings
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
    data = {"status": "ok", "django": "ok", "db": "ok", "storage": "ok"}


    try:
        connection.ensure_connection()
    except OperationalError as e:
        data["status"] = "error"
        data["db"] = "error"
        data["db_error"] = str(e)

    base_dir = str(getattr(settings, "BASE_ID_DIR", ""))
    if not base_dir:
        data["status"] = "error"
        data["storage"] = "error"
        data["storage_error"] = "BASE_ID_DIR is not set"
    else:
        if not os.path.isdir(base_dir):
            data["status"] = "error"
            data["storage"] = "error"
            data["storage_error"] = f"BASE_ID_DIR does not exist or not a directory: {base_dir}"
        elif not os.access(base_dir, os.W_OK):
            data["status"] = "error"
            data["storage"] = "error"
            data["storage_error"] = f"BASE_ID_DIR is not writable: {base_dir}"

    return JsonResponse(data, status=200 if data["status"] == "ok" else 503)
