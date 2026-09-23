from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("acts_app", "0021_act_note")]

    operations = [
        migrations.AddField(
            model_name="actparty",
            name="authorization_inherited",
            field=models.BooleanField(default=False, verbose_name="Выбор из предыдущего акта"),
        ),
    ]
