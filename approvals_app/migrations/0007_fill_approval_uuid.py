# approvals_app/migrations/0007_fill_approval_uuid.py
import uuid

from django.db import migrations


def fill_uuid(apps, schema_editor):
    Approval = apps.get_model("approvals_app", "Approval")

    # заполняем только пустые
    qs = Approval.objects.filter(uuid__isnull=True).only("id")

    for a in qs.iterator():
        a.uuid = uuid.uuid4()
        a.save(update_fields=["uuid"])


class Migration(migrations.Migration):

    dependencies = [
        ("approvals_app", "0006_approval_uuid"),
    ]

    operations = [
        migrations.RunPython(fill_uuid, migrations.RunPython.noop),
    ]