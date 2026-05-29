from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("documents_app", "0004_alter_generateddocument_file"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="documentbatchproject",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Проверен"),
        ),
        migrations.AddField(
            model_name="documentbatchproject",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reviewed_document_batch_projects",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Проверил",
            ),
        ),
    ]
