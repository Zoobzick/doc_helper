# Generated manually for AOOK support.

from django.conf import settings
from django.db import migrations, models
import acts_app.models
import django.core.validators
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects_app", "0008_alter_plot_code_alter_stage_code"),
        ("acts_app", "0017_backfill_act_period_from_work_end_date"),
    ]

    operations = [
        migrations.CreateModel(
            name="Aook",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uuid", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True, verbose_name="UUID")),
                ("number", models.CharField(max_length=64, verbose_name="№ АООК")),
                ("act_date", models.DateField(verbose_name="Дата АООК")),
                ("work_name", models.CharField(max_length=512, verbose_name="Наименование работ")),
                ("work_start_date", models.DateField(blank=True, null=True, verbose_name="Дата начала работ")),
                ("work_end_date", models.DateField(blank=True, null=True, verbose_name="Дата окончания работ")),
                ("work_norms_text", models.TextField(blank=True, default="", verbose_name="Работы выполнены в соответствии с")),
                ("copies_count", models.PositiveSmallIntegerField(default=3, validators=[django.core.validators.MinValueValidator(1)], verbose_name="Акт составлен в (экземплярах)")),
                ("aosr_registry_number", models.CharField(blank=True, default="", max_length=64, verbose_name="№ реестра АОСР")),
                ("protocols_registry_number", models.CharField(blank=True, default="", max_length=64, verbose_name="№ реестра протоколов")),
                ("xlsx_file", models.FileField(blank=True, null=True, upload_to=acts_app.models.aook_file_upload_to, verbose_name="XLSX")),
                ("pdf_file", models.FileField(blank=True, null=True, upload_to=acts_app.models.aook_file_upload_to, verbose_name="PDF")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создан")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлён")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_aooks", to=settings.AUTH_USER_MODEL, verbose_name="Кто делал")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="aooks", to="projects_app.project", verbose_name="Шифр проекта")),
            ],
            options={
                "verbose_name": "АООК",
                "verbose_name_plural": "АООК",
                "ordering": ("-act_date", "number"),
            },
        ),
        migrations.CreateModel(
            name="AookSourceAct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)], verbose_name="Позиция")),
                ("act", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="aook_source_items", to="acts_app.act")),
                ("aook", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="source_act_items", to="acts_app.aook")),
            ],
            options={
                "verbose_name": "АОСР в АООК",
                "verbose_name_plural": "АОСР в АООК",
                "ordering": ("position", "id"),
            },
        ),
        migrations.CreateModel(
            name="AookProtocolItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)], verbose_name="Позиция")),
                ("document_name", models.CharField(max_length=512, verbose_name="Наименование документа")),
                ("document_number", models.CharField(blank=True, default="", max_length=255, verbose_name="№")),
                ("document_date", models.DateField(blank=True, null=True, verbose_name="Дата")),
                ("organization_name", models.CharField(blank=True, default="", max_length=255, verbose_name="Организация")),
                ("sheets_count", models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)], verbose_name="Листов")),
                ("aook", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="protocol_items", to="acts_app.aook")),
            ],
            options={
                "verbose_name": "Протокол АООК",
                "verbose_name_plural": "Протоколы АООК",
                "ordering": ("position", "id"),
            },
        ),
        migrations.AddIndex(
            model_name="aook",
            index=models.Index(fields=["project", "act_date"], name="aook_project_date_idx"),
        ),
        migrations.AddIndex(
            model_name="aooksourceact",
            index=models.Index(fields=["aook", "position"], name="aooksrc_aook_pos_idx"),
        ),
        migrations.AddIndex(
            model_name="aooksourceact",
            index=models.Index(fields=["act"], name="aooksrc_act_idx"),
        ),
        migrations.AddIndex(
            model_name="aookprotocolitem",
            index=models.Index(fields=["aook", "position"], name="aookprot_aook_pos_idx"),
        ),
        migrations.AddConstraint(
            model_name="aooksourceact",
            constraint=models.UniqueConstraint(fields=("aook", "act"), name="uniq_aook_source_act"),
        ),
        migrations.AddConstraint(
            model_name="aooksourceact",
            constraint=models.UniqueConstraint(fields=("aook", "position"), name="uniq_aook_source_pos"),
        ),
        migrations.AddConstraint(
            model_name="aookprotocolitem",
            constraint=models.UniqueConstraint(fields=("aook", "position"), name="uniq_aook_protocol_pos"),
        ),
    ]
