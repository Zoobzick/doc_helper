from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("documents_app", "0005_documentbatchproject_reviewed"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="documentbatchproject",
            name="review_status",
            field=models.CharField(
                choices=[
                    ("pending", "Ожидает проверки"),
                    ("in_progress", "Проверяется"),
                    ("reviewed", "Проверен"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
                verbose_name="Статус проверки",
            ),
        ),
        migrations.AddField(
            model_name="documentbatchproject",
            name="review_started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Проверка начата"),
        ),
        migrations.AddField(
            model_name="documentbatchproject",
            name="review_started_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="started_document_batch_project_reviews",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Проверяет",
            ),
        ),
        migrations.CreateModel(
            name="DocumentBatchActReviewNote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name="UUID")),
                ("text", models.TextField(verbose_name="Замечание")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("is_resolved", models.BooleanField(default=False, verbose_name="Исправлено")),
                ("resolved_at", models.DateTimeField(blank=True, null=True, verbose_name="Исправлено")),
                (
                    "batch_act",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_notes",
                        to="documents_app.documentbatchact",
                        verbose_name="Акт комплекта",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_document_batch_act_review_notes",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Автор",
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="resolved_document_batch_act_review_notes",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Исправил",
                    ),
                ),
            ],
            options={
                "verbose_name": "Замечание к акту комплекта",
                "verbose_name_plural": "Замечания к актам комплекта",
                "ordering": ("created_at", "id"),
            },
        ),
        migrations.AddIndex(
            model_name="documentbatchactreviewnote",
            index=models.Index(fields=["batch_act", "is_resolved"], name="documents_a_batch_a_f6d0ec_idx"),
        ),
    ]
