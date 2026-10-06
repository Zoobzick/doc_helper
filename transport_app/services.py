from copy import deepcopy
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from django.conf import settings
from lxml import etree

from documents_app.utils.pdf_utils import convert_docx_to_pdf


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{" + NS["w"] + "}"
TOKEN = re.compile(r"\{\{[^{}]+\}\}")
TEMPLATE_NAME = "zayavka_transport2_template.docx"


def _text(element):
    return "".join(element.itertext()) if element.tag == W + "t" else "".join(element.xpath('.//w:t/text()', namespaces=NS))


def _replace_tokens(element, values):
    """Replace text only, even when Word splits a token across formatted runs."""
    for paragraph in element.iter(W + "p"):
        nodes = list(paragraph.iter(W + "t"))
        original = "".join(node.text or "" for node in nodes)
        offsets, offset = [], 0
        for node in nodes:
            offsets.append((offset, offset + len(node.text or ""), node))
            offset += len(node.text or "")
        for match in reversed(list(TOKEN.finditer(original))):
            if match.group() not in values:
                continue
            touched = [(a, b, node) for a, b, node in offsets if a < match.end() and b > match.start()]
            for index, (a, b, node) in enumerate(touched):
                start, end = max(0, match.start() - a), min(b - a, match.end() - a)
                replacement = str(values[match.group()]) if index == 0 else ""
                node.text = (node.text or "")[:start] + replacement + (node.text or "")[end:]
                node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        # A newline inside w:t is not a Word line break. Keep the same run/rPr.
        for node in nodes:
            if "\n" not in (node.text or ""):
                continue
            lines = node.text.split("\n")
            node.text = lines[0]
            cursor = node
            for line in lines[1:]:
                br = etree.Element(W + "br")
                cursor.addnext(br)
                text = etree.Element(W + "t")
                text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                text.text = line
                br.addnext(text)
                cursor = text


def _signer(value):
    # The current UI stores “position, name”; retain entries without a comma.
    position, separator, name = value.rpartition(",")
    return (position.strip(), name.strip()) if separator else ("", value.strip())


def build_docx(request, destination):
    """Fill the retained package; never reconstruct tables or their formatting."""
    template = Path(settings.BASE_DIR) / "document_templates" / "docx" / TEMPLATE_NAME
    author_position, author_name = _signer(request.author)
    reviewer_position, reviewer_name = _signer(request.reviewer)
    approver_position, approver_name = _signer(request.approver)
    values = {
        "{{org_full}}": request.organization, "{{uchastok}}": request.site_display,
        "{{phone_number}}": request.phone, "{{utverzhd_fio}}": request.approver,
        "{{data_zayavki}}": request.date.strftime('"%d" %m %Yг.'),
        "{{sostavil_dolzh}}": author_position, "{{sostavil_fio}}": author_name,
        "{{soglasoval_dolzh}}": reviewer_position, "{{soglasoval_fio}}": reviewer_name,
        "{{request_number}}": request.pk, "{{place}}": request.place_display,
        "{{ploshadka}}": request.place_display,
        "{{utv_dolzhnost}}": approver_position, "{{utv_fio}}": approver_name,
    }
    row_tokens = {"{{№}}", "{{technique}}", "{{technique_dates}}", "{{count}}", "{{job}}", "{{comment}}"}
    with ZipFile(template) as source:
        root = etree.fromstring(source.read("word/document.xml"))
        rows = [row for row in root.iter(W + "tr") if "{{technique}}" in _text(row)]
        if len(rows) != 1 or not row_tokens.issubset(set(TOKEN.findall(_text(rows[0])))):
            raise RuntimeError("В шаблоне должна быть одна строка со всеми токенами транспорта.")
        unknown = set(TOKEN.findall(_text(root))) - set(values) - row_tokens
        if unknown:
            raise RuntimeError("Неизвестные токены шаблона: " + ", ".join(sorted(unknown)))
        prototype = rows[0]
        table = prototype.getparent()
        insertion = table.index(prototype)
        # Remove only the prototype's contiguous empty data rows, not headers/footer.
        following = prototype.getnext()
        while following is not None and following.tag == W + "tr" and not _text(following).strip() and not following.xpath('.//w:drawing | .//w:pict', namespaces=NS):
            next_row = following.getnext()
            table.remove(following)
            following = next_row
        table.remove(prototype)
        # Global substitution precedes item insertion: user text is never re-templated.
        _replace_tokens(root, values)
        for number, item in enumerate(request.items.all(), 1):
            row = deepcopy(prototype)
            _replace_tokens(row, {"{{№}}": number, "{{technique}}": item.vehicle,
                                 "{{technique_dates}}": item.schedule, "{{count}}": item.quantity,
                                 "{{job}}": item.work, "{{comment}}": item.note})
            table.insert(insertion + number - 1, row)
        content = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        with ZipFile(destination, "w") as output:
            for entry in source.infolist():
                output.writestr(entry, content if entry.filename == "word/document.xml" else source.read(entry.filename))


def generate_pdf(request):
    with TemporaryDirectory(prefix="transport_") as directory:
        source = Path(directory) / f"transport-{request.pk}-v{request.revision}.docx"
        build_docx(request, source)
        content = convert_docx_to_pdf(source, Path(directory)).read_bytes()
        if not content.startswith(b"%PDF-"):
            raise RuntimeError("Конвертер не создал корректный PDF.")
        return content
