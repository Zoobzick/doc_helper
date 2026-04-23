from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from documents_app.models import (
    DocumentBatch,
    DocumentBatchProject,
    GeneratedDocument,
    GeneratedDocumentSourceKind,
    GeneratedDocumentType,
)
from documents_app.services.id_handover.document_signatures import DocumentSignatureService
from documents_app.services.id_handover.registry_context_builder import RegistryContextBuilder
from documents_app.services.id_handover.registry_xlsx_renderer import RegistryXlsxRenderer
from documents_app.utils.pdf_utils import convert_xlsx_to_pdf, get_pdf_pages_count


@dataclass(slots=True)
class ProjectRegistryGenerationResult:
    """
    Результат генерации реестра по одному project внутри batch.

    xlsx_document (GeneratedDocument): основной XLSX-реестр
    pdf_document (GeneratedDocument): preview PDF этого же реестра
    context (dict): контекст, по которому строился реестр
    dependency_signature (str): сигнатура зависимостей для XLSX/PDF
    xlsx_created (bool): был ли создан новый GeneratedDocument XLSX
    pdf_created (bool): был ли создан новый GeneratedDocument PDF
    pages_count (int): число страниц preview PDF
    """
    xlsx_document: GeneratedDocument
    pdf_document: GeneratedDocument
    context: dict
    dependency_signature: str
    xlsx_created: bool
    pdf_created: bool
    pages_count: int


class ProjectRegistryGenerationServiceError(Exception):
    """Базовая ошибка сервиса генерации проектного реестра."""


class ProjectRegistryGenerationValidationError(ProjectRegistryGenerationServiceError):
    """Ошибка входных данных / состава batch для генерации реестра."""


class ProjectRegistryGenerationService:
    """
    Генерирует реестр для одного project внутри batch и сохраняет:

    - REGISTRY_XLSX
    - REGISTRY_PREVIEW_PDF

    ВАЖНО:
    - context строится один раз
    - XLSX и PDF получают одну и ту же dependency_signature
    - pages_count считается по итоговому preview PDF
    """

    def __init__(
        self,
        *,
        context_builder: RegistryContextBuilder | None = None,
        renderer: RegistryXlsxRenderer | None = None,
        signature_service: DocumentSignatureService | None = None,
    ) -> None:
        self.context_builder = context_builder or RegistryContextBuilder()
        self.renderer = renderer or RegistryXlsxRenderer()
        self.signature_service = signature_service or DocumentSignatureService()

    @transaction.atomic
    def generate_for_project(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
        template_path: str | Path,
    ) -> ProjectRegistryGenerationResult:
        """
        Генерирует реестр по одному project внутри batch
        и сохраняет XLSX + preview PDF в GeneratedDocument.
        """
        batch_project = self._get_batch_project_or_raise(
            batch=batch,
            project_id=project_id,
        )

        context = self.context_builder.build(
            batch=batch,
            project_id=project_id,
        )

        xlsx_dependency_signature = self.signature_service.build_project_registry_signature(
            batch=batch,
            project_id=project_id,
            document_type=GeneratedDocumentType.REGISTRY_XLSX,
        )
        pdf_dependency_signature = self.signature_service.build_project_registry_signature(
            batch=batch,
            project_id=project_id,
            document_type=GeneratedDocumentType.REGISTRY_PREVIEW_PDF,
        )

        xlsx_filename = self._build_xlsx_output_filename(
            batch=batch,
            project=batch_project.project,
        )
        pdf_filename = self._build_pdf_output_filename(
            batch=batch,
            project=batch_project.project,
        )

        xlsx_document, xlsx_created = self._get_or_create_generated_document(
            batch=batch,
            project=batch_project.project,
            document_type=GeneratedDocumentType.REGISTRY_XLSX,
        )
        pdf_document, pdf_created = self._get_or_create_generated_document(
            batch=batch,
            project=batch_project.project,
            document_type=GeneratedDocumentType.REGISTRY_PREVIEW_PDF,
        )

        rendered_xlsx_bytes, rendered_pdf_bytes, pages_count = self._render_context_to_files(
            context=context,
            template_path=template_path,
            project_id=project_id,
        )

        self._save_file_to_generated_document(
            generated_document=xlsx_document,
            filename=xlsx_filename,
            content=rendered_xlsx_bytes,
        )
        self._save_file_to_generated_document(
            generated_document=pdf_document,
            filename=pdf_filename,
            content=rendered_pdf_bytes,
        )

        generated_at = timezone.now()

        self._apply_generated_document_meta(
            generated_document=xlsx_document,
            batch=batch,
            dependency_signature=xlsx_dependency_signature,
            pages_count=pages_count,
            generated_at=generated_at,
        )
        self._apply_generated_document_meta(
            generated_document=pdf_document,
            batch=batch,
            dependency_signature=pdf_dependency_signature,
            pages_count=pages_count,
            generated_at=generated_at,
        )

        return ProjectRegistryGenerationResult(
            xlsx_document=xlsx_document,
            pdf_document=pdf_document,
            context=context,
            dependency_signature=xlsx_dependency_signature,
            xlsx_created=xlsx_created,
            pdf_created=pdf_created,
            pages_count=pages_count,
        )

    def _get_batch_project_or_raise(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
    ) -> DocumentBatchProject:
        batch_project = (
            DocumentBatchProject.objects
            .select_related("project")
            .filter(batch=batch, project_id=project_id)
            .first()
        )
        if not batch_project:
            raise ProjectRegistryGenerationValidationError(
                "Указанный проект не входит в состав данного комплекта."
            )
        return batch_project

    def _get_or_create_generated_document(
        self,
        *,
        batch: DocumentBatch,
        project,
        document_type: str,
    ) -> tuple[GeneratedDocument, bool]:
        generated_document, created = GeneratedDocument.objects.get_or_create(
            batch=batch,
            project=project,
            document_type=document_type,
            defaults={
                "source_kind": GeneratedDocumentSourceKind.GENERATED,
                "created_by": batch.created_by,
                "is_actual": True,
            },
        )
        return generated_document, created

    def _render_context_to_files(
        self,
        *,
        context: dict,
        template_path: str | Path,
        project_id: int,
    ) -> tuple[bytes, bytes, int]:
        """
        Возвращает:
        - xlsx bytes
        - pdf bytes
        - pages_count
        """
        template_path = Path(template_path)
        if not template_path.exists():
            raise ProjectRegistryGenerationValidationError(
                f"XLSX-шаблон не найден: {template_path}"
            )

        suffix = template_path.suffix or ".xlsx"

        with tempfile.TemporaryDirectory(prefix="doc_helper_registry_") as tmp_dir:
            tmp_dir_path = Path(tmp_dir)
            xlsx_output_path = tmp_dir_path / f"registry_project_{project_id}{suffix}"

            rendered_path = self.renderer.render_from_context(
                context=context,
                template_path=template_path,
                output_path=xlsx_output_path,
            )

            pdf_path = convert_xlsx_to_pdf(
                xlsx_path=rendered_path,
                output_dir=tmp_dir_path,
            )
            pages_count = get_pdf_pages_count(pdf_path)

            return (
                rendered_path.read_bytes(),
                pdf_path.read_bytes(),
                pages_count,
            )

    def _save_file_to_generated_document(
        self,
        *,
        generated_document: GeneratedDocument,
        filename: str,
        content: bytes,
    ) -> None:
        if generated_document.file:
            try:
                generated_document.file.delete(save=False)
            except Exception:
                pass

        generated_document.file.save(
            filename,
            ContentFile(content),
            save=False,
        )
        generated_document.original_name = filename

    def _apply_generated_document_meta(
        self,
        *,
        generated_document: GeneratedDocument,
        batch: DocumentBatch,
        dependency_signature: str,
        pages_count: int,
        generated_at,
    ) -> None:
        generated_document.source_kind = GeneratedDocumentSourceKind.GENERATED
        generated_document.dependency_signature = dependency_signature
        generated_document.pages_count = pages_count
        generated_document.is_actual = True
        generated_document.created_by = batch.created_by
        generated_document.generated_at = generated_at
        generated_document.save()

    def _build_xlsx_output_filename(
        self,
        *,
        batch: DocumentBatch,
        project,
    ) -> str:
        project_code = (getattr(project, "full_code", "") or "").strip()
        safe_project_code = self._sanitize_filename_part(project_code) or f"project_{project.id}"
        return f"Реестр ИД {safe_project_code} batch_{batch.id}.xlsx"

    def _build_pdf_output_filename(
        self,
        *,
        batch: DocumentBatch,
        project,
    ) -> str:
        project_code = (getattr(project, "full_code", "") or "").strip()
        safe_project_code = self._sanitize_filename_part(project_code) or f"project_{project.id}"
        return f"Реестр ИД {safe_project_code} batch_{batch.id}.pdf"

    @staticmethod
    def _sanitize_filename_part(value: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        cleaned = "".join("_" if ch in invalid_chars else ch for ch in value)
        cleaned = cleaned.strip().strip(".")
        return cleaned