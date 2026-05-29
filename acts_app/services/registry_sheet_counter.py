from __future__ import annotations

import subprocess
from pathlib import Path

from django.conf import settings

from acts_app.models import ActAttachment
from documents_app.utils.pdf_utils import get_pdf_pages_count


class RegistrySheetCountError(RuntimeError):
    pass


def physical_sheets_for_pages(pages_count: int) -> int:
    pages = max(1, int(pages_count or 0))
    return (pages + 1) // 2


def docx_to_pdf_cached(docx_path: Path) -> Path:
    if not docx_path.exists():
        raise FileNotFoundError(f"DOCX not found: {docx_path}")

    out_dir = docx_path.parent
    pdf_path = out_dir / f"{docx_path.stem}.pdf"

    if pdf_path.exists():
        try:
            if pdf_path.stat().st_mtime >= docx_path.stat().st_mtime:
                return pdf_path
        except Exception:
            pass

    cmd = [
        getattr(settings, "LIBREOFFICE_EXECUTABLE", None) or "libreoffice",
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(docx_path),
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RegistrySheetCountError("LibreOffice не найден. Установи пакет libreoffice на сервере.") from exc
    except subprocess.CalledProcessError as exc:
        try:
            err = (exc.stderr or b"").decode("utf-8", errors="ignore")
        except Exception:
            err = str(exc)
        raise RegistrySheetCountError(f"Не удалось конвертировать DOCX в PDF: {err[:500]}") from exc

    if not pdf_path.exists():
        raise RegistrySheetCountError("Конвертация завершилась без ошибки, но PDF не появился.")

    return pdf_path


def refresh_registry_sheets_count(*, registry: ActAttachment, docx_path: Path) -> int:
    try:
        pdf_path = docx_to_pdf_cached(Path(docx_path))
        pages_count = get_pdf_pages_count(pdf_path)
    except Exception as exc:
        if isinstance(exc, RegistrySheetCountError):
            raise
        raise RegistrySheetCountError(str(exc)) from exc

    sheets_count = physical_sheets_for_pages(pages_count)
    if int(registry.sheets_count or 0) != sheets_count:
        registry.sheets_count = sheets_count
        registry.save(update_fields=["sheets_count"])

    return sheets_count
