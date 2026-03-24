from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.formula.translate import Translator
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet


@dataclass(slots=True)
class PlaceholderCellMatch:
    """
    Найденный плейсхолдер в workbook.

    sheet (лист Excel)
    row (номер строки)
    column (номер колонки)
    value (исходное значение ячейки)
    """
    sheet: Worksheet
    row: int
    column: int
    value: str


class RegistryXlsxRendererError(Exception):
    """Базовая ошибка рендера XLSX-реестра."""


class RegistryXlsxRendererValidationError(RegistryXlsxRendererError):
    """Ошибка структуры шаблона XLSX-реестра."""


class RegistryXlsxRenderer:
    """
    Рендерит XLSX-реестр по уже готовому context.

    Поддерживает:
    - замену шапочных плейсхолдеров
    - вставку табличной части по rows
    - сохранение исходного форматирования шаблона
    - bold для строк акта
    - перенос формулы в колонке "Страница"

    Ожидаемые специальные плейсхолдеры:
    - {{documents}}
    - {{sheets}}

    ВАЖНО:
    - оба плейсхолдера должны находиться на одной строке шаблона
    - documents/sheets — это anchors табличной части
    - значение для "Страница" не заполняем руками, а копируем формулу шаблонной строки
    - renderer НЕ строит context сам
    """

    DOCUMENTS_PLACEHOLDER = "{{documents}}"
    SHEETS_PLACEHOLDER = "{{sheets}}"

    HEADER_PLACEHOLDERS = {
        "{{tech_customer_org_short}}": "tech_customer_org_short",
        "{{builder_rep_org_short}}": "builder_rep_org_short",
        "{{other_rep_org_short}}": "other_rep_org_short",
        "{{contractor_rep_org_short}}": "contractor_rep_org_short",
        "{{project_line_full_name}}": "project_line_full_name",
        "{{project_stage_full_name}}": "project_stage_full_name",
        "{{project_full_code}}": "project_full_code",
        "{{project_plot_full_name}}": "project_plot_full_name",
        "{{project_construction}}": "project_construction",
        "{{executive_or_and_working_documentation_registry}}": "executive_or_and_working_documentation_registry",
    }

    def render(
        self,
        *,
        context: dict[str, Any],
        template_path: str | Path,
        output_path: str | Path,
    ) -> Path:
        """
        Публичная точка входа.

        Renderer работает только по уже собранному context.
        """
        return self.render_from_context(
            context=context,
            template_path=template_path,
            output_path=output_path,
        )

    def render_from_context(
        self,
        *,
        context: dict[str, Any],
        template_path: str | Path,
        output_path: str | Path,
    ) -> Path:
        """
        Рендерит XLSX по уже готовому context.
        """
        template_path = Path(template_path)
        output_path = Path(output_path)

        if not template_path.exists():
            raise RegistryXlsxRendererValidationError(
                f"Шаблон не найден: {template_path}"
            )

        workbook = load_workbook(template_path)
        worksheet = self._resolve_target_sheet(workbook)

        placeholders = context.get("placeholders", {})
        rows = context.get("rows", [])

        if not rows:
            raise RegistryXlsxRendererValidationError(
                "Контекст реестра не содержит строк rows."
            )

        self._replace_header_placeholders(
            worksheet=worksheet,
            placeholders=placeholders,
        )

        documents_match = self._find_placeholder_cell(
            worksheet=worksheet,
            placeholder=self.DOCUMENTS_PLACEHOLDER,
        )
        sheets_match = self._find_placeholder_cell(
            worksheet=worksheet,
            placeholder=self.SHEETS_PLACEHOLDER,
        )

        if documents_match.row != sheets_match.row:
            raise RegistryXlsxRendererValidationError(
                "Плейсхолдеры {{documents}} и {{sheets}} должны находиться на одной строке."
            )

        template_row_idx = documents_match.row
        documents_col_idx = documents_match.column
        sheets_col_idx = sheets_match.column

        if len(rows) > 1:
            worksheet.insert_rows(template_row_idx + 1, amount=len(rows) - 1)
            self._replicate_template_row_block(
                worksheet=worksheet,
                template_row_idx=template_row_idx,
                rows_count=len(rows),
            )

        for offset, row_context in enumerate(rows):
            target_row_idx = template_row_idx + offset
            self._render_single_registry_row(
                worksheet=worksheet,
                target_row_idx=target_row_idx,
                template_row_idx=template_row_idx,
                documents_col_idx=documents_col_idx,
                sheets_col_idx=sheets_col_idx,
                row_context=row_context,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output_path)

        return output_path

    def _resolve_target_sheet(self, workbook):
        """
        Пока используем active sheet.

        Если позже в шаблоне появится отдельное имя листа,
        сюда можно добавить явный выбор по имени.
        """
        return workbook.active

    def _replace_header_placeholders(
        self,
        *,
        worksheet: Worksheet,
        placeholders: dict[str, Any],
    ) -> None:
        """
        Заменяет шапочные плейсхолдеры по всему листу.

        Стиль ячеек не меняется — меняется только value.
        """
        placeholder_value_map = {
            placeholder: str(placeholders.get(context_key, "") or "")
            for placeholder, context_key in self.HEADER_PLACEHOLDERS.items()
        }

        for row in worksheet.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                if not isinstance(cell.value, str):
                    continue

                new_value = cell.value
                changed = False

                for placeholder, replacement in placeholder_value_map.items():
                    if placeholder in new_value:
                        new_value = new_value.replace(placeholder, replacement)
                        changed = True

                if changed:
                    cell.value = new_value

    def _find_placeholder_cell(
        self,
        *,
        worksheet: Worksheet,
        placeholder: str,
    ) -> PlaceholderCellMatch:
        """
        Ищет ячейку, которая содержит точный плейсхолдер.
        """
        for row in worksheet.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                if cell.value == placeholder:
                    return PlaceholderCellMatch(
                        sheet=worksheet,
                        row=cell.row,
                        column=cell.column,
                        value=cell.value,
                    )

        raise RegistryXlsxRendererValidationError(
            f"В шаблоне не найден плейсхолдер {placeholder}"
        )

    def _replicate_template_row_block(
        self,
        *,
        worksheet: Worksheet,
        template_row_idx: int,
        rows_count: int,
    ) -> None:
        """
        Копирует стиль/формулы/мерджи шаблонной строки на вставленные строки.

        Логика:
        - template_row_idx уже существует
        - ниже уже вставлены новые пустые строки
        - нужно протянуть на них:
          - стили ячеек
          - высоту строки
          - формулы/значения шаблонной строки
          - merged ranges вида B:D на шаблонной строке
        """
        max_col = worksheet.max_column
        template_row_height = worksheet.row_dimensions[template_row_idx].height

        merged_ranges_for_template_row = self._get_single_row_merged_ranges(
            worksheet=worksheet,
            row_idx=template_row_idx,
        )

        for target_row_idx in range(template_row_idx + 1, template_row_idx + rows_count):
            if template_row_height is not None:
                worksheet.row_dimensions[target_row_idx].height = template_row_height

            for col_idx in range(1, max_col + 1):
                source_cell = worksheet.cell(row=template_row_idx, column=col_idx)
                target_cell = worksheet.cell(row=target_row_idx, column=col_idx)

                if isinstance(source_cell, MergedCell):
                    continue

                self._copy_cell_style(source_cell=source_cell, target_cell=target_cell)
                self._copy_cell_value_with_formula_translation(
                    source_cell=source_cell,
                    target_cell=target_cell,
                )

            for merged_range in merged_ranges_for_template_row:
                row_offset = target_row_idx - template_row_idx
                new_min_row = merged_range.min_row + row_offset
                new_max_row = merged_range.max_row + row_offset
                new_range = (
                    f"{worksheet.cell(new_min_row, merged_range.min_col).coordinate}:"
                    f"{worksheet.cell(new_max_row, merged_range.max_col).coordinate}"
                )
                worksheet.merge_cells(new_range)

    def _get_single_row_merged_ranges(
        self,
        *,
        worksheet: Worksheet,
        row_idx: int,
    ):
        """
        Возвращает merged ranges, которые полностью лежат в одной template-строке.

        Для нашего шаблона это нужно, чтобы копировать объединение B:D на каждую новую строку.
        """
        result = []
        for merged_range in worksheet.merged_cells.ranges:
            if merged_range.min_row == row_idx and merged_range.max_row == row_idx:
                result.append(merged_range)
        return result

    def _copy_cell_style(self, *, source_cell, target_cell) -> None:
        """
        Копирует стиль ячейки без изменения значения.
        """
        if source_cell.has_style:
            target_cell._style = copy(source_cell._style)

        if source_cell.number_format:
            target_cell.number_format = source_cell.number_format

        if source_cell.font:
            target_cell.font = copy(source_cell.font)

        if source_cell.fill:
            target_cell.fill = copy(source_cell.fill)

        if source_cell.border:
            target_cell.border = copy(source_cell.border)

        if source_cell.alignment:
            target_cell.alignment = copy(source_cell.alignment)

        if source_cell.protection:
            target_cell.protection = copy(source_cell.protection)

    def _copy_cell_value_with_formula_translation(self, *, source_cell, target_cell) -> None:
        """
        Копирует значение шаблонной ячейки.

        Если в source формула, пытаемся сдвинуть относительные ссылки на новую строку.
        Это особенно важно для колонки "Страница".
        """
        source_value = source_cell.value

        if not isinstance(source_value, str) or not source_value.startswith("="):
            target_cell.value = source_value
            return

        try:
            translated_formula = Translator(
                formula=source_value,
                origin=source_cell.coordinate,
            ).translate_formula(target_cell.coordinate)
            target_cell.value = translated_formula
        except Exception:
            target_cell.value = source_value

    def _render_single_registry_row(
        self,
        *,
        worksheet: Worksheet,
        target_row_idx: int,
        template_row_idx: int,
        documents_col_idx: int,
        sheets_col_idx: int,
        row_context: dict[str, Any],
    ) -> None:
        """
        Заполняет одну строку реестра.
        """
        documents_cell = worksheet.cell(row=target_row_idx, column=documents_col_idx)
        sheets_cell = worksheet.cell(row=target_row_idx, column=sheets_col_idx)

        documents_cell.value = row_context.get("document_text", "")
        sheets_cell.value = row_context.get("sheets_count", "")

        if row_context.get("is_act_row"):
            self._apply_bold_to_row(
                worksheet=worksheet,
                row_idx=target_row_idx,
            )

    def _apply_bold_to_row(self, *, worksheet: Worksheet, row_idx: int) -> None:
        """
        Делает всю строку жирной.
        """
        for col_idx in range(1, worksheet.max_column + 1):
            cell = worksheet.cell(row=row_idx, column=col_idx)
            if isinstance(cell, MergedCell):
                continue

            current_font = copy(cell.font) if cell.font else Font()
            current_font.bold = True
            cell.font = current_font