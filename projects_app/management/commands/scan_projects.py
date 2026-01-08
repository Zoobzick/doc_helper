import os
import re
from django.core.management.base import BaseCommand
from django.db import transaction

from projects_app.models import (
    Project,
    ProjectRevision,
    Designer,
    Line,
    Stage,
    Plot,
    Section,
    DesignStage,
)

import json

from django.conf import settings


def normalize_code(value: str) -> str:
    # (value) строка-код проекта
    # убираем пробелы и приводим разные тире к обычному дефису
    return value.replace('–', '-').replace('—', '-').strip()


def debug_print(cmd, message: str):
    # (cmd) self из команды Django
    # (message) строка для вывода
    cmd.stdout.write(cmd.style.WARNING(f'[DEBUG] {message}'))


PROJECTS_DIR = settings.PROJECTS_DIR
PROJECTS_JSON = settings.PROJECTS_JSON

with open(PROJECTS_JSON, 'r', encoding='utf-8') as f:
    PROJECTS_META = json.load(f)

# Регулярка для конца имени: КЖ29-01.1.pdf
REVISION_RE = re.compile(
    r'(?P<section>[А-ЯA-Z]{2,3})(?P<number>\d+)(?:-(?P<revision>[\d.]+))?$'
)


class Command(BaseCommand):
    help = 'Сканирует папку проектов и обновляет БД'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('🔍 Сканирование проектов...'))

        files = self._collect_files(PROJECTS_DIR)

        self.stdout.write(f'Найдено файлов: {len(files)}')

        for file_path in files:
            try:
                self.process_file(file_path)
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f'Ошибка: {file_path} → {e}')
                )

        self.stdout.write(self.style.SUCCESS('✅ Сканирование завершено'))

    # ------------------------------------------------------------------

    def _collect_files(self, root: str) -> list[str]:
        """
        (root) корневая папка проектов
        Возвращает список путей ко всем PDF
        """
        result = []

        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                if name.lower().endswith('.pdf'):
                    result.append(os.path.join(dirpath, name))

        return result

    # ------------------------------------------------------------------

    @transaction.atomic
    def process_file(self, file_path: str):
        file_name = os.path.basename(file_path)
        base_name = os.path.splitext(file_name)[0]

        parts = base_name.split('-')

        if len(parts) < 7:
            raise ValueError('Слишком короткое имя файла')

        # --- базовые коды ---
        designer_code = parts[0]
        line_code = parts[1]
        design_stage_code = parts[2]
        stage_code = parts[3]
        plot_code = parts[4]

        # --- хвост ---
        tail = '-'.join(parts[5:])

        # --- КЖ29 / КЖ29-01.1 ---
        match = REVISION_RE.search(tail)
        if not match:
            raise ValueError('Не удалось распарсить раздел проекта')

        section_code = match.group('section')
        number = int(match.group('number'))
        revision = match.group('revision') or '00'

        # --- внутренний код (001-01) ---
        internal_code = tail[: match.start()].strip('-')

        # --- справочники ---
        designer = self._get_or_mark(Designer, designer_code)
        line = self._get_or_mark(Line, line_code)
        design_stage = self._get_or_mark(DesignStage, design_stage_code)
        stage = self._get_or_mark(Stage, stage_code)
        plot = self._get_or_mark(Plot, plot_code)
        section = self._get_or_mark(Section, section_code)

        needs_review = any(
            obj is None for obj in
            [designer, line, design_stage, stage, plot, section]
        )

        # --- базовый шифр проекта (БЕЗ ревизии) ---
        base_project_code = (
            f'{designer_code}-{line_code}-{design_stage_code}-'
            f'{stage_code}-{plot_code}-'
            f'{internal_code + "-" if internal_code else ""}'
            f'{section_code}{number}'
        )

        debug_print(self, f'FILE: {file_name}')
        debug_print(self, f'BASE_CODE (raw): [{base_project_code}]')
        debug_print(self, f'BASE_CODE (norm): [{normalize_code(base_project_code)}]')

        # --- конструкция из JSON ---
        construction = ""

        # (normalized_meta) нормализованные ключи JSON, (key) нормализованный ключ проекта
        normalized_meta = {normalize_code(k): v for k, v in PROJECTS_META.items()}
        key = normalize_code(base_project_code)

        # (meta) словарь метаданных по проекту из JSON или None
        meta = normalized_meta.get(key)

        debug_print(self, f'JSON HAS KEY? {"YES" if meta else "NO"}')

        if not meta:
            # если ключа нет вообще — точно надо вручную
            needs_review = True
        else:
            # (construction) строка конструкции из JSON (может быть пустой)
            construction = (meta or {}).get("construction", "") or ""

            # ключ есть, но конструкция пустая/пробелы => тоже надо вручную
            if not construction.strip():
                needs_review = True

        # --- Project (КЖ29) ---
        project, created = Project.objects.get_or_create(
            designer=designer,
            line=line,
            design_stage=design_stage,
            stage=stage,
            plot=plot,
            section=section,
            number=number,
            internal_code=internal_code,
            defaults={
                'construction': construction,
                'needs_review': needs_review,
            }
        )
        debug_print(
            self,
            f'CONSTRUCTION DB=[{project.construction!r}] JSON=[{construction!r}]'
        )
        # если есть конструкция в JSON и она отличается от БД — обновляем
        if construction and project.construction != construction:
            project.construction = construction
            project.needs_review = False
            project.save(update_fields=['construction', 'needs_review'])

        # --- ProjectRevision ---
        revision_obj, _ = ProjectRevision.objects.get_or_create(
            project=project,
            revision=revision,
            defaults={
                'file_name': file_name,
                'file_path': file_path,
            }
        )

        # --- актуальная версия ---
        ProjectRevision.objects.filter(project=project).update(is_latest=False)
        revision_obj.is_latest = True
        revision_obj.save(update_fields=['is_latest'])

        self.stdout.write(
            self.style.SUCCESS(
                f'✔ {base_project_code}-{revision}'
            )
        )

    # ------------------------------------------------------------------

    def _get_or_mark(self, model, code: str):
        """
        Пытается найти объект справочника по code.
        Если не найден — возвращает None.
        """
        try:
            return model.objects.get(code=code)
        except model.DoesNotExist:
            return None
