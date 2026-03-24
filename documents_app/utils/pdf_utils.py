import subprocess
from pathlib import Path

from PyPDF2 import PdfReader


def convert_xlsx_to_pdf(xlsx_path: Path, output_dir: Path) -> Path:
    """
    Конвертирует XLSX → PDF через LibreOffice
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "libreoffice",
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


def get_pdf_pages_count(pdf_path: Path) -> int:
    """
    Возвращает количество страниц PDF
    """
    reader = PdfReader(str(pdf_path))
    return len(reader.pages)