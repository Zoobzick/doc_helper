import django.conf
import django.db.models.deletion
from django.db import migrations, models

import passports_app.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(django.conf.settings.AUTH_USER_MODEL),
        ("passports_app", "0004_passport_sheets_count"),
    ]

    operations = [
        migrations.CreateModel(
            name="PassportShareLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "token",
                    models.CharField(
                        db_index=True,
                        default=passports_app.models.generate_passport_share_token,
                        max_length=96,
                        unique=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("revoked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("access_count", models.PositiveIntegerField(default=0)),
                ("last_accessed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_passport_share_links",
                        to=django.conf.settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "passport",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="share_links",
                        to="passports_app.passport",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="passportsharelink",
            index=models.Index(fields=["token", "expires_at"], name="passports_a_token_f58211_idx"),
        ),
        migrations.AddIndex(
            model_name="passportsharelink",
            index=models.Index(fields=["passport", "expires_at"], name="passports_a_passpor_098a38_idx"),
        ),
    ]
