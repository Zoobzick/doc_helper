from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("acts_app", "0022_actparty_authorization_inherited")]

    operations = [
        migrations.AlterField(
            model_name="act",
            name="status",
            field=models.CharField(
                choices=[("DRAFT", "Черновик"), ("READY", "Создан"), ("FINAL", "Финальный")],
                default="READY", db_index=True, max_length=16, verbose_name="Статус",
            ),
        ),
    ]
