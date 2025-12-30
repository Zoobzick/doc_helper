# documents_app/models.py
from django.db import models


class DocumentsPermissions(models.Model):
    """
    Техническая модель: нужна только для хранения кастомных permissions приложения.
    """
    class Meta:
        managed = False  # таблица в БД не создаётся
        default_permissions = ()  # стандартные add/change/delete/view не нужны
        permissions = [
            ("view_box_label_page", "Can view box label page"),
        ]
