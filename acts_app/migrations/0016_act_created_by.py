from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("acts_app", "0015_alter_act_act_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="act",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_acts",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Кто делал",
            ),
        ),
    ]
