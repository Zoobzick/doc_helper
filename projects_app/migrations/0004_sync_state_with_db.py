from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Эта миграция синхронизирует СОСТОЯНИЕ Django-миграций с фактической схемой БД,
    НЕ выполняя никаких SQL-операций. Нужна потому, что в БД уже нет колонок `name`,
    но 0001_initial их создавал.
    """

    dependencies = [
        ("projects_app", "0003_alter_project_options_alter_projectrevision_options_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                # Designer: name -> full_name
                migrations.RenameField(
                    model_name="designer",
                    old_name="name",
                    new_name="full_name",
                ),

                # DesignStage: name -> full_name, удалить description, добавить is_active
                migrations.RenameField(
                    model_name="designstage",
                    old_name="name",
                    new_name="full_name",
                ),
                migrations.RemoveField(
                    model_name="designstage",
                    name="description",
                ),
                migrations.AddField(
                    model_name="designstage",
                    name="is_active",
                    field=models.BooleanField(default=True),
                ),
                migrations.AlterField(
                    model_name="designstage",
                    name="full_name",
                    field=models.CharField(max_length=255),
                ),

                # Plot: name -> full_name, удалить description
                migrations.RenameField(
                    model_name="plot",
                    old_name="name",
                    new_name="full_name",
                ),
                migrations.RemoveField(
                    model_name="plot",
                    name="description",
                ),

                # Section: удалить name (в БД его уже нет)
                migrations.RemoveField(
                    model_name="section",
                    name="name",
                ),

                # Stage: удалить name/description/order (в БД только code + is_active)
                migrations.RemoveField(model_name="stage", name="name"),
                migrations.RemoveField(model_name="stage", name="description"),
                migrations.RemoveField(model_name="stage", name="order"),

                # Project: добавить created_at (в БД он уже есть)
                migrations.AddField(
                    model_name="project",
                    name="created_at",
                    field=models.DateTimeField(auto_now_add=True),
                ),
            ],
        ),
    ]
