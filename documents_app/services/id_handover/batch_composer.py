from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, QuerySet

from acts_app.models import Act
from projects_app.models import Project

from documents_app.models import (
    DocumentBatch,
    DocumentBatchAct,
    DocumentBatchActSource,
    DocumentBatchGenerationMode,
    DocumentBatchLetterType,
    DocumentBatchProject,
    DocumentBatchProjectScope,
    DocumentBatchSelectionMode, DocumentBatchDocumentationType,
)

User = get_user_model()


@dataclass(slots=True)
class BatchCreateParams:
    """
    Параметры создания комплекта сдачи ИД.

    created_by (пользователь, создающий комплект)
    selection_mode (режим отбора: all_time/range)
    generation_mode (режим генерации: registry_only/letter_only/full_set)
    letter_type (тип письма)
    documentation_type (сдача ИД/РД/ИДиРД)
    project_scope (охват проектов: one_project/multi_project/auto_by_period)
    title (необязательное название комплекта)
    comment (необязательный комментарий)
    month_from (нижняя граница периода в формате MM.YYYY)
    month_to (верхняя граница периода в формате MM.YYYY)
    project_ids (список id проектов для one_project/multi_project)
    """

    created_by: User
    selection_mode: str
    generation_mode: str
    letter_type: str
    documentation_type: str
    project_scope: str
    title: str = ""
    comment: str = ""
    month_from: str = ""
    month_to: str = ""
    letter_number: str = ""
    letter_date: date | None = None
    project_ids: Sequence[int] | None = None


class DocumentBatchComposerError(Exception):
    """Базовая ошибка сервиса формирования состава комплекта."""


class DocumentBatchComposerValidationError(DocumentBatchComposerError):
    """Ошибка валидации входных параметров сервиса."""


class DocumentBatchComposer:
    """
    Сервис формирования состава комплекта сдачи ИД.

    Что делает:
    1. Создаёт DocumentBatch
    2. Определяет состав проектов
    3. Определяет состав актов по каждому проекту
    4. Создаёт DocumentBatchProject
    5. Создаёт DocumentBatchAct

    ВАЖНО:
    - один реестр = один project
    - один и тот же Act может войти в несколько project внутри одного batch,
      если он привязан к нескольким Project через Act.projects
    - порядок актов внутри проекта:
      1) по work_end_date ASC
      2) при равенстве по id ASC
    - пока только состав batch, без GeneratedDocument и без preview snapshot
    """

    def __init__(self, params: BatchCreateParams):
        self.params = params

    @transaction.atomic
    def create_batch(self) -> DocumentBatch:
        """
        Полный сценарий создания комплекта.
        """
        self._validate_input_params()

        batch = DocumentBatch(
            title=(self.params.title or "").strip(),
            comment=(self.params.comment or "").strip(),
            selection_mode=self.params.selection_mode,
            month_from=(self.params.month_from or "").strip(),
            month_to=(self.params.month_to or "").strip(),
            generation_mode=self.params.generation_mode,
            letter_type=self.params.letter_type,
            letter_number=(self.params.letter_number or "").strip(),
            letter_date=self.params.letter_date,
            documentation_type=self.params.documentation_type,
            project_scope=self.params.project_scope,
            created_by=self.params.created_by,
        )
        batch.full_clean()
        batch.save()

        projects = self._resolve_projects()
        if not projects:
            raise DocumentBatchComposerValidationError(
                "Не найдено ни одного проекта для формирования комплекта."
            )

        self._create_batch_projects(batch=batch, projects=projects)

        acts_by_project = self._resolve_acts_by_project(projects=projects)
        self._create_batch_acts(batch=batch, acts_by_project=acts_by_project)

        return batch

    def _validate_input_params(self) -> None:
        """
        Валидируем входные параметры сервиса до записи в БД.
        """
        valid_selection_modes = {
            DocumentBatchSelectionMode.ALL_TIME,
            DocumentBatchSelectionMode.RANGE,
        }
        valid_generation_modes = {
            DocumentBatchGenerationMode.REGISTRY_ONLY,
            DocumentBatchGenerationMode.LETTER_ONLY,
            DocumentBatchGenerationMode.FULL_SET,
        }
        valid_letter_types = {
            DocumentBatchLetterType.FOR_EXECUTION,
            DocumentBatchLetterType.TO_ARCHIVE,
        }
        valid_project_scopes = {
            DocumentBatchProjectScope.ONE_PROJECT,
            DocumentBatchProjectScope.MULTI_PROJECT,
            DocumentBatchProjectScope.AUTO_BY_PERIOD,
        }

        valid_documentation_types = {
            DocumentBatchDocumentationType.ID,
            DocumentBatchDocumentationType.RD,
            DocumentBatchDocumentationType.ID_RD,
        }

        if self.params.selection_mode not in valid_selection_modes:
            raise DocumentBatchComposerValidationError("Некорректный selection_mode.")

        if self.params.generation_mode not in valid_generation_modes:
            raise DocumentBatchComposerValidationError("Некорректный generation_mode.")

        if self.params.letter_type not in valid_letter_types:
            raise DocumentBatchComposerValidationError("Некорректный letter_type.")

        if self.params.project_scope not in valid_project_scopes:
            raise DocumentBatchComposerValidationError("Некорректный project_scope.")

        if self.params.documentation_type not in valid_documentation_types:
            raise DocumentBatchComposerValidationError("Некорректный documentation_type.")

        project_ids = list(self.params.project_ids or [])

        if self.params.project_scope == DocumentBatchProjectScope.ONE_PROJECT:
            if len(project_ids) != 1:
                raise DocumentBatchComposerValidationError(
                    "Для режима 'one_project' нужно передать ровно один project_id."
                )

        if self.params.project_scope == DocumentBatchProjectScope.MULTI_PROJECT:
            if not project_ids:
                raise DocumentBatchComposerValidationError(
                    "Для режима 'multi_project' нужно передать хотя бы один project_id."
                )

        if self.params.project_scope == DocumentBatchProjectScope.AUTO_BY_PERIOD:
            if project_ids:
                raise DocumentBatchComposerValidationError(
                    "Для режима 'auto_by_period' project_ids передавать не нужно."
                )

    def _resolve_projects(self) -> list[Project]:
        """
        Возвращает список проектов, которые входят в комплект.
        """
        if self.params.project_scope == DocumentBatchProjectScope.ONE_PROJECT:
            return list(
                Project.objects.filter(id__in=self.params.project_ids).order_by("id")
            )

        if self.params.project_scope == DocumentBatchProjectScope.MULTI_PROJECT:
            return list(
                Project.objects.filter(id__in=self.params.project_ids).order_by("id")
            )

        act_qs = self._build_base_act_queryset()

        project_ids = (
            Project.objects.filter(acts__in=act_qs)
            .order_by()
            .values_list("id", flat=True)
            .distinct()
        )

        return list(Project.objects.filter(id__in=project_ids).order_by("id"))

    def _resolve_acts_by_project(self, projects: Sequence[Project]) -> dict[int, list[Act]]:
        """
        Возвращает словарь:
        {
            project_id: [act1, act2, ...]
        }

        Для каждого проекта выбираются только те акты,
        у которых этот проект присутствует в Act.projects.

        Сортировка внутри проекта:
        - work_end_date ASC
        - id ASC
        """
        result: dict[int, list[Act]] = {}
        base_qs = self._build_base_act_queryset()

        for project in projects:
            project_acts = list(
                base_qs.filter(projects=project)
                .distinct()
                .order_by("work_end_date", "id")
            )
            result[project.id] = project_acts

        return result

    def _build_base_act_queryset(self) -> QuerySet[Act]:
        """
        Базовый queryset актов по параметрам периода.

        ВАЖНО:
        В модели Act период хранится как:
        - act_year (int)
        - act_month (int)

        Поэтому строку MM.YYYY сначала переводим в:
        - year
        - month
        """
        qs = Act.objects.all()

        if self.params.selection_mode == DocumentBatchSelectionMode.ALL_TIME:
            return qs

        from_year, from_month = self._parse_month_code(self.params.month_from)
        to_year, to_month = self._parse_month_code(self.params.month_to)

        return qs.filter(
            Q(act_year__gt=from_year) | Q(act_year=from_year, act_month__gte=from_month),
            Q(act_year__lt=to_year) | Q(act_year=to_year, act_month__lte=to_month),
        )

    def _create_batch_projects(self, batch: DocumentBatch, projects: Sequence[Project]) -> None:
        """
        Создаёт записи DocumentBatchProject.
        """
        batch_project_objects: list[DocumentBatchProject] = []

        for index, project in enumerate(projects, start=1):
            batch_project_objects.append(
                DocumentBatchProject(
                    batch=batch,
                    project=project,
                    order=index,
                )
            )

        DocumentBatchProject.objects.bulk_create(batch_project_objects)

    def _create_batch_acts(
        self,
        batch: DocumentBatch,
        acts_by_project: dict[int, list[Act]],
    ) -> None:
        """
        Создаёт записи DocumentBatchAct.

        Порядок актов считается строго внутри проекта.
        Один и тот же act может появиться в нескольких проектах batch,
        если он связан с несколькими Project через M2M.
        """
        batch_act_objects: list[DocumentBatchAct] = []

        for project_id, acts in acts_by_project.items():
            for order, act in enumerate(acts, start=1):
                batch_act_objects.append(
                    DocumentBatchAct(
                        batch=batch,
                        project_id=project_id,
                        act=act,
                        order=order,
                        source=DocumentBatchActSource.AUTO,
                        added_by=self.params.created_by,
                    )
                )

        DocumentBatchAct.objects.bulk_create(batch_act_objects)

    @staticmethod
    def _parse_month_code(month_code: str) -> tuple[int, int]:
        """
        Преобразует 'MM.YYYY' -> (year, month)

        Пример:
        '03.2026' -> (2026, 3)
        """
        month_str, year_str = month_code.split(".")
        return int(year_str), int(month_str)