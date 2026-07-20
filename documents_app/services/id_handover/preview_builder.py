from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Prefetch

from acts_app.models import ActAppendixLine, ActMaterialItem

from documents_app.models import (
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchProject,
)
from documents_app.services.id_handover.material_registry_projection import (
    get_batch_appendix_sheets_count,
)


@dataclass(slots=True)
class DocumentPreviewRow:
    """
    Одна строка preview состава проекта.

    row_type:
        - "act"
        - "appendix"

    position:
        Сквозной номер строки внутри проекта.

    act_position:
        Позиция акта внутри проекта (DocumentBatchAct.order).

    sheets_count:
        Количество листов документа.
        Для акта всегда 1.
    """
    row_type: str
    position: int
    act_position: int
    act_id: int
    act_number: str
    act_date: str
    document_name: str
    sheets_count: int
    source_batch_act_id: int
    source_appendix_line_id: int | None
    source_kind: str
    appendix_position: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_type": self.row_type,
            "is_act": self.row_type == "act",
            "is_appendix": self.row_type == "appendix",
            "position": self.position,
            "act_position": self.act_position,
            "act_id": self.act_id,
            "act_number": self.act_number,
            "act_date": self.act_date,
            "document_name": self.document_name,
            "label": self.document_name,
            "sheets_count": self.sheets_count,
            "source_batch_act_id": self.source_batch_act_id,
            "source_appendix_line_id": self.source_appendix_line_id,
            "source_kind": self.source_kind,
            "appendix_position": self.appendix_position,
            "can_move_up": self.row_type == "act",
            "can_move_down": self.row_type == "act",
            "can_delete": self.row_type == "act",
        }


class DocumentBatchPreviewBuilderError(Exception):
    """Базовая ошибка построения preview комплекта."""


class DocumentBatchPreviewBuilder:
    """
    Строит web-preview для комплекта сдачи ИД.

    Новый формат preview:
    - шапка batch
    - список проектов
    - внутри каждого проекта:
      - плоский список документов в том порядке, в котором они реально
        укладываются в папку:
            1) акт (всегда 1 лист)
            2) все приложения этого акта
            3) следующий акт
            4) его приложения
            ...

    ВАЖНО:
    - preview ничего не изменяет в БД
    - preview только читает DocumentBatch / DocumentBatchAct / ActAppendixLine
    """

    EXEC_SCHEME_PREFIX = "исполнительная схема"

    def build(self, *, batch: DocumentBatch) -> dict[str, Any]:
        """
        Возвращает полную preview-структуру для batch.
        """
        batch_projects = self._get_batch_projects(batch=batch)
        batch_acts_by_project = self._get_batch_acts_grouped(batch=batch)

        projects_payload: list[dict[str, Any]] = []
        total_projects_count = 0
        total_acts_count = 0
        total_registry_rows_count = 0
        total_sheets_count = 0

        for batch_project in batch_projects:
            project_id = batch_project.project_id
            project_batch_acts = batch_acts_by_project.get(project_id, [])

            project_payload = self._build_project_payload(
                batch_project=batch_project,
                batch_acts=project_batch_acts,
            )

            projects_payload.append(project_payload)

            total_projects_count += 1
            total_acts_count += project_payload["acts_count"]
            total_registry_rows_count += project_payload["registry_rows_count"]
            total_sheets_count += project_payload["total_sheets_count"]

        preview_data = {
            "batch": self._build_batch_payload(batch=batch),
            "summary": {
                "projects_count": total_projects_count,
                "acts_count": total_acts_count,
                "registry_rows_count": total_registry_rows_count,
                "total_sheets_count": total_sheets_count,
            },
            "projects": projects_payload,
        }
        return preview_data

    @transaction.atomic
    def build_and_save_snapshot(self, *, batch: DocumentBatch) -> dict[str, Any]:
        """
        Строит preview и сохраняет его в batch.preview_snapshot_json.
        """
        preview_data = self.build(batch=batch)
        batch.preview_snapshot_json = preview_data
        batch.save(update_fields=["preview_snapshot_json", "updated_at"])
        return preview_data

    def _get_batch_projects(self, *, batch: DocumentBatch) -> list[DocumentBatchProject]:
        """
        Загружает проекты batch в порядке order.
        """
        return list(
            DocumentBatchProject.objects
            .filter(batch=batch)
            .select_related("project")
            .order_by("order", "id")
        )

    def _get_batch_acts_grouped(self, *, batch: DocumentBatch) -> dict[int, list[DocumentBatchAct]]:
        """
        Загружает все DocumentBatchAct и группирует их по project_id.

        К act заранее префетчит appendix_lines в порядке position.
        """
        appendix_prefetch = Prefetch(
            "act__appendix_lines",
            queryset=ActAppendixLine.objects.select_related("source_attachment").order_by("position", "id"),
        )
        materials_prefetch = Prefetch(
            "act__materials",
            queryset=(
                ActMaterialItem.objects
                .select_related("passport", "passport__material")
                .order_by("position", "id")
            ),
        )

        batch_acts = list(
            DocumentBatchAct.objects
            .filter(batch=batch)
            .select_related("project", "act")
            .prefetch_related(appendix_prefetch, materials_prefetch)
            .order_by("project_id", "order", "id")
        )

        grouped: dict[int, list[DocumentBatchAct]] = {}
        for batch_act in batch_acts:
            grouped.setdefault(batch_act.project_id, []).append(batch_act)

        return grouped

    def _build_batch_payload(self, *, batch: DocumentBatch) -> dict[str, Any]:
        """
        Шапка preview по batch.
        """
        return {
            "id": batch.id,
            "uuid": str(batch.uuid),
            "title": batch.title,
            "comment": batch.comment,
            "selection_mode": batch.selection_mode,
            "selection_mode_display": batch.get_selection_mode_display(),
            "month_from": batch.month_from,
            "month_to": batch.month_to,
            "generation_mode": batch.generation_mode,
            "generation_mode_display": batch.get_generation_mode_display(),
            "letter_type": batch.letter_type,
            "letter_type_display": batch.get_letter_type_display(),
            "letter_number": batch.letter_number,
            "letter_date": batch.letter_date.isoformat() if batch.letter_date else None,
            "documentation_type": batch.documentation_type,
            "documentation_type_display": batch.get_documentation_type_display(),
            "project_scope": batch.project_scope,
            "project_scope_display": batch.get_project_scope_display(),
            "created_by_id": batch.created_by_id,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "updated_at": batch.updated_at.isoformat() if batch.updated_at else None,
        }

    def _build_project_payload(
        self,
        *,
        batch_project: DocumentBatchProject,
        batch_acts: list[DocumentBatchAct],
    ) -> dict[str, Any]:
        """
        Строит preview по одному проекту batch.
        """
        document_rows: list[dict[str, Any]] = []
        acts_payload: list[dict[str, Any]] = []

        current_position = 0
        total_sheets_count = 0

        for batch_act in batch_acts:
            act_payload, rows = self._build_act_payload_and_document_rows(
                batch_act=batch_act,
                current_position=current_position,
            )

            acts_payload.append(act_payload)
            document_rows.extend([row.to_dict() for row in rows])

            current_position += len(rows)
            total_sheets_count += act_payload["total_sheets_count"]

        project = batch_project.project
        project_name = getattr(project, "full_code", None) or str(project)

        return {
            "project": {
                "id": project.id,
                "uuid": str(getattr(project, "uuid", "")) if getattr(project, "uuid", None) else "",
                "title": project_name,
                "full_code": getattr(project, "full_code", ""),
                "order": batch_project.order,
                "batch_project_id": batch_project.id,
            },
            "summary": {
                "acts_count": len(acts_payload),
                "registry_rows_count": len(document_rows),
                "documents_count": len(document_rows),
                "total_sheets_count": total_sheets_count,
            },
            "project_id": project.id,
            "project_code": getattr(project, "full_code", ""),
            "project_name": project_name,
            "acts_count": len(acts_payload),
            "registry_rows_count": len(document_rows),
            "documents_count": len(document_rows),
            "total_sheets_count": total_sheets_count,
            "acts": acts_payload,
            "registry_rows": document_rows,
            "document_rows": document_rows,
        }

    def _build_act_payload_and_document_rows(
        self,
        *,
        batch_act: DocumentBatchAct,
        current_position: int,
    ) -> tuple[dict[str, Any], list[DocumentPreviewRow]]:
        """
        Строит:
        1. payload по одному акту
        2. плоские строки документов по этому акту

        Правило:
        - первая строка = сам акт, всегда 1 лист
        - далее строки ActAppendixLine
        """
        act = batch_act.act
        appendix_lines = list(act.appendix_lines.all())

        rows: list[DocumentPreviewRow] = []

        act_document_name = self._build_act_document_name(batch_act=batch_act)

        act_row = DocumentPreviewRow(
            row_type="act",
            position=current_position + 1,
            act_position=batch_act.order,
            act_id=act.id,
            act_number=act.number,
            act_date=self._format_date(act.act_date),
            document_name=act_document_name,
            sheets_count=1,
            source_batch_act_id=batch_act.id,
            source_appendix_line_id=None,
            source_kind=batch_act.source,
            appendix_position=None,
        )
        rows.append(act_row)

        for appendix_line in appendix_lines:
            appendix_document_name = self._build_appendix_document_name(
                batch_act=batch_act,
                appendix_line=appendix_line,
            )

            appendix_row = DocumentPreviewRow(
                row_type="appendix",
                position=current_position + len(rows) + 1,
                act_position=batch_act.order,
                act_id=act.id,
                act_number=act.number,
                act_date=self._format_date(act.act_date),
                document_name=appendix_document_name,
                sheets_count=self._normalize_sheets_count(
                    get_batch_appendix_sheets_count(appendix_line)
                ),
                source_batch_act_id=batch_act.id,
                source_appendix_line_id=appendix_line.id,
                source_kind=batch_act.source,
                appendix_position=appendix_line.position,
            )
            rows.append(appendix_row)

        total_sheets_count = sum(row.sheets_count for row in rows)

        act_payload = {
            "batch_act_id": batch_act.id,
            "batch_act_uuid": str(batch_act.uuid),
            "order": batch_act.order,
            "source": batch_act.source,
            "source_display": batch_act.get_source_display(),
            "position": batch_act.order,
            "act_id": act.id,
            "act_uuid": str(act.uuid),
            "act_number": act.number,
            "act_date": self._format_date(act.act_date),
            "document_name": act_document_name,
            "label": act_document_name,
            "work_name": act.work_name,
            "work_start_date": self._format_date(act.work_start_date),
            "work_end_date": self._format_date(act.work_end_date),
            "sheets_total": getattr(act, "sheets_total", 0),
            "status": act.status,
            "status_display": act.get_status_display(),
            "summary": {
                "appendix_lines_count": len(appendix_lines),
                "registry_rows_count": len(rows),
                "documents_count": len(rows),
                "total_sheets_count": total_sheets_count,
            },
            "appendix_lines_count": len(appendix_lines),
            "registry_rows_count": len(rows),
            "documents_count": len(rows),
            "total_sheets_count": total_sheets_count,
            "document_rows": [row.to_dict() for row in rows],
            "registry_rows": [row.to_dict() for row in rows],
        }

        return act_payload, rows

    def _build_act_document_name(self, *, batch_act: DocumentBatchAct) -> str:
        """
        Отдельная строка для самого акта.

        Формат:
        Акт освидетельствования скрытых работ №{number} от {date}г.
        {work_name}
        """
        act = batch_act.act
        act_date_text = self._format_date(act.act_date)
        work_name = (act.work_name or "").strip()

        first_line = f"Акт освидетельствования скрытых работ №{act.number} от {act_date_text}г."
        if work_name:
            return f"{first_line}\n{work_name}"
        return first_line

    def _build_appendix_document_name(
        self,
        *,
        batch_act: DocumentBatchAct,
        appendix_line: ActAppendixLine,
    ) -> str:
        """
        Возвращает текст строки приложения.

        Особое правило:
        если строка начинается с "Исполнительная схема ...",
        то после даты схемы добавляем перевод строки и work_name.

        Формат:
        Исполнительная схема №... от ...г.
        {work_name}

        Для остальных приложений label остаётся как есть.
        """
        label = (appendix_line.label or "").strip()
        if not label:
            return ""

        if not self._is_exec_scheme_label(label):
            return label

        return self._inject_work_name_after_exec_scheme_date(
            label=label,
            work_name=(batch_act.act.work_name or "").strip(),
        )

    def _is_exec_scheme_label(self, label: str) -> bool:
        """
        Проверяет, что строка относится к исполнительной схеме.
        """
        normalized = label.strip().lower()
        return normalized.startswith(self.EXEC_SCHEME_PREFIX)

    def _inject_work_name_after_exec_scheme_date(self, *, label: str, work_name: str) -> str:
        """
        Добавляет work_name после первой строки исполнительной схемы.

        Примеры:
        1)
        Было:
        Исполнительная схема №123 от 10.03.2026г.

        Стало:
        Исполнительная схема №123 от 10.03.2026г.
        Наименование работ

        2)
        Было:
        Исполнительная схема №123 от 10.03.2026г.
        Наименование работ

        Стало:
        без изменений
        """
        if not work_name:
            return label

        normalized_label = label.replace("\r\n", "\n").replace("\r", "\n")

        if "\n" in normalized_label:
            first_line, remainder = normalized_label.split("\n", 1)
            if remainder.strip():
                return normalized_label
            return f"{first_line}\n{work_name}"

        match = re.match(r"^(.*?г\.)\s*$", normalized_label, flags=re.IGNORECASE)
        if match:
            first_line = match.group(1).strip()
            return f"{first_line}\n{work_name}"

        return f"{normalized_label}\n{work_name}"

    @staticmethod
    def _normalize_sheets_count(value) -> int:
        """
        Приводит количество листов к безопасному целому числу >= 0.
        """
        if value in (None, ""):
            return 0

        try:
            result = int(value)
        except (TypeError, ValueError):
            return 0

        return max(result, 0)

    @staticmethod
    def _format_date(value) -> str:
        """
        Приводит date / datetime / None к строке dd.mm.yyyy или пустой строке.
        """
        if not value:
            return ""
        return value.strftime("%d.%m.%Y")
