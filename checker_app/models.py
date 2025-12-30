from django.db import models


class CheckerPermissions(models.Model):
    """
    Пустая модель нужна только чтобы завести кастомные permissions для checker_app,
    не привязываясь к CRUD реальных моделей.
    Записей в БД создавать не нужно.
    """

    class Meta:
        managed = False  # таблица в БД не создаётся
        default_permissions = ()  # отключаем add/change/delete/view для этой модели
        verbose_name = "Права Checker"
        verbose_name_plural = "Права Checker"

        permissions = [
            ("view_checker_section", "Может видеть раздел Checker в меню"),
            ("check_projects_and_folder_page", "Может открывать страницу проверки шифра (путь и проект)"),
            ("check_doc_acts_page", "Может открывать страницу проверки актов .doc"),
            ("view_doc_structure", "Can view doc structure pages"),
            ("check_directive_checker_page", "Can open directive checker page"),
        ]
