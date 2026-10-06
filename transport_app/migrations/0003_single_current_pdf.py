from django.db import migrations, models
from django.db.models import F


def remove_old_pdfs(apps, schema_editor):
    pdfs = apps.get_model("transport_app", "TransportPDF").objects.using(schema_editor.connection.alias)
    pdfs.exclude(revision=F("request__revision")).delete()


class Migration(migrations.Migration):
    dependencies = [("transport_app", "0002_remove_transportrequest_status")]
    operations = [
        migrations.RunPython(remove_old_pdfs, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="transportpdf", name="transport_pdf_revision"),
        migrations.AddConstraint(model_name="transportpdf", constraint=models.UniqueConstraint(fields=("request",), name="transport_single_pdf")),
    ]
