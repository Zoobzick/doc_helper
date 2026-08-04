import django.conf
import django.db.models.deletion
from django.db import migrations, models

import directive_app.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(django.conf.settings.AUTH_USER_MODEL),
        ("directive_app", "0005_alter_directive_pdf_file"),
    ]

    operations = [
        migrations.CreateModel(
            name="DirectiveShareLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "token",
                    models.CharField(
                        db_index=True,
                        default=directive_app.models.generate_directive_share_token,
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
                        related_name="created_directive_share_links",
                        to=django.conf.settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "directive",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="share_links",
                        to="directive_app.directive",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="directivesharelink",
            index=models.Index(fields=["token", "expires_at"], name="directive_a_token_1f844a_idx"),
        ),
        migrations.AddIndex(
            model_name="directivesharelink",
            index=models.Index(fields=["directive", "expires_at"], name="directive_a_directi_e3f41d_idx"),
        ),
    ]
