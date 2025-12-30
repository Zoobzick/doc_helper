# storage.py
from django.core.files.storage import FileSystemStorage
import os


class DirectivesStorage(FileSystemStorage):
    """Хранилище ТОЛЬКО для приказов"""

    def __init__(self):
        # Указываем конкретную папку для приказов
        location = r'\\Marina\ид участок №5 (липовая роща)\Приказы'
        super().__init__(location=location, base_url=None)
