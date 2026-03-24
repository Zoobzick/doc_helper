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
    Результат генерации XLSX-реестра по одному project внутри batch.
    """
    generated_document: GeneratedDocument
    context: dict
    dependency_signature: str
    created: bool


class ProjectRegistryGenerationServiceError(Exception):
    """Базовая ошибка сервиса генерации проектного XLSX-реестра."""


class ProjectRegistryGenerationValidationError(ProjectRegistryGenerationServiceError):
    """Ошибка входных данных / состава batch для генерации реестра."""


class ProjectRegistryGenerationService:
    """
    Генерирует XLSX-реестр для одного project внутри batch
    и сохраняет его как GeneratedDocument.

    ВАЖНО:
    - context строится ровно один раз
    - renderer получает уже готовый context
    - другие проекты batch не затрагиваются
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
        Генерирует XLSX-реестр для одного project внутри batch
        и сохраняет его в GeneratedDocument.
        """
        batch_project = self._get_batch_project_or_raise(
            batch=batch,
            project_id=project_id,
        )

        context = self.context_builder.build(
            batch=batch,
            project_id=project_id,
        )

        dependency_signature = self.signature_service.build_project_registry_signature(
            batch=batch,
            project_id=project_id,
            document_type=GeneratedDocumentType.REGISTRY_XLSX,
        )

        filename = self._build_output_filename(
            batch=batch,
            project=batch_project.project,
        )

        generated_document, created = self._get_or_create_generated_document(
            batch=batch,
            project=batch_project.project,
        )

        rendered_bytes, pages_count = self._render_context_to_bytes_and_pages(
            context=context,
            template_path=template_path,
            project_id=project_id,
        )

        self._save_file_to_generated_document(
            generated_document=generated_document,
            filename=filename,
            content=rendered_bytes,
        )

        generated_document.source_kind = GeneratedDocumentSourceKind.GENERATED
        generated_document.dependency_signature = dependency_signature
        generated_document.pages_count = pages_count
        generated_document.is_actual = True
        generated_document.created_by = batch.created_by
        generated_document.generated_at = timezone.now()
        generated_document.save()

        return ProjectRegistryGenerationResult(
            generated_document=generated_document,
            context=context,
            dependency_signature=dependency_signature,
            created=created,
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
    ) -> tuple[GeneratedDocument, bool]:
        generated_document, created = GeneratedDocument.objects.get_or_create(
            batch=batch,
            project=project,
            document_type=GeneratedDocumentType.REGISTRY_XLSX,
            defaults={
                "source_kind": GeneratedDocumentSourceKind.GENERATED,
                "created_by": batch.created_by,
                "is_actual": True,
            },
        )
        return generated_document, created

    def _render_context_to_bytes_and_pages(
        self,
        *,
        context: dict,
        template_path: str | Path,
        project_id: int,
    ) -> tuple[bytes, int]:
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

            return rendered_path.read_bytes(), pages_count

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

    def _build_output_filename(
        self,
        *,
        batch: DocumentBatch,
        project,
    ) -> str:
        project_code = (getattr(project, "full_code", "") or "").strip()
        safe_project_code = self._sanitize_filename_part(project_code) or f"project_{project.id}"
        return f"Реестр ИД {safe_project_code} batch_{batch.id}.xlsx"

    @staticmethod
    def _sanitize_filename_part(value: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        cleaned = "".join("_" if ch in invalid_chars else ch for ch in value)
        cleaned = cleaned.strip().strip(".")
        return cleaned