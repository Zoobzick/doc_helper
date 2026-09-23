from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("passports_app", "0005_passportsharelink")]
    operations = [
        migrations.AddField(
            model_name="passport", name="document_date_text",
            field=models.CharField(
                "Дата как в документе", max_length=32, blank=True, default="",
                help_text="Например: 07.2026г. или Июль 2026 года. Имеет приоритет над полной датой.",
            ),
        ),
    ]
