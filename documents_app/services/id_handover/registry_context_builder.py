from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from django.db.models import Prefetch

from acts_app.models import ActAppendixLine, ActParty
from directive_app.models import ActRole

from documents_app.models import (
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchDocumentationType,
    DocumentBatchProject,
)


@dataclass(slots=True)
class RegistryRowContext:
    """
    Одна строка таблицы реестра.

    number (номер строки в таблице)
    document_text (текст для колонки documents)
    sheets_count (значение для колонки sheets)
    is_act_row (строка самого акта)
    act_id (id акта)
    appendix_line_id (id строки приложения, если есть)
    """
    number: int
    document_text: str
    sheets_count: int
    is_act_row: bool
    act_id: int
    appendix_line_id: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "document_text": self.document_text,
            "sheets_count": self.sheets_count,
            "is_act_row": self.is_act_row,
            "act_id": self.act_id,
            "appendix_line_id": self.appendix_line_id,
        }


class RegistryContextBuilderError(Exception):
    """Базовая ошибка builder-а контекста реестра."""


class RegistryContextBuilderValidationError(RegistryContextBuilderError):
    """Ошибка входных данных builder-а."""


class RegistryContextBuilder:
    """
    Строит контекст для XLSX-шаблона реестра по одному проекту внутри batch.

    Плейсхолдеры шапки:
    - {{tech_customer_org_short}}
    - {{builder_rep_org_short}}
    - {{other_rep_org_short}}
    - {{contractor_rep_org_short}}
    - {{project_line_full_name}}
    - {{project_stage_full_name}}
    - {{project_full_code}}
    - {{project_plot_full_name}}
    - {{project_construction}}
    - {{executive_or_and_working_documentation_registry}}

    Табличная часть:
    - {{documents}}
    - {{sheets}}

    ВАЖНО:
    - один контекст = один project
    - порядок актов берётся из DocumentBatchAct.order
    - первая строка по акту всегда 1 лист
    - далее идут ActAppendixLine
    """

    EXEC_SCHEME_PREFIX = "исполнительная схема"

    def build(self, *, batch: DocumentBatch, project_id: int) -> dict[str, Any]:
        batch_project = self._get_batch_project(batch=batch, project_id=project_id)
        batch_acts = self._get_project_batch_acts(batch=batch, project_id=project_id)

        if not batch_acts:
            raise RegistryContextBuilderValidationError(
                "Нельзя построить реестр: в проекте нет актов."
            )

        first_act = batch_acts[0].act
        rows = self._build_rows(batch_acts=batch_acts)

        context = {
            "batch": {
                "id": batch.id,
                "uuid": str(batch.uuid),
                "title": batch.title,
            },
            "project": {
                "id": batch_project.project_id,
                "uuid": str(getattr(batch_project.project, "uuid", "")) if getattr(batch_project.project, "uuid", None) else "",
                "full_code": getattr(batch_project.project, "full_code", ""),
                "line_full_name": self._get_project_line_full_name(batch_project.project),
                "stage_full_name": self._get_project_stage_full_name(batch_project.project),
                "plot_full_name": self._get_project_plot_full_name(batch_project.project),
                "construction": self._get_project_construction(batch_project.project),
            },
            "placeholders": {
                "tech_customer_org_short": self._get_party_org_short(first_act, ActRole.TECH_CUSTOMER_CONTROL),
                "builder_rep_org_short": self._get_party_org_short(first_act, ActRole.BUILDER_REP),
                "other_rep_org_short": self._get_party_org_short(first_act, ActRole.OTHER_REP),
                "contractor_rep_org_short": self._get_party_org_short(first_act, ActRole.CONTRACTOR_REP),
                "project_line_full_name": self._get_project_line_full_name(batch_project.project),
                "project_stage_full_name": self._get_project_stage_full_name(batch_project.project),
                "project_full_code": getattr(batch_project.project, "full_code", ""),
                "project_plot_full_name": self._get_project_plot_full_name(batch_project.project),
                "project_construction": self._get_project_construction(batch_project.project),
                "executive_or_and_working_documentation_registry": self._get_registry_title(
                    batch.documentation_type
                ),
            },
            "rows": [row.to_dict() for row in rows],
            "summary": {
                "acts_count": len(batch_acts),
                "rows_count": len(rows),
                "total_sheets_count": sum(row.sheets_count for row in rows),
            },
        }
        return context

    def _get_batch_project(self, *, batch: DocumentBatch, project_id: int) -> DocumentBatchProject:
        batch_project = (
            DocumentBatchProject.objects
            .select_related("project")
            .filter(batch=batch, project_id=project_id)
            .first()
        )
        if not batch_project:
            raise RegistryContextBuilderValidationError(
                "Проект не входит в состав указанного комплекта."
            )
        return batch_project

    def _get_project_batch_acts(self, *, batch: DocumentBatch, project_id: int) -> list[DocumentBatchAct]:
        appendix_prefetch = Prefetch(
            "act__appendix_lines",
            queryset=ActAppendixLine.objects.order_by("position", "id"),
        )
        parties_prefetch = Prefetch(
            "act__parties",
            queryset=ActParty.objects.select_related("organization").order_by("position", "id"),
        )

        return list(
            DocumentBatchAct.objects
            .filter(batch=batch, project_id=project_id)
            .select_related("act", "project")
            .prefetch_related(appendix_prefetch, parties_prefetch)
            .order_by("order", "id")
        )

    def _build_rows(self, *, batch_acts: list[DocumentBatchAct]) -> list[RegistryRowContext]:
        rows: list[RegistryRowContext] = []
        row_number = 0

        for batch_act in batch_acts:
            act = batch_act.act

            row_number += 1
            rows.append(
                RegistryRowContext(
                    number=row_number,
                    document_text=self._build_act_document_text(batch_act=batch_act),
                    sheets_count=1,
                    is_act_row=True,
                    act_id=act.id,
                    appendix_line_id=None,
                )
            )

            appendix_lines = list(act.appendix_lines.all())
            for appendix_line in appendix_lines:
                row_number += 1
                rows.append(
                    RegistryRowContext(
                        number=row_number,
                        document_text=self._build_appendix_document_text(
                            batch_act=batch_act,
                            appendix_line=appendix_line,
                        ),
                        sheets_count=appendix_line.sheets_count,
                        is_act_row=False,
                        act_id=act.id,
                        appendix_line_id=appendix_line.id,
                    )
                )

        return rows

    def _build_act_document_text(self, *, batch_act: DocumentBatchAct) -> str:
        act = batch_act.act
        act_date_text = self._format_date(act.act_date)
        work_name = (act.work_name or "").strip()

        first_line = f"Акт освидетельствования скрытых работ №{act.number} от {act_date_text}г."
        if work_name:
            return f"{first_line}\n{work_name}"
        return first_line

    def _build_appendix_document_text(
        self,
        *,
        batch_act: DocumentBatchAct,
        appendix_line: ActAppendixLine,
    ) -> str:
        label = (appendix_line.label or "").strip()
        if not label:
            return ""

        if not self._is_exec_scheme_label(label):
            return label

        return self._inject_work_name_into_exec_scheme_label(
            label=label,
            work_name=(batch_act.act.work_name or "").strip(),
        )

    def _is_exec_scheme_label(self, label: str) -> bool:
        return label.strip().lower().startswith(self.EXEC_SCHEME_PREFIX)

    def _inject_work_name_into_exec_scheme_label(self, *, label: str, work_name: str) -> str:
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

    def _get_party_org_short(self, act, role: str) -> str:
        """
        Берём краткое имя организации из подписантов первого акта реестра.

        Для OTHER_REP, если их несколько, берём первую строку по position.
        """
        parties = list(act.parties.all())

        for party in parties:
            if party.role != role:
                continue
            if not getattr(party, "organization", None):
                continue

            organization = party.organization

            short_name = getattr(organization, "short_name", "") or ""
            full_name = getattr(organization, "full_name", "") or ""

            return short_name.strip() or full_name.strip()

        return ""

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

    def _get_registry_title(self, documentation_type: str) -> str:
        mapping = {
            DocumentBatchDocumentationType.ID: "Реестр исполнительной документации",
            DocumentBatchDocumentationType.RD: "Реестр рабочей документации",
            DocumentBatchDocumentationType.ID_RD: "Реестр исполнительной и рабочей документации",
        }
        return mapping.get(documentation_type, "")

    @staticmethod
    def _format_date(value) -> str:
        if not value:
            return ""
        return value.strftime("%d.%m.%Y")