from django.db import migrations


def mark_existing_acts_ready(apps, schema_editor):
    Act = apps.get_model("acts_app", "Act")
    Act.objects.using(schema_editor.connection.alias).filter(status="DRAFT").update(status="READY")


class Migration(migrations.Migration):
    dependencies = [("acts_app", "0023_act_ready_status")]

    operations = [
        migrations.RunPython(mark_existing_acts_ready, migrations.RunPython.noop),
    ]
