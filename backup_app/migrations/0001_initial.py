from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BackupRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "trigger",
                    models.CharField(
                        choices=[
                            ("MANUAL", "Вручную"),
                            ("DEPLOY", "Перед деплоем"),
                            ("SCHEDULED", "По расписанию"),
                        ],
                        default="MANUAL",
                        max_length=16,
                        verbose_name="Тип запуска",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("RUNNING", "Выполняется"),
                            ("SUCCESS", "Успешно"),
                            ("FAILED", "Ошибка"),
                        ],
                        db_index=True,
                        default="RUNNING",
                        max_length=16,
                        verbose_name="Статус",
                    ),
                ),
                ("reason", models.CharField(blank=True, default="", max_length=255, verbose_name="Причина")),
                ("file_path", models.CharField(blank=True, default="", max_length=1024, verbose_name="Файл")),
                ("size_bytes", models.PositiveBigIntegerField(default=0, verbose_name="Размер")),
                ("error", models.TextField(blank=True, default="", verbose_name="Ошибка")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создан")),
                ("completed_at", models.DateTimeField(blank=True, null=True, verbose_name="Завершён")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="backup_runs",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кто запустил",
                    ),
                ),
            ],
            options={
                "verbose_name": "Бэкап",
                "verbose_name_plural": "Бэкапы",
                "ordering": ("-created_at", "-id"),
            },
        ),
    ]
