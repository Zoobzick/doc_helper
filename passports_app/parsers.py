from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

FILENAME_RE = re.compile(
    r"""
    ^\s*
    (?P<material>.+?)
    \s*\(\s*
    (?P<doc_name>.+?)
    \s*№\s*
    (?P<doc_number>.+?)
    \s+от\s+
    (?P<doc_date>.+?)
    \s*\)\s*
    $
    """,
    re.VERBOSE | re.IGNORECASE,
)


@dataclass(frozen=True)
class ParseResult:
    material: str
    document_name: str
    document_number: str
    document_date: Optional[date]
    document_date_text: str = ""
    needs_review: bool = False


def parse_document_date(value: str) -> tuple[Optional[date], str, bool]:
    """Точная дата или исходный текст; неверная календарная дата требует проверки."""
    value = value.strip()
    if re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", value):
        day, month, year = map(int, value.split("."))
        try:
            return date(year, month, day), "", False
        except ValueError:
            return None, value, True
    return None, value, not bool(value)


def _clean(s: str) -> str:
    return " ".join(s.strip().split())


def parse_passport_filename(filename: str) -> Optional[ParseResult]:
    base, _ext = os.path.splitext(os.path.basename(filename))
    m = FILENAME_RE.match(base)
    if not m:
        return None

    doc_date, date_text, needs_review = parse_document_date(m.group("doc_date"))

    return ParseResult(
        material=_clean(m.group("material")),
        document_name=_clean(m.group("doc_name")),
        document_number=_clean(m.group("doc_number")).replace("%", "/"),
        document_date=doc_date,
        document_date_text=date_text,
        needs_review=needs_review,
    )
