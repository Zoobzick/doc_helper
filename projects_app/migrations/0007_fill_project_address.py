from __future__ import annotations

from django.db import migrations

DEFAULT_ADDR = "Московская область, г. Красногорск, ул. Липовая роща д.2к2."


def forwards(apps, schema_editor):
    Project = apps.get_model("projects_app", "Project")

    # 1) если вдруг где-то NULL (на всякий)
    Project.objects.filter(address__isnull=True).update(address="")

    # 2) заполняем пустые/пробельные
    # (strip в БД не сделаем, но пустые строки покроем)
    Project.objects.filter(address="").update(address=DEFAULT_ADDR)


def backwards(apps, schema_editor):
    # Обратный ход обычно не нужен, но пусть будет аккуратный:
    Project = apps.get_model("projects_app", "Project")
    Project.objects.filter(address=DEFAULT_ADDR).update(address="")


class Migration(migrations.Migration):
    dependencies = [
        ("projects_app", "0006_project_address"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
