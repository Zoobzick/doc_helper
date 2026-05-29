from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from documents_app.models import (
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchLetterType,
    DocumentBatchProject,
    GeneratedDocument,
    GeneratedDocumentType,
)


@dataclass(slots=True)
class LetterProjectContext:
    """
    Один project внутри письма.

    project_id (id проекта)
    batch_project_id (id строки DocumentBatchProject)
    order (порядок проекта в batch)
    project (шифр проекта)
    plot (наименование участка/пикета)
    construction (конструкция)
    pages_count (сколько страниц печатается у реестра)
    pages_str (строка для шаблона письма, например "3 л.")
    """
    project_id: int
    batch_project_id: int
    order: int
    project: str
    plot: str
    construction: str
    pages_count: int
    pages_str: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "batch_project_id": self.batch_project_id,
            "order": self.order,
            "project": self.project,
            "plot": self.plot,
            "construction": self.construction,
            "pages_count": self.pages_count,
            "pages_str": self.pages_str,
            "composition_text": f"{self.project}. {self.plot}. {self.construction}",
        }


@dataclass(slots=True)
class LetterAppendixItemContext:
    """
    Одна строка приложения письма.

    number (сквозной номер строки приложения)
    project_id (id проекта)
    batch_project_id (id строки DocumentBatchProject)
    project_order (порядок проекта в batch)
    line_order_within_project (порядок строки внутри блока проекта: 1..N)
    item_type (тип строки приложения)
    text (готовый текст строки)
    project / plot / construction / pages_count / pages_str
    - дублируем в item для самодостаточности context-а
    - это делает контракт builder ↔ renderer устойчивее
    """
    number: int
    project_id: int
    batch_project_id: int
    project_order: int
    line_order_within_project: int
    item_type: str
    text: str
    project: str
    plot: str
    construction: str
    pages_count: int
    pages_str: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "project_id": self.project_id,
            "batch_project_id": self.batch_project_id,
            "project_order": self.project_order,
            "line_order_within_project": self.line_order_within_project,
            "item_type": self.item_type,
            "text": self.text,
            "project": self.project,
            "plot": self.plot,
            "construction": self.construction,
            "pages_count": self.pages_count,
            "pages_str": self.pages_str,
        }


class LetterContextBuilderError(Exception):
    """Базовая ошибка builder-а контекста письма."""


class LetterContextBuilderValidationError(LetterContextBuilderError):
    """Ошибка входных данных builder-а."""


class LetterContextBuilder:
    """
    Строит context для письма по batch.

    Что собирает:
    - шапку письма (number, dd, mm, yyyy, period, project_line_full_name, project_line_code)
    - список проектов в порядке DocumentBatchProject.order
    - body composition lines
    - приложение письма:
      - 2 строки на project для FOR_EXECUTION
      - 3 строки на project для TO_ARCHIVE

    ВАЖНО:
    - pages_str берётся только из уже сгенерированного GeneratedDocument(REGISTRY_XLSX).pages_count
    - период письма определяется по фактически попавшим в batch актам
    - builder не строит pages_count сам и не работает с XLSX/PDF напрямую
    """

    MONTHS_NOMINATIVE = {
        1: "январь",
        2: "февраль",
        3: "март",
        4: "апрель",
        5: "май",
        6: "июнь",
        7: "июль",
        8: "август",
        9: "сентябрь",
        10: "октябрь",
        11: "ноябрь",
        12: "декабрь",
    }

    MONTHS_GENITIVE = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    }

    def build(self, *, batch: DocumentBatch) -> dict[str, Any]:
        self._validate_batch_for_letter(batch=batch)

        batch_projects = self._get_batch_projects(batch=batch)
        if not batch_projects:
            raise LetterContextBuilderValidationError(
                "Нельзя построить письмо: в комплекте нет проектов."
            )

        batch_acts = self._get_batch_acts(batch=batch)
        if not batch_acts:
            raise LetterContextBuilderValidationError(
                "Нельзя построить письмо: в комплекте нет актов."
            )

        registry_documents_map = self._get_registry_documents_map(batch=batch)

        projects = self._build_projects_context(
            batch_projects=batch_projects,
            registry_documents_map=registry_documents_map,
        )

        appendix_items = self._build_appendix_items(
            projects=projects,
            letter_type=batch.letter_type,
        )

        first_project = batch_projects[0].project
        line = getattr(first_project, "line", None)

        placeholders = {
            "number": (batch.letter_number or "").strip(),
            "dd": f"{batch.letter_date.day:02d}",
            "mm": self._get_month_genitive(batch.letter_date.month),
            "yyyy": str(batch.letter_date.year),
            "period": self._build_period_text(batch_acts=batch_acts),
            "project_line_full_name": (getattr(line, "full_name", "") or "").strip(),
            "project_line_code": (getattr(line, "code", "") or "").strip(),
        }

        context = {
            "batch": {
                "id": batch.id,
                "uuid": str(batch.uuid),
                "title": batch.title,
                "letter_type": batch.letter_type,
                "letter_type_display": batch.get_letter_type_display(),
                "letter_number": (batch.letter_number or "").strip(),
                "letter_date": batch.letter_date.isoformat() if batch.letter_date else None,
                "documentation_type": batch.documentation_type,
                "documentation_type_display": batch.get_documentation_type_display(),
            },
            "placeholders": placeholders,
            "projects": [item.to_dict() for item in projects],
            "body": {
                "composition_lines": [
                    {
                        "project_id": item.project_id,
                        "batch_project_id": item.batch_project_id,
                        "order": item.order,
                        "text": f"{item.project}. {item.plot}. {item.construction}",
                        "project": item.project,
                        "plot": item.plot,
                        "construction": item.construction,
                    }
                    for item in projects
                ]
            },
            "appendix": {
                "items": [item.to_dict() for item in appendix_items],
                "items_count": len(appendix_items),
            },
            "summary": {
                "projects_count": len(projects),
                "acts_count": len(batch_acts),
                "appendix_items_count": len(appendix_items),
                "total_registry_pages_count": sum(item.pages_count for item in projects),
            },
        }
        return context

    def _validate_batch_for_letter(self, *, batch: DocumentBatch) -> None:
        if not (batch.letter_number or "").strip():
            raise LetterContextBuilderValidationError(
                "Нельзя построить письмо: не заполнен номер письма."
            )

        if not batch.letter_date:
            raise LetterContextBuilderValidationError(
                "Нельзя построить письмо: не заполнена дата письма."
            )

        if batch.letter_type not in {
            DocumentBatchLetterType.FOR_EXECUTION,
            DocumentBatchLetterType.TO_ARCHIVE,
        }:
            raise LetterContextBuilderValidationError(
                f"Неподдерживаемый тип письма: {batch.letter_type}"
            )

    def _get_batch_projects(self, *, batch: DocumentBatch) -> list[DocumentBatchProject]:
        return list(
            DocumentBatchProject.objects
            .filter(batch=batch)
            .select_related("project", "project__line", "project__plot")
            .order_by("order", "id")
        )

    def _get_batch_acts(self, *, batch: DocumentBatch) -> list[DocumentBatchAct]:
        return list(
            DocumentBatchAct.objects
            .filter(batch=batch)
            .select_related("act", "project")
            .order_by("project__id", "order", "id")
        )

    def _get_registry_documents_map(self, *, batch: DocumentBatch) -> dict[int, GeneratedDocument]:
        registry_documents = list(
            GeneratedDocument.objects
            .select_related("project")
            .filter(
                batch=batch,
                document_type=GeneratedDocumentType.REGISTRY_XLSX,
                project__isnull=False,
            )
            .order_by("project_id", "id")
        )

        result: dict[int, GeneratedDocument] = {}
        for document in registry_documents:
            if document.project_id not in result:
                result[document.project_id] = document
        return result

    def _build_projects_context(
        self,
        *,
        batch_projects: list[DocumentBatchProject],
        registry_documents_map: dict[int, GeneratedDocument],
    ) -> list[LetterProjectContext]:
        result: list[LetterProjectContext] = []

        for batch_project in batch_projects:
            project = batch_project.project
            registry_document = registry_documents_map.get(batch_project.project_id)

            if not registry_document:
                raise LetterContextBuilderValidationError(
                    f"Для проекта id={batch_project.project_id} не найден сгенерированный реестр XLSX."
                )

            pages_count = registry_document.pages_count
            if not isinstance(pages_count, int) or pages_count <= 0:
                raise LetterContextBuilderValidationError(
                    f"У реестра проекта id={batch_project.project_id} не заполнен корректный pages_count."
                )

            result.append(
                LetterProjectContext(
                    project_id=batch_project.project_id,
                    batch_project_id=batch_project.id,
                    order=batch_project.order,
                    project=(getattr(project, "full_code", "") or "").strip(),
                    plot=self._get_project_plot_full_name(project),
                    construction=self._get_project_construction(project),
                    pages_count=pages_count,
                    pages_str=self._format_pages_str(pages_count),
                )
            )

        return result

    def _build_appendix_items(
        self,
        *,
        projects: list[LetterProjectContext],
        letter_type: str,
    ) -> list[LetterAppendixItemContext]:
        items: list[LetterAppendixItemContext] = []
        item_number = 0

        for project in projects:
            if letter_type == DocumentBatchLetterType.FOR_EXECUTION:
                lines = self._build_for_execution_appendix_lines(project=project)
            elif letter_type == DocumentBatchLetterType.TO_ARCHIVE:
                lines = self._build_to_archive_appendix_lines(project=project)
            else:
                raise LetterContextBuilderValidationError(
                    f"Неподдерживаемый тип письма: {letter_type}"
                )

            for line_order_within_project, (item_type, text) in enumerate(lines, start=1):
                item_number += 1
                items.append(
                    LetterAppendixItemContext(
                        number=item_number,
                        project_id=project.project_id,
                        batch_project_id=project.batch_project_id,
                        project_order=project.order,
                        line_order_within_project=line_order_within_project,
                        item_type=item_type,
                        text=text,
                        project=project.project,
                        plot=project.plot,
                        construction=project.construction,
                        pages_count=project.pages_count,
                        pages_str=project.pages_str,
                    )
                )

        return items

    def _build_for_execution_appendix_lines(
        self,
        *,
        project: LetterProjectContext,
    ) -> list[tuple[str, str]]:
        return [
            (
                "registry",
                f"Реестр исполнительной документации по шифру {project.project} "
                f"(Папка №1) – {project.pages_str} в 2-х экземплярах;",
            ),
            (
                "documentation",
                f"Исполнительная документация согласно реестру по шифру {project.project}\n"
                f"(Папка №1) – 1 папка оригинал, 1 папка заверенная копия, "
                f"копия в электронном виде;",
            ),
        ]

    def _build_to_archive_appendix_lines(
        self,
        *,
        project: LetterProjectContext,
    ) -> list[tuple[str, str]]:
        return [
            (
                "registry",
                f"Реестр исполнительной документации по шифру {project.project} "
                f"– {project.pages_str} в 2-х экземплярах;",
            ),
            (
                "documentation",
                f"Исполнительная документация согласно реестру по шифру {project.project} "
                f"– оригинал в 1 экз., заверенная копия в 1 экз., "
                f"копия в электронном виде, включая схемы в DWG формате;",
            ),
            (
                "working_drawings",
                f"Исполнительные рабочие чертежи согласно реестра по шифру {project.project} "
                f"– заверенные копии в 2-х экземплярах, копия в электронном виде.",
            ),
        ]

    def _build_period_text(self, *, batch_acts: list[DocumentBatchAct]) -> str:
        month_points: list[tuple[int, int]] = []

        for batch_act in batch_acts:
            act = batch_act.act
            point_date = act.work_end_date or act.act_date
            if not point_date:
                continue
            month_points.append((point_date.year, point_date.month))

        if not month_points:
            raise LetterContextBuilderValidationError(
                "Нельзя определить период письма: у актов отсутствуют work_end_date/act_date."
            )

        month_points = sorted(set(month_points))
        start_year, start_month = month_points[0]
        end_year, end_month = month_points[-1]

        if (start_year, start_month) == (end_year, end_month):
            return f"{self._get_month_nominative(start_month)} {start_year}г."

        if start_year == end_year:
            return (
                f"с {self._get_month_genitive(start_month)} "
                f"по {self._get_month_genitive(end_month)} {start_year}г."
            )

        return (
            f"с {self._get_month_genitive(start_month)} {start_year}г. "
            f"по {self._get_month_genitive(end_month)} {end_year}г."
        )

    def _format_pages_str(self, pages_count: int) -> str:
        return f"{pages_count} л."

    def _get_project_plot_full_name(self, project) -> str:
        plot = getattr(project, "plot", None)
        if not plot:
            return ""
        return (getattr(plot, "full_name", "") or "").strip()

    def _get_project_construction(self, project) -> str:
        return (getattr(project, "construction", "") or "").strip()

    def _get_month_nominative(self, month: int) -> str:
        value = self.MONTHS_NOMINATIVE.get(month)
        if not value:
            raise LetterContextBuilderValidationError(
                f"Некорректный номер месяца: {month}"
            )
        return value

    def _get_month_genitive(self, month: int) -> str:
        value = self.MONTHS_GENITIVE.get(month)
        if not value:
            raise LetterContextBuilderValidationError(
                f"Некорректный номер месяца: {month}"
            )
        return value