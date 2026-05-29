import sys
import types
from types import SimpleNamespace

from django.test import SimpleTestCase

try:
    import docx  # noqa: F401
except ImportError:
    docx_module = types.ModuleType("docx")
    docx_module.Document = lambda *args, **kwargs: None
    docx_enum_module = types.ModuleType("docx.enum")
    docx_enum_table_module = types.ModuleType("docx.enum.table")
    docx_enum_text_module = types.ModuleType("docx.enum.text")
    docx_enum_table_module.WD_CELL_VERTICAL_ALIGNMENT = SimpleNamespace(CENTER="CENTER")
    docx_enum_text_module.WD_ALIGN_PARAGRAPH = SimpleNamespace(CENTER="CENTER")
    sys.modules["docx"] = docx_module
    sys.modules["docx.enum"] = docx_enum_module
    sys.modules["docx.enum.table"] = docx_enum_table_module
    sys.modules["docx.enum.text"] = docx_enum_text_module

    act_docx_generator = types.ModuleType("acts_app.services.act_docx_generator")

    class DocxRenderError(RuntimeError):
        pass

    act_docx_generator.DocxRenderError = DocxRenderError
    act_docx_generator.get_act_docx_paths = lambda act: []
    act_docx_generator.replace_tokens = lambda doc, mapping: None
    sys.modules["acts_app.services.act_docx_generator"] = act_docx_generator

from acts_app.services.registry_p3_docx_generator import _collect_material_rows_for_registry


class _FakeMaterials:
    def __init__(self, items):
        self.items = items

    def select_related(self, *args):
        return self

    def order_by(self, *args):
        return self.items


class RegistryP3RowsTests(SimpleTestCase):
    def test_materials_with_same_document_are_grouped_together(self):
        shared_document_first = SimpleNamespace(
            manual_name="Арматура 10 А240",
            manual_doc_name="Паспорт качества",
            manual_doc_no="№1234",
            manual_doc_date_text="11.02.2024",
            manual_doc_date=None,
            passport_id=None,
            sheets_count=1,
            concrete_volume_m3=None,
        )
        other_document = SimpleNamespace(
            manual_name="Арматура 20 А500",
            manual_doc_name="Паспорт качества",
            manual_doc_no="9876",
            manual_doc_date_text="12.02.2024",
            manual_doc_date=None,
            passport_id=None,
            sheets_count=1,
            concrete_volume_m3=None,
        )
        shared_document_second = SimpleNamespace(
            manual_name="Арматура 12 А240",
            manual_doc_name="Паспорт качества",
            manual_doc_no="1234",
            manual_doc_date_text="11.02.2024",
            manual_doc_date=None,
            passport_id=None,
            sheets_count=1,
            concrete_volume_m3=None,
        )
        act = SimpleNamespace(
            materials=_FakeMaterials(
                [shared_document_first, other_document, shared_document_second]
            )
        )

        rows = _collect_material_rows_for_registry(act)

        self.assertEqual(
            [row.material_name for row in rows],
            ["Арматура 10 А240", "Арматура 12 А240", "Арматура 20 А500"],
        )
        self.assertEqual(rows[0].doc_key, rows[1].doc_key)
        self.assertNotEqual(rows[1].doc_key, rows[2].doc_key)
