# Generated manually to allow long approval file paths.

import approvals_app.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("approvals_app", "0008_alter_approval_uuid"),
    ]

    operations = [
        migrations.AlterField(
            model_name="approval",
            name="file",
            field=models.FileField(
                max_length=500,
                storage=approvals_app.models.approvals_storage,
                upload_to=approvals_app.models.approval_upload_to,
                verbose_name="PDF файл",
            ),
        ),
    ]
