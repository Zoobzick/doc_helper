from django.db import migrations


DEFAULT_DOCUMENT_NAME = "\u0410\u043a\u0442 \u043e\u0441\u0432\u0438\u0434\u0435\u0442\u0435\u043b\u044c\u0441\u0442\u0432\u043e\u0432\u0430\u043d\u0438\u044f \u0441\u043a\u0440\u044b\u0442\u044b\u0445 \u0440\u0430\u0431\u043e\u0442"


def _column_names(connection, table_name):
    with connection.cursor() as cursor:
        return {
            column.name
            for column in connection.introspection.get_table_description(cursor, table_name)
        }


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _normalize_manual_source_schema(apps, schema_editor):
    table_name = "acts_app_aookmanualsourceact"
    connection = schema_editor.connection

    if table_name not in connection.introspection.table_names():
        return

    quote = schema_editor.quote_name
    table = quote(table_name)
    columns = _column_names(connection, table_name)

    if "document_number" not in columns and "act_number" in columns:
        schema_editor.execute(
            f"ALTER TABLE {table} RENAME COLUMN {quote('act_number')} TO {quote('document_number')}"
        )
        columns = _column_names(connection, table_name)

    if "document_date" not in columns and "act_date" in columns:
        schema_editor.execute(
            f"ALTER TABLE {table} RENAME COLUMN {quote('act_date')} TO {quote('document_date')}"
        )
        columns = _column_names(connection, table_name)

    if "document_name" not in columns:
        schema_editor.execute(
            f"ALTER TABLE {table} ADD COLUMN {quote('document_name')} "
            f"varchar(512) NOT NULL DEFAULT {_literal(DEFAULT_DOCUMENT_NAME)}"
        )
        columns = _column_names(connection, table_name)

    if "document_number" not in columns:
        schema_editor.execute(
            f"ALTER TABLE {table} ADD COLUMN {quote('document_number')} varchar(255) NOT NULL DEFAULT ''"
        )
        columns = _column_names(connection, table_name)

    if "document_date" not in columns:
        schema_editor.execute(
            f"ALTER TABLE {table} ADD COLUMN {quote('document_date')} date NULL"
        )
        columns = _column_names(connection, table_name)

    if "organization_name" not in columns:
        schema_editor.execute(
            f"ALTER TABLE {table} ADD COLUMN {quote('organization_name')} varchar(255) NOT NULL DEFAULT ''"
        )

    schema_editor.execute(
        f"UPDATE {table} SET {quote('document_name')} = {_literal(DEFAULT_DOCUMENT_NAME)} "
        f"WHERE {quote('document_name')} IS NULL OR {quote('document_name')} = ''"
    )

    columns = _column_names(connection, table_name)
    for old_column in ("act_number", "act_date", "work_name", "sheets_count"):
        if old_column in columns:
            schema_editor.execute(
                f"ALTER TABLE {table} DROP COLUMN {quote(old_column)}"
            )


class Migration(migrations.Migration):

    dependencies = [
        ("acts_app", "0019_aookmanualsourceact"),
    ]

    operations = [
        migrations.RunPython(_normalize_manual_source_schema, migrations.RunPython.noop),
    ]
