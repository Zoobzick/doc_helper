# Generated manually for AOOK manual registry rows.

from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("acts_app", "0018_aook_aooksourceact_aookprotocolitem"),
    ]

    operations = [
        migrations.CreateModel(
            name="AookManualSourceAct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)], verbose_name="Позиция")),
                ("act_number", models.CharField(max_length=255, verbose_name="№ акта")),
                ("act_date", models.DateField(blank=True, null=True, verbose_name="Дата акта")),
                ("work_name", models.CharField(blank=True, default="", max_length=512, verbose_name="Наименование работ")),
                ("organization_name", models.CharField(blank=True, default="", max_length=255, verbose_name="Организация")),
                ("sheets_count", models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)], verbose_name="Листов")),
                ("aook", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="manual_source_act_items", to="acts_app.aook")),
            ],
            options={
                "verbose_name": "Ручная строка АОСР в АООК",
                "verbose_name_plural": "Ручные строки АОСР в АООК",
                "ordering": ("position", "id"),
            },
        ),
        migrations.AddIndex(
            model_name="aookmanualsourceact",
            index=models.Index(fields=["aook", "position"], name="aookmansrc_aook_pos_idx"),
        ),
        migrations.AddConstraint(
            model_name="aookmanualsourceact",
            constraint=models.UniqueConstraint(fields=("aook", "position"), name="uniq_aook_manual_source_pos"),
        ),
    ]
