import os
from datetime import datetime
import hashlib
from django.conf import settings
from django.db import transaction
from ..models import Material, Category, Passport


class FileProcessor:
    """
    Упрощенный сервис для обработки файлов паспортов
    """

    def __init__(self, parser_service):
        self.parser = parser_service
        self.base_path = getattr(settings, 'PASSPORTS_DIR', None)

    def process_all_files(self):
        """
        Основной метод обработки
        """
        if not self.base_path or not os.path.exists(self.base_path):
            raise Exception(f"Директория не существует: {self.base_path}")

        # Получаем все файлы
        all_files = self.parser.get_all_files(self.base_path)

        stats = {
            'total': len(all_files),
            'saved': 0,
            'updated': 0,
            'errors': [],
            'materials_created': 0,
        }

        # Кэш материалов
        material_cache = {}

        with transaction.atomic():
            for file_path, filename in all_files:
                try:
                    # Парсим имя файла
                    parsed_data = self.parser.parse_filename(filename)
                    if not parsed_data:
                        stats['errors'].append(f"{filename}: Не удалось распарсить")
                        continue

                    # Получаем или создаем материал
                    material_name = parsed_data.get('material_name') or "Неизвестный материал"
                    material = self._get_or_create_material(material_name, material_cache, stats)

                    # Подготовка данных
                    document_name = parsed_data.get('document_name', filename)
                    passport_number = parsed_data.get('passport_number', '')
                    document_date = parsed_data.get('document_date') or datetime.now().date()

                    # Генерация номера если нет
                    if not passport_number:
                        passport_number = f"FILE_{os.path.splitext(filename)[0][:50]}"

                    # Проверяем существование
                    existing = Passport.objects.filter(
                        material=material,
                        document_number=passport_number
                    ).first()

                    if existing:
                        # Обновляем
                        existing.document_name = document_name
                        existing.document_date = document_date
                        existing.file_name = filename
                        existing.file_path = file_path
                        existing.save()
                        stats['updated'] += 1
                    else:
                        # Создаем новый
                        Passport.objects.create(
                            material=material,
                            document_name=document_name,
                            document_number=passport_number,
                            document_date=document_date,
                            file_name=filename,
                            file_path=file_path
                        )
                        stats['saved'] += 1

                except Exception as e:
                    stats['errors'].append(f"{filename}: {str(e)}")

        return stats

    def _get_or_create_material(self, material_name, cache, stats):
        """
        Получает или создает материал
        """
        material_name_clean = material_name.strip().title()

        if material_name_clean not in cache:
            material_obj, created = Material.objects.get_or_create(
                name=material_name_clean,
                defaults={'name': material_name_clean}
            )
            cache[material_name_clean] = material_obj
            if created:
                stats['materials_created'] += 1

        return cache[material_name_clean]