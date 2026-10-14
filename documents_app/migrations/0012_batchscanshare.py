import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("documents_app", "0011_batchscan"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BatchScanShare",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("summary", models.JSONField(default=dict)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="scan_shares", to="documents_app.documentbatch")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("scans", models.ManyToManyField(related_name="shares", to="documents_app.batchscan")),
            ],
        ),
    ]
