from pathlib import Path

from .settings import *  # noqa: F401,F403


BASE_DIR = Path(__file__).resolve().parent.parent

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

ROOT_URLCONF = "settings.test_urls"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "authapp",
    "projects_app",
    "orgs_app",
]

MIGRATION_MODULES = {
    "authapp": None,
    "projects_app": None,
    "orgs_app": None,
}

BASE_ID_DIR = BASE_DIR / ".tmp_test_storage"
PASSPORTS_DIR = BASE_ID_DIR / "Паспорта"
DIRECTIVE_DIR = BASE_ID_DIR / "Приказы"
APPROVALS_DIR = BASE_ID_DIR / "Согласования"
PROJECTS_DIR = BASE_ID_DIR / "Проекты"
PROJECTS_JSON = PROJECTS_DIR / "projects.json"
ACTS_DIR = BASE_ID_DIR / "Акты"
DOCUMENTS_DIR = BASE_ID_DIR / "Документы"
MEDIA_ROOT = BASE_ID_DIR

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
