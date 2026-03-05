from django.db import migrations


def forwards(apps, schema_editor):
    ActAttachment = apps.get_model("acts_app", "ActAttachment")


    qs = ActAttachment.objects.all().only("id", "is_protocol", "is_original")

    for a in qs.iterator():
        if getattr(a, "is_protocol", False):
            a.original_state = 1 if getattr(a, "is_original", False) else 0
        else:
            a.original_state = 2
        a.save(update_fields=["original_state"])


def backwards(apps, schema_editor):
    # обратную миграцию не делаем (можно, но не нужно)
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("acts_app", "0013_actattachment_original_state"),  # <-- миграция №1
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="actattachment", name="is_protocol"),
        migrations.RemoveField(model_name="actattachment", name="is_original"),
    ]