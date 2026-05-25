from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import documents_app.models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents_app", "0006_project_review_status_and_act_notes"),
    ]

    operations = [
        migrations.CreateModel(
            name="BatchAttachment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "uuid",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                        verbose_name="UUID",
                    ),
                ),
                (
                    "attachment_type",
                    models.CharField(
                        choices=[
                            ("stamped_letter_pdf", "Письмо с отметкой PDF"),
                            ("marksurvey_pdf", "Маркзамер PDF"),
                        ],
                        max_length=30,
                        verbose_name="Тип вложения",
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        max_length=500,
                        upload_to=documents_app.models.batch_attachment_upload_to,
                        verbose_name="Файл",
                    ),
                ),
                (
                    "original_name",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name="Исходное имя файла",
                    ),
                ),
                (
                    "uploaded_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Дата загрузки",
                    ),
                ),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="documents_app.documentbatch",
                        verbose_name="Комплект",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="uploaded_batch_attachments",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Загрузил",
                    ),
                ),
            ],
            options={
                "verbose_name": "Пользовательское вложение комплекта",
                "verbose_name_plural": "Пользовательские вложения комплектов",
                "ordering": ("attachment_type", "-uploaded_at", "-id"),
            },
        ),
        migrations.AddConstraint(
            model_name="batchattachment",
            constraint=models.UniqueConstraint(
                condition=models.Q(attachment_type="stamped_letter_pdf"),
                fields=("batch", "attachment_type"),
                name="uniq_batch_single_stamped_letter_attachment",
            ),
        ),
    ]
