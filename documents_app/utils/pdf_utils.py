import subprocess
from pathlib import Path

from django.conf import settings
from PyPDF2 import PdfReader


def convert_xlsx_to_pdf(xlsx_path: Path, output_dir: Path) -> Path:
    """
    Конвертирует XLSX → PDF через LibreOffice
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            settings.LIBREOFFICE_EXECUTABLE,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(xlsx_path),
        ],
        check=True,
    )

    return output_dir / f"{xlsx_path.stem}.pdf"

def convert_docx_to_pdf(docx_path: Path, output_dir: Path) -> Path:
    """
    Конвертирует DOCX → PDF через LibreOffice
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            settings.LIBREOFFICE_EXECUTABLE,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(docx_path),
        ],
        check=True,
    )

    return output_dir / f"{docx_path.stem}.pdf"


def get_pdf_pages_count(pdf_path: Path) -> int:
    """
    Возвращает количество страниц PDF
    """
    reader = PdfReader(str(pdf_path))
    return len(reader.pages)