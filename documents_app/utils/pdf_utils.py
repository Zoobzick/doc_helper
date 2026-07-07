import subprocess
import tempfile
from pathlib import Path
from shutil import which

from django.conf import settings
from PyPDF2 import PdfReader


def _get_libreoffice_executable() -> str:
    configured = str(getattr(settings, "LIBREOFFICE_EXECUTABLE", "") or "").strip()
    candidates = [configured, "libreoffice", "soffice"]

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        candidate_path = Path(candidate)
        if candidate_path.is_absolute() and candidate_path.exists():
            return candidate

        resolved = which(candidate)
        if resolved:
            return resolved

    return configured or "libreoffice"


def _convert_to_pdf(source_path: Path, output_dir: Path, *, source_label: str) -> Path:
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    expected_pdf_path = output_dir / f"{source_path.stem}.pdf"
    output_dir.mkdir(parents=True, exist_ok=True)

    executable = _get_libreoffice_executable()
    with tempfile.TemporaryDirectory(prefix="doc_helper_lo_profile_") as profile_dir:
        args = [
            executable,
            f"-env:UserInstallation={Path(profile_dir).resolve().as_uri()}",
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(source_path),
        ]

        try:
            proc = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"LibreOffice not found while converting {source_label} to PDF. "
                "Install libreoffice-calc and libreoffice-writer on the server, "
                "or set DOC_HELPER_LIBREOFFICE_EXECUTABLE to the soffice/libreoffice path."
            ) from exc

    if proc.returncode != 0:
        raise RuntimeError(
            f"LibreOffice failed to convert {source_label} to PDF. "
            f"Command: {' '.join(args)}. "
            f"stdout: {proc.stdout.strip() or '-'}; stderr: {proc.stderr.strip() or '-'}"
        )

    if not expected_pdf_path.exists():
        raise RuntimeError(
            f"LibreOffice finished without creating PDF for {source_label}: {expected_pdf_path}. "
            f"stdout: {proc.stdout.strip() or '-'}; stderr: {proc.stderr.strip() or '-'}"
        )

    return expected_pdf_path


def convert_xlsx_to_pdf(xlsx_path: Path, output_dir: Path) -> Path:
    return _convert_to_pdf(xlsx_path, output_dir, source_label="XLSX")


def convert_docx_to_pdf(docx_path: Path, output_dir: Path) -> Path:
    return _convert_to_pdf(docx_path, output_dir, source_label="DOCX")


def get_pdf_pages_count(pdf_path: Path) -> int:
    reader = PdfReader(str(pdf_path))
    return len(reader.pages)
