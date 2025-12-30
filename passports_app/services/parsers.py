import os
from datetime import datetime


def parse_filename(filename):
    """
    Универсальный парсер имен файлов
    Возвращает словарь с данными или None
    """
    if not filename:
        return None

    name_without_ext = os.path.splitext(filename)[0]

    # Пытаемся распарсить PDF формат
    if filename.lower().endswith('.pdf'):
        try:
            if '(' in name_without_ext and ')' in name_without_ext and '№' in name_without_ext and ' от ' in name_without_ext:
                mat_name = name_without_ext.split('(')[0].strip()
                in_brackets = name_without_ext.split('(')[1].split(')')[0]

                doc_name = in_brackets.split('№')[0].strip()
                rest = in_brackets.split('№')[1]

                passport_number = rest.split(' от ')[0].strip()
                date_str = rest.split(' от ')[1].strip()

                # Пытаемся распарсить дату
                document_date = None
                for date_format in ['%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d']:
                    try:
                        document_date = datetime.strptime(date_str, date_format).date()
                        break
                    except:
                        continue

                return {
                    "material_name": mat_name,
                    "document_name": doc_name,
                    "passport_number": passport_number,
                    "document_date": document_date,
                    "filename": filename
                }
        except:
            pass

    # Для всех остальных файлов
    return {
        "material_name": None,
        "document_name": name_without_ext,
        "passport_number": None,
        "document_date": None,
        "filename": filename
    }


def get_all_files(base_path):
    """
    Получает все файлы в директории и поддиректориях
    Возвращает список кортежей (полный_путь, имя_файла)
    """
    all_files = []

    if not os.path.exists(base_path):
        return all_files

    for root, dirs, files in os.walk(base_path):
        for file in files:
            # Пропускаем временные файлы и скрытые файлы
            if not file.startswith('.'):
                file_path = os.path.join(root, file)
                all_files.append((file_path, file))

    return all_files


def get_category_from_path(file_path, base_path):
    """
    Извлекает категорию из пути файла
    """
    try:
        relative_path = os.path.relpath(file_path, base_path)
        dir_path = os.path.dirname(relative_path)

        if dir_path == '.' or not dir_path:
            return ("Корневая", None)

        parts = dir_path.split(os.sep)
        if parts:
            return (parts[0], parts[1] if len(parts) > 1 else None)
    except:
        pass

    return ("Корневая", None)
