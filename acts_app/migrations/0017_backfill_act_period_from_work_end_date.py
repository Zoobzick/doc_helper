from django.db import migrations


def set_period_from_work_end_date(apps, schema_editor):
    Act = apps.get_model("acts_app", "Act")
    for act in Act.objects.only("id", "act_date", "work_end_date").iterator():
        period_date = act.work_end_date or act.act_date
        Act.objects.filter(pk=act.pk).update(
            act_year=period_date.year,
            act_month=period_date.month,
        )


def set_period_from_act_date(apps, schema_editor):
    Act = apps.get_model("acts_app", "Act")
    for act in Act.objects.only("id", "act_date").iterator():
        Act.objects.filter(pk=act.pk).update(
            act_year=act.act_date.year,
            act_month=act.act_date.month,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("acts_app", "0016_act_created_by"),
    ]

    operations = [
        migrations.RunPython(set_period_from_work_end_date, set_period_from_act_date),
    ]
