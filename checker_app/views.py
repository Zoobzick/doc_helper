import os
from datetime import datetime

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.views import View

from .services.checker_service import CheckService
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .forms import DocumentStructureUploadForm

from .utils.docx_structure import extract_docx_structure
from .utils.xlsx_structure import extract_xlsx_structure

MONTHS = {
    '01': 'Январь', '02': 'Февраль', '03': 'Март', '04': 'Апрель',
    '05': 'Май', '06': 'Июнь', '07': 'Июль', '08': 'Август',
    '09': 'Сентябрь', '10': 'Октябрь', '11': 'Ноябрь', '12': 'Декабрь'
}


class CheckActsView(PermissionRequiredMixin, View):
    permission_required = "checker_app.check_projects_and_folder_page"
    raise_exception = True

    def get(self, request):
        month = request.GET.get('month', '10')  # по умолчанию октябрь
        year = request.GET.get('year', '2025')  # по умолчанию 2025 год

        try:
            check_service = CheckService(month, year)

            all_files = check_service.get_list_of_files()
            path_and_projects = check_service.get_projects_and_path(all_files)

            results = check_service.check_project_and_path(
                path_and_projects,
                verbose=False
            )

            total = results['total']
            matches_percentage = (results['matches'] / total * 100) if total > 0 else 0
            mismatches_percentage = (results['mismatches'] / total * 100) if total > 0 else 0

            context = {
                'total_files': results['total'],
                'matches': results['matches'],
                'mismatches': results['mismatches'],
                'matches_percentage': matches_percentage,
                'mismatches_percentage': mismatches_percentage,
                'check_details': results['details'],
                'month': month,
                'year': year,
                'months': MONTHS,
                'show_all_files': request.GET.get('show_all', False),
            }

        except Exception as e:
            context = {
                'error': str(e),
                'month': month,
                'year': year,
                'months': MONTHS,
            }

        return render(request, 'checker_app/check_projects_and_folder.html', context)


class CheckDocActsView(PermissionRequiredMixin, View):
    permission_required = "checker_app.check_doc_acts_page"
    raise_exception = True

    def get(self, request):
        month = request.GET.get('month', '10')
        year = request.GET.get('year', '2025')

        try:
            check_service = CheckService(month, year)

            start_directory = check_service.full_path
            doc_act_files = check_service.find_act_doc_files(start_directory)

            files_info = []
            directories_set = set()

            for file_path in doc_act_files:
                file_name = os.path.basename(file_path)
                relative_path = os.path.relpath(file_path, start_directory)
                directory = os.path.dirname(relative_path)

                if directory:
                    directories_set.add(directory)
                else:
                    directories_set.add('Корневая папка')

                try:
                    file_size = os.path.getsize(file_path)
                    if file_size < 1024:
                        size_str = f"{file_size} Б"
                    elif file_size < 1024 * 1024:
                        size_str = f"{file_size / 1024:.1f} КБ"
                    else:
                        size_str = f"{file_size / (1024 * 1024):.1f} МБ"
                except Exception:
                    size_str = "Неизвестно"

                try:
                    mtime = os.path.getmtime(file_path)
                    modified_date = datetime.fromtimestamp(mtime).strftime('%d.%m.%Y %H:%M')
                except Exception:
                    modified_date = "Неизвестно"

                files_info.append({
                    'file_path': file_path,
                    'file_name': file_name,
                    'directory': directory if directory else 'Корневая папка',
                    'size': size_str,
                    'modified': modified_date,
                    'is_danger': True,
                })

            files_info.sort(key=lambda x: x['file_path'])

            context = {
                'month': month,
                'year': year,
                'months': MONTHS,
                'start_directory': start_directory,
                'total_files': len(doc_act_files),
                'directories_count': len(directories_set),
                'files_info': files_info,
                'has_doc_files': len(doc_act_files) > 0,
            }

        except Exception as e:
            context = {
                'error': str(e),
                'month': month,
                'year': year,
                'months': MONTHS,
            }

        return render(request, 'checker_app/check_act_doc.html', context)


def _list_files(base_dir: Path, suffix: str) -> list[str]:
    # (base_dir) — каталог с шаблонами
    # (suffix) — ".docx" или ".xlsx"
    if not base_dir.exists():
        return []
    return sorted([p.name for p in base_dir.glob(f"*{suffix}")])


def _safe_resolve(base_dir: Path, filename: str, allowed_suffix: str) -> Path:
    # (filename) — имя файла без путей, например "test.docx"
    if "/" in filename or "\\" in filename:
        raise Http404("Некорректное имя файла")

    full_path = (base_dir / filename).resolve()
    if base_dir not in full_path.parents:
        raise Http404("Недоступный путь")

    if not full_path.exists() or full_path.suffix.lower() != allowed_suffix:
        raise Http404("Файл не найден")

    return full_path


def _get_templates_dir(doc_type: str) -> Path:
    # (doc_type) — "docx" или "xlsx"
    if doc_type == "docx":
        return Path(settings.DOCX_TEMPLATES_DIR)  # (..) папка docx-шаблонов
    if doc_type == "xlsx":
        return Path(settings.XLSX_TEMPLATES_DIR)  # (..) папка xlsx-шаблонов
    raise Http404("Неизвестный тип документа")


@login_required
@permission_required("checker_app.view_doc_structure", raise_exception=True)
@require_http_methods(["GET", "POST"])
def act_structure_page(request):
    # (doc_type) — выбранный тип структуры: docx/xlsx
    doc_type = (request.GET.get("type") or "docx").strip().lower()
    if doc_type not in ("docx", "xlsx"):
        doc_type = "docx"

    docx_dir = _get_templates_dir("docx")  # (docx_dir) — каталог docx
    xlsx_dir = _get_templates_dir("xlsx")  # (xlsx_dir) — каталог xlsx

    available_docx = _list_files(docx_dir, ".docx")  # (available_docx) — список docx шаблонов
    available_xlsx = _list_files(xlsx_dir, ".xlsx")  # (available_xlsx) — список xlsx шаблонов

    selected = (request.GET.get("file") or "").strip()  # (selected) — выбранный файл из списка слева

    # результаты (под разные типы)
    docx_paragraphs = []  # (docx_paragraphs) — параграфы docx
    docx_cells = []  # (docx_cells) — ячейки таблиц docx

    xlsx_sheets = []  # (xlsx_sheets) — список листов xlsx
    xlsx_cells = []  # (xlsx_cells) — непустые ячейки xlsx

    shown_filename = None  # (shown_filename) — что реально сейчас показано
    upload_form = DocumentStructureUploadForm(initial={"doc_type": doc_type})

    # --- POST: загрузка файла (не из папки шаблонов) ---
    if request.method == "POST":
        upload_form = DocumentStructureUploadForm(request.POST, request.FILES)
        if upload_form.is_valid():
            chosen_type = upload_form.cleaned_data["doc_type"]  # (chosen_type) — выбранный тип в форме
            f = upload_form.cleaned_data["file"]  # (f) — UploadedFile

            if chosen_type == "docx":
                docx_paragraphs, docx_cells = extract_docx_structure(f.file)
            else:
                xlsx_sheets, xlsx_cells = extract_xlsx_structure(f.file)

            doc_type = chosen_type
            shown_filename = f.name

    # --- GET: выбор файла из каталога шаблонов ---
    elif selected:
        allowed_suffix = f".{doc_type}"  # (allowed_suffix) — ".docx" или ".xlsx"
        base_dir = _get_templates_dir(doc_type)  # (base_dir) — каталог под выбранный тип
        file_path = _safe_resolve(base_dir, selected, allowed_suffix)

        if doc_type == "docx":
            docx_paragraphs, docx_cells = extract_docx_structure(str(file_path))
        else:
            xlsx_sheets, xlsx_cells = extract_xlsx_structure(str(file_path))

        shown_filename = selected

    context = {
        "doc_type": doc_type,

        "available_docx": available_docx,
        "available_xlsx": available_xlsx,

        "shown_filename": shown_filename,

        # docx result
        "paragraphs": docx_paragraphs,
        "cells": docx_cells,

        # xlsx result
        "sheets": xlsx_sheets,
        "xlsx_cells": xlsx_cells,

        "upload_form": upload_form,
    }
    return render(request, "checker_app/act_structure_page.html", context)


MONTHS_DIG = {
    "01": "Январь", "02": "Февраль", "03": "Март", "04": "Апрель",
    "05": "Май", "06": "Июнь", "07": "Июль", "08": "Август",
    "09": "Сентябрь", "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь",
}


