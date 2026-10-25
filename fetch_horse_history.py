
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import tempfile
import shutil
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup

from keiba_http import (
    DEFAULT_UA,
    Fetcher,
    FetchError,
    BlockedError,
    ParseError,
    HORSE_URL,
)

from keiba_common import (
    nfkc,
    now_iso,
    text_of,
    to_float,
    to_int,
)

log = logging.getLogger("horse_history")

SCHEMA_VERSION = 1
CACHE_KEEP_ROWS = 100


def _hkey(s: str) -> str:
    return re.sub(r"\s+", "", nfkc(s))


def parse_history(
    html: str,
) -> Optional[list[dict[str, Any]]]:
    """戦績テーブルが無ければNone、あれば行のリストを返す。"""

    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.db_h_race_results")

    if table is None:
        return None

    ths = table.select("thead th") or table.select(
        "tr:first-child th"
    )

    cols = {
        _hkey(th.get_text()): i
        for i, th in enumerate(ths)
    }

    if "日付" not in cols or "着順" not in cols:
        raise ParseError(
            f"想定外の見出し: {list(cols)[:8]}"
        )

    rows: list[dict[str, Any]] = []

    for tr in table.select("tbody tr") or table.select("tr")[1:]:
        tds = tr.find_all("td", recursive=False)

        if len(tds) < len(cols):
            continue

        def cell(name: str):
            i = cols.get(name)
            return (
                tds[i]
                if i is not None and i < len(tds)
                else None
            )

        def val(name: str) -> Optional[str]:
            return text_of(cell(name))

        d = re.fullmatch(
            r"(\d{4})/(\d{1,2})/(\d{1,2})",
            nfkc(val("日付") or ""),
        )

        race_cell = cell("レース名")
        a_race = race_cell.find("a") if race_cell else None

        m_rid = (
            re.search(r"/race/(\d{12})", a_race.get("href", ""))
            if a_race
            else None
        )

        dist = re.fullmatch(
            r"(芝|ダ|障)(\d{3,4})",
            nfkc(val("距離") or ""),
        )

        t = re.fullmatch(
            r"(\d+):(\d{2})\.(\d)",
            nfkc(val("タイム") or ""),
        )

        bw = re.search(
            r"(\d{3})\s*\(([+-]?\d+)\)",
            nfkc(val("馬体重") or ""),
        )

        finish_raw = val("着順")

        rows.append({
            "race_id": m_rid[1] if m_rid else None,
            "date": (
                f"{int(d[1]):04d}-{int(d[2]):02d}-{int(d[3]):02d}"
                if d
                else None
            ),
            "venue": val("開催"),
            "race_name": (
                text_of(a_race) if a_race else val("レース名")
            ),
            "head_count": to_int(val("頭数")),
            "frame_no": to_int(val("枠番")),
            "horse_no": to_int(val("馬番")),
            "odds": to_float(val("オッズ")),
            "popularity": to_int(val("人気")),
            "finish_raw": finish_raw,
            "finish_pos": to_int(finish_raw),
            "jockey": val("騎手"),
            "weight_carried": to_float(val("斤量")),
            "surface": dist[1] if dist else None,
            "distance_m": int(dist[2]) if dist else None,
            "track_condition": val("馬場"),
            "time": val("タイム"),
            "time_sec": (
                round(
                    int(t[1]) * 60
                    + int(t[2])
                    + int(t[3]) / 10,
                    1,
                )
                if t
                else None
            ),
            "margin": val("着差"),
            "passing": val("通過"),
            "last_3f": to_float(val("上り")),
            "body_weight": int(bw[1]) if bw else None,
            "body_weight_diff": int(bw[2]) if bw else None,
            "weather": val("天気"),
        })

    return rows
