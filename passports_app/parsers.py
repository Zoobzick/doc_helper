from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from django.utils.dateparse import parse_date


FILENAME_RE = re.compile(
    r"""
    ^\s*
    (?P<material>.+?)
    \s*\(\s*
    (?P<doc_name>.+?)
    \s*№\s*
    (?P<doc_number>.+?)
    \s+от\s+
    (?P<doc_date>\d{2}\.\d{2}\.\d{4})
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
    document_date: date


def _clean(s: str) -> str:
    return " ".join(s.strip().split())


def parse_passport_filename(filename: str) -> Optional[ParseResult]:
    base, _ext = os.path.splitext(os.path.basename(filename))
    m = FILENAME_RE.match(base)
    if not m:
        return None

    dd, mm, yyyy = m.group("doc_date").split(".")
    doc_date = parse_date(f"{yyyy}-{mm}-{dd}")
    if doc_date is None:
        return None

    return ParseResult(
        material=_clean(m.group("material")),
        document_name=_clean(m.group("doc_name")),
        document_number=_clean(m.group("doc_number")),
        document_date=doc_date,
    )
