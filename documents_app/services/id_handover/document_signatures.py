from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Prefetch

from acts_app.models import ActAppendixLine, ActParty

from documents_app.models import (
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchProject,
    GeneratedDocument,
    GeneratedDocumentType,
)


@dataclass(slots=True)
class SignatureCheckResult:
    """
    Результат проверки актуальности документа.
    """
    generated_document: GeneratedDocument
    expected_signature: str
    stored_signature: str
    is_actual: bool


class DocumentSignatureService:
    """
    Сервис расчёта сигнатур зависимостей для GeneratedDocument.
    """

    PROJECT_REGISTRY_TYPES = {
        GeneratedDocumentType.REGISTRY_XLSX,
        GeneratedDocumentType.REGISTRY_PREVIEW_PDF,
    }

    BATCH_LETTER_TYPES = {
        GeneratedDocumentType.LETTER_DOCX,
        GeneratedDocumentType.LETTER_PREVIEW_PDF,
    }

    STAMPED_TYPES = {
        GeneratedDocumentType.STAMPED_LETTER_PDF,
        GeneratedDocumentType.STAMPED_LETTER_IMAGE,
    }

    def build_signature_for_document(self, *, generated_document: GeneratedDocument) -> str:
        document_type = generated_document.document_type

        if document_type in self.PROJECT_REGISTRY_TYPES:
            if not generated_document.project_id:
                raise ValueError("Для проектного реестра должен быть указан project.")
            return self.build_project_registry_signature(
                batch=generated_document.batch,
                project_id=generated_document.project_id,
                document_type=document_type,
            )

        if document_type in self.BATCH_LETTER_TYPES:
            return self.build_batch_letter_signature(
                batch=generated_document.batch,
                document_type=document_type,
            )

        if document_type in self.STAMPED_TYPES:
            return self.build_stamped_letter_signature(
                batch=generated_document.batch,
                document_type=document_type,
            )

        raise ValueError(f"Неподдерживаемый document_type: {document_type}")

    def build_project_registry_signature(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
        document_type: str,
    ) -> str:
        """
        Сигнатура проектного документа реестра.

        Включает:
        - параметры batch
        - проектные данные, реально используемые в реестре
        - состав и порядок batch acts внутри проекта
        - данные act
        - appendix_lines
        - parties
        - document_type
        """
        batch_project = (
            DocumentBatchProject.objects
            .select_related("project", "project__line", "project__stage", "project__plot")
            .filter(batch=batch, project_id=project_id)
            .first()
        )
        if not batch_project:
            raise ValueError("Проект не найден в составе batch.")

        appendix_prefetch = Prefetch(
            "act__appendix_lines",
            queryset=ActAppendixLine.objects.order_by("position", "id"),
        )
        parties_prefetch = Prefetch(
            "act__parties",
            queryset=ActParty.objects.select_related(
                "organization",
                "chosen_authorization",
            ).order_by("position", "id"),
        )

        project_batch_acts = list(
            DocumentBatchAct.objects
            .filter(batch=batch, project_id=project_id)
            .select_related("act", "project")
            .prefetch_related(appendix_prefetch, parties_prefetch)
            .order_by("order", "id")
        )

        project = batch_project.project

        payload = {
            "scope": "project_registry",
            "document_type": document_type,
            "batch": self._serialize_batch_core(batch=batch),
            "project": {
                "id": batch_project.project_id,
                "uuid": str(getattr(project, "uuid", "")) if getattr(project, "uuid", None) else "",
                "full_code": getattr(project, "full_code", ""),
                "line_full_name": self._get_project_line_full_name(project),
                "stage_full_name": self._get_project_stage_full_name(project),
                "plot_full_name": self._get_project_plot_full_name(project),
                "construction": self._get_project_construction(project),
                "batch_order": batch_project.order,
            },
            "acts": [
                self._serialize_batch_act_for_registry(batch_act=batch_act)
                for batch_act in project_batch_acts
            ],
        }
        return self._hash_payload(payload)

    def build_batch_letter_signature(
        self,
        *,
        batch: DocumentBatch,
        document_type: str,
    ) -> str:
        """
        Сигнатура общего письма batch.

        Включает:
        - core-параметры batch
        - состав проектов
        - ожидаемые сигнатуры проектных реестров
        - pages_count уже сгенерированных проектных реестров
        """
        batch_projects = list(
            DocumentBatchProject.objects
            .select_related("project", "project__line", "project__stage", "project__plot")
            .filter(batch=batch)
            .order_by("order", "id")
        )

        projects_payload: list[dict[str, Any]] = []

        for batch_project in batch_projects:
            project = batch_project.project
            project_id = batch_project.project_id

            expected_registry_signature = self.build_project_registry_signature(
                batch=batch,
                project_id=project_id,
                document_type=GeneratedDocumentType.REGISTRY_XLSX,
            )

            registry_document = self._get_registry_generated_document_for_letter(
                batch=batch,
                project_id=project_id,
            )

            projects_payload.append(
                {
                    "project": {
                        "id": project_id,
                        "uuid": str(getattr(project, "uuid", "")) if getattr(project, "uuid", None) else "",
                        "full_code": getattr(project, "full_code", ""),
                        "line_full_name": self._get_project_line_full_name(project),
                        "stage_full_name": self._get_project_stage_full_name(project),
                        "plot_full_name": self._get_project_plot_full_name(project),
                        "construction": self._get_project_construction(project),
                        "batch_order": batch_project.order,
                    },
                    "expected_registry_signature": expected_registry_signature,
                    "registry_document": {
                        "id": registry_document.id,
                        "dependency_signature": registry_document.dependency_signature or "",
                        "pages_count": registry_document.pages_count,
                        "is_actual": registry_document.is_actual,
                    },
                }
            )

        payload = {
            "scope": "batch_letter",
            "document_type": document_type,
            "batch": self._serialize_batch_core(batch=batch),
            "projects": projects_payload,
        }
        return self._hash_payload(payload)

    def build_stamped_letter_signature(
        self,
        *,
        batch: DocumentBatch,
        document_type: str,
    ) -> str:
        payload = {
            "scope": "stamped_letter",
            "document_type": document_type,
            "batch": {
                "id": batch.id,
                "uuid": str(batch.uuid),
            },
        }
        return self._hash_payload(payload)

    def check_document_actuality(
        self,
        *,
        generated_document: GeneratedDocument,
    ) -> SignatureCheckResult:
        expected_signature = self.build_signature_for_document(
            generated_document=generated_document
        )
        stored_signature = generated_document.dependency_signature or ""
        is_actual = expected_signature == stored_signature

        return SignatureCheckResult(
            generated_document=generated_document,
            expected_signature=expected_signature,
            stored_signature=stored_signature,
            is_actual=is_actual,
        )

    @transaction.atomic
    def refresh_document_actuality(
        self,
        *,
        generated_document: GeneratedDocument,
    ) -> SignatureCheckResult:
        result = self.check_document_actuality(generated_document=generated_document)

        if generated_document.is_actual != result.is_actual:
            generated_document.is_actual = result.is_actual
            generated_document.save(update_fields=["is_actual"])

        return result

    @transaction.atomic
    def refresh_batch_documents_actuality(self, *, batch: DocumentBatch) -> list[SignatureCheckResult]:
        documents = list(
            GeneratedDocument.objects
            .select_related("batch", "project")
            .filter(batch=batch)
            .order_by("project_id", "document_type", "id")
        )

        results: list[SignatureCheckResult] = []
        changed_documents: list[GeneratedDocument] = []

        for document in documents:
            result = self.check_document_actuality(generated_document=document)
            results.append(result)

            if document.is_actual != result.is_actual:
                document.is_actual = result.is_actual
                changed_documents.append(document)

        if changed_documents:
            GeneratedDocument.objects.bulk_update(changed_documents, ["is_actual"])

        return results

    def _get_registry_generated_document_for_letter(
        self,
        *,
        batch: DocumentBatch,
        project_id: int,
    ) -> GeneratedDocument:
        registry_document = (
            GeneratedDocument.objects
            .filter(
                batch=batch,
                project_id=project_id,
                document_type=GeneratedDocumentType.REGISTRY_XLSX,
            )
            .first()
        )
        if not registry_document:
            raise ValueError(
                f"Для batch id={batch.id} и project id={project_id} не найден сгенерированный реестр."
            )

        if registry_document.pages_count is None or registry_document.pages_count <= 0:
            raise ValueError(
                f"У реестра batch id={batch.id}, project id={project_id} не заполнен pages_count."
            )

        return registry_document

    def _serialize_batch_core(self, *, batch: DocumentBatch) -> dict[str, Any]:
        """
        Сериализует только те поля batch, которые реально влияют на документы.
        """
        return {
            "id": batch.id,
            "uuid": str(batch.uuid),
            "selection_mode": batch.selection_mode,
            "month_from": batch.month_from,
            "month_to": batch.month_to,
            "generation_mode": batch.generation_mode,
            "letter_type": batch.letter_type,
            "letter_number": batch.letter_number,
            "letter_date": batch.letter_date.isoformat() if batch.letter_date else None,
            "documentation_type": batch.documentation_type,
            "project_scope": batch.project_scope,
            "title": batch.title,
            "comment": batch.comment,
        }

    def _serialize_batch_act_for_registry(self, *, batch_act: DocumentBatchAct) -> dict[str, Any]:
        act = batch_act.act
        appendix_lines = list(act.appendix_lines.all())
        parties = list(act.parties.all())

        return {
            "batch_act": {
                "id": batch_act.id,
                "uuid": str(batch_act.uuid),
                "order": batch_act.order,
                "source": batch_act.source,
                "project_id": batch_act.project_id,
                "act_id": batch_act.act_id,
            },
            "act": {
                "id": act.id,
                "uuid": str(act.uuid),
                "number": act.number,
                "act_date": act.act_date.isoformat() if act.act_date else None,
                "work_name": act.work_name,
                "work_start_date": act.work_start_date.isoformat() if act.work_start_date else None,
                "work_end_date": act.work_end_date.isoformat() if act.work_end_date else None,
                "status": act.status,
                "sheets_total": getattr(act, "sheets_total", 0),
                "updated_at": act.updated_at.isoformat() if act.updated_at else None,
            },
            "appendix_lines": [
                {
                    "id": line.id,
                    "position": line.position,
                    "label": line.label,
                    "sheets_count": line.sheets_count,
                    "is_label_overridden": line.is_label_overridden,
                }
                for line in appendix_lines
            ],
            "parties": [
                {
                    "id": party.id,
                    "position": party.position,
                    "role": party.role,
                    "organization_id": party.organization_id,
                    "organization_short_name": (
                        (getattr(party.organization, "short_name", "") or "").strip()
                        if getattr(party, "organization", None) else ""
                    ),
                    "organization_full_name": (
                        (getattr(party.organization, "full_name", "") or "").strip()
                        if getattr(party, "organization", None) else ""
                    ),
                    "chosen_authorization_id": getattr(party, "chosen_authorization_id", None),
                }
                for party in parties
            ],
        }

    def _get_project_line_full_name(self, project) -> str:
        line = getattr(project, "line", None)
        if not line:
            return ""
        return (getattr(line, "full_name", "") or "").strip()

    def _get_project_stage_full_name(self, project) -> str:
        stage = getattr(project, "stage", None)
        if not stage:
            return ""
        return (getattr(stage, "full_name", "") or "").strip()

    def _get_project_plot_full_name(self, project) -> str:
        plot = getattr(project, "plot", None)
        if not plot:
            return ""
        return (getattr(plot, "full_name", "") or "").strip()

    def _get_project_construction(self, project) -> str:
        return (getattr(project, "construction", "") or "").strip()

    @staticmethod
    def _hash_payload(payload: dict[str, Any]) -> str:
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()