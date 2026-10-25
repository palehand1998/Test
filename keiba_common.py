
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def nfkc(s: Optional[str]) -> str:
    return unicodedata.normalize("NFKC", s or "")


def to_int(s: Optional[str]) -> Optional[int]:
    m = re.fullmatch(r"-?\d+", nfkc(s).strip())
    return int(m.group()) if m else None


def to_float(s: Optional[str]) -> Optional[float]:
    m = re.fullmatch(r"-?\d+(?:\.\d+)?", nfkc(s).strip())
    return float(m.group()) if m else None


def text_of(node) -> Optional[str]:
    return clean(node.get_text(" ")) if node is not None else None


def decode_html(content: bytes) -> str:
    """HTMLをデコードする。"""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("euc_jp", errors="replace")
