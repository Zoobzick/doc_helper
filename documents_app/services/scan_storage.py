"""Direct browser-to-S3 multipart uploads, separate from backup objects."""
from pathlib import PurePosixPath
import re

import boto3
from botocore.config import Config
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PART_SIZE = 16 * 1024 * 1024
MAX_SIZE = 5 * 1024**3
EXTENSIONS = {".zip", ".7z", ".rar", ".pdf", ".tar", ".gz"}


def download_name(scan, *, title=None):
    title = scan.batch.title if title is None else title
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", title or "")
    title = " ".join(title.split()).strip(" .") or f"Комплект {scan.batch_id}"
    # Leave room for the prefix and extension within common filesystem limits.
    title = title.encode("utf-8")[:210].decode("utf-8", errors="ignore").rstrip(" .")
    suffix = ".tar.gz" if scan.name.lower().endswith(".tar.gz") else PurePosixPath(scan.name).suffix
    return f"Скан ИД {title}{suffix}"


def option(name):
    return getattr(settings, "S3_SCAN_" + name, "") or getattr(settings, "S3_BACKUP_" + name, "")


def client():
    if not all(option(name) for name in ("BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY")):
        raise ImproperlyConfigured("Хранилище сканов S3 не настроено.")
    return boto3.client(
        "s3", endpoint_url=option("ENDPOINT_URL") or None,
        region_name=option("REGION") or None,
        aws_access_key_id=option("ACCESS_KEY_ID"),
        aws_secret_access_key=option("SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=60,
                      retries={"max_attempts": 2}),
    )


def validate_file(name, size):
    if not isinstance(name, str) or not name or len(name) > 255:
        raise ValueError("Некорректное имя файла.")
    if any(ord(c) < 32 for c in name) or "/" in name or "\\" in name:
        raise ValueError("Некорректное имя файла.")
    if PurePosixPath(name).suffix.lower() not in EXTENSIONS:
        raise ValueError("Выберите PDF или архив ZIP, 7Z, RAR, TAR, GZ.")
    if type(size) is not int or not 0 < size <= MAX_SIZE:
        raise ValueError("Размер файла должен быть от 1 байта до 5 ГБ.")


def params(scan):
    return {"Bucket": scan.bucket, "Key": scan.key, "UploadId": scan.upload_id}


def verified_parts(s3, scan):
    parts = []
    for page in s3.get_paginator("list_parts").paginate(**params(scan)):
        parts.extend(page.get("Parts", []))
    count = (scan.size + PART_SIZE - 1) // PART_SIZE
    if len(parts) != count:
        raise ValueError("Загружены не все части файла. Повторите загрузку.")
    for number, part in enumerate(parts, 1):
        expected = min(PART_SIZE, scan.size - (number - 1) * PART_SIZE)
        if part["PartNumber"] != number or part["Size"] != expected:
            raise ValueError("Размер загруженного файла не совпадает с заявленным.")
    return [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]
