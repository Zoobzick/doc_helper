from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("acts_app", "0020_normalize_aook_manual_source_act"),
    ]

    operations = [
        migrations.AddField(
            model_name="act",
            name="note",
            field=models.TextField(blank=True, default="", verbose_name="Примечание"),
        ),
    ]
