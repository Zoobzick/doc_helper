from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents_app", "0008_batchattachment"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentbatch",
            name="excluded_project_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="ID проектов, вручную удалённых из автоматически собираемого комплекта.",
                verbose_name="Исключённые проекты",
            ),
        ),
    ]
