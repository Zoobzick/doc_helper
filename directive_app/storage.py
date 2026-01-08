# storage.py
from django.core.files.storage import FileSystemStorage
import os
from django.conf import settings


class DirectivesStorage(FileSystemStorage):
    """Хранилище ТОЛЬКО для приказов"""

    def __init__(self):
        # Указываем конкретную папку для приказов
        location = settings.DIRECTIVE_DIR
        super().__init__(location=location, base_url=None)
