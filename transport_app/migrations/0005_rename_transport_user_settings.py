from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("transport_app", "0004_transportdefaults_skip_general")]

    operations = [
        migrations.RenameModel(
            old_name="TransportDefaults",
            new_name="TransportUserSettings",
        ),
    ]
