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

try:
    import PyPDF2  # noqa: F401
except ImportError:
    pypdf2_module = types.ModuleType("PyPDF2")

    class PdfReader:  # pragma: no cover - only for minimal local test env
        def __init__(self, *args, **kwargs):
            self.pages = []

    pypdf2_module.PdfReader = PdfReader
    sys.modules["PyPDF2"] = pypdf2_module

from acts_app.services.registry_p3_docx_generator import _collect_material_rows_for_registry
from acts_app.services.registry_sheet_counter import physical_sheets_for_pages


class _FakeMaterials:
    def __init__(self, items):
        self.items = items

    def select_related(self, *args):
        return self

    def order_by(self, *args):
        return self.items


class RegistryP3RowsTests(SimpleTestCase):
    def test_physical_sheets_are_counted_for_double_sided_printing(self):
        self.assertEqual(physical_sheets_for_pages(1), 1)
        self.assertEqual(physical_sheets_for_pages(2), 1)
        self.assertEqual(physical_sheets_for_pages(3), 2)
        self.assertEqual(physical_sheets_for_pages(6), 3)

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

    def test_private_material_documents_stay_grouped_before_shared_document(self):
        arm36_shared = SimpleNamespace(
            manual_name="36 А500С",
            manual_doc_name="Сертификат качества",
            manual_doc_no="1",
            manual_doc_date_text="01.12.2025",
            manual_doc_date=None,
            passport_id=None,
            sheets_count=1,
            concrete_volume_m3=None,
        )
        arm36_private_2 = SimpleNamespace(
            manual_name="36 А500С",
            manual_doc_name="Сертификат качества",
            manual_doc_no="2",
            manual_doc_date_text="02.12.2025",
            manual_doc_date=None,
            passport_id=None,
            sheets_count=1,
            concrete_volume_m3=None,
        )
        arm36_private_3 = SimpleNamespace(
            manual_name="36 А500С",
            manual_doc_name="Сертификат качества",
            manual_doc_no="3",
            manual_doc_date_text="03.12.2025",
            manual_doc_date=None,
            passport_id=None,
            sheets_count=1,
            concrete_volume_m3=None,
        )
        arm12_shared = SimpleNamespace(
            manual_name="12 А500С",
            manual_doc_name="Сертификат качества",
            manual_doc_no="1",
            manual_doc_date_text="01.12.2025",
            manual_doc_date=None,
            passport_id=None,
            sheets_count=1,
            concrete_volume_m3=None,
        )
        act = SimpleNamespace(
            materials=_FakeMaterials(
                [arm36_shared, arm36_private_2, arm36_private_3, arm12_shared]
            )
        )

        rows = _collect_material_rows_for_registry(act)

        self.assertEqual(
            [(row.material_name, row.doc_no) for row in rows],
            [("36 А500С", "2"), ("36 А500С", "3"), ("36 А500С", "1"), ("12 А500С", "1")],
        )
        self.assertEqual(rows[0].material_merge_key, rows[1].material_merge_key)
        self.assertNotEqual(rows[1].material_merge_key, rows[2].material_merge_key)
        self.assertEqual(rows[2].doc_key, rows[3].doc_key)
