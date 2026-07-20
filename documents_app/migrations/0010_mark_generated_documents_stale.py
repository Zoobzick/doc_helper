from django.db import migrations


def mark_generated_documents_stale(apps, schema_editor):
    GeneratedDocument = apps.get_model("documents_app", "GeneratedDocument")
    GeneratedDocument.objects.filter(
        document_type__in=[
            "registry_xlsx",
            "registry_preview_pdf",
            "letter_docx",
            "letter_preview_pdf",
        ]
    ).update(is_actual=False)


class Migration(migrations.Migration):
    dependencies = [
        ("documents_app", "0009_documentbatch_excluded_project_ids"),
    ]

    operations = [
        migrations.RunPython(
            mark_generated_documents_stale,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
