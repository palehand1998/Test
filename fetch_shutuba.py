
#!/usr/bin/env python3
"""Netkeibaの出馬表だけを取得してJSONに保存する。"""

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup


from keiba_http import (
    DEFAULT_UA,
    Fetcher,
    FetchError,
    ParseError,
    SHUTUBA_URL,
    HORSE_URL,
)

from keiba_common import (
    nfkc,
    now_iso,
    text_of,
    to_float,
    to_int,
)

log = logging.getLogger(__name__)


def parse_shutuba(
    html: str,
    race_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """出馬表HTMLからレース情報と出走馬だけを解析する。"""

    soup = BeautifulSoup(html, "html.parser")

    d1 = text_of(soup.select_one("div.RaceData01"))
    d2 = text_of(soup.select_one("div.RaceData02"))
    title = text_of(soup.title)

    def rx(
        pattern: str,
        text: Optional[str],
        group: int = 1,
    ) -> Optional[str]:
        m = re.search(pattern, nfkc(text)) if text else None
        return m.group(group) if m else None

    m_date = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
        nfkc(title or ""),
    )

    race_date = (
        f"{int(m_date[1]):04d}-{int(m_date[2]):02d}-{int(m_date[3]):02d}"
        if m_date
        else None
    )

    race = {
        "race_id": race_id,
        "source_url": SHUTUBA_URL.format(race_id=race_id),
        "fetched_at": now_iso(),
        "date": race_date,
        "name": text_of(soup.select_one("div.RaceName")),
        "start_time": rx(r"(\d{1,2}:\d{2})発走", d1),
        "surface": rx(r"(芝|ダ|障)\s*\d{3,4}m", d1),
        "distance_m": to_int(
            rx(r"(?:芝|ダ|障)\s*(\d{3,4})m", d1)
        ),
        "direction": rx(r"\((右|左|直線)", d1),
        "weather": rx(r"天候\s*:\s*(\S+)", d1),
        "track_condition": rx(r"馬場\s*:\s*(\S+)", d1),
        "head_count": to_int(rx(r"(\d+)頭", d2)),
        "race_data01_raw": d1,
        "race_data02_raw": d2,
    }

    horses: list[dict[str, Any]] = []
    table = soup.select_one("table.Shutuba_Table")

    if table is None:
        raise ParseError("出馬表テーブル Shutuba_Table が見つかりません")

    for tr in table.select("tr.HorseList"):
        a_horse = tr.select_one('td.HorseInfo a[href*="/horse/"]')

        if a_horse is None:
            a_horse = tr.select_one('a[href*="/horse/"]')

        if a_horse is None:
            continue

        href = a_horse.get("href", "")
        hid = rx(r"/horse/(\d+)", href)

        if not hid:
            continue

        a_jk = tr.select_one("td.Jockey a")
        jk_href = a_jk.get("href", "") if a_jk else ""

        td_barei = tr.select_one("td.Barei")
        sexage = nfkc(text_of(td_barei))
        m_sa = re.search(r"([牡牝セ])\s*(\d+)", sexage)

        td_wc = (
            td_barei.find_next_sibling("td")
            if td_barei
            else None
        )
        wc = to_float(text_of(td_wc))

        m_bw = re.search(
            r"(\d{3})\s*\(([+-]?\d+)\)",
            nfkc(text_of(tr.select_one("td.Weight"))),
        )

        horses.append({
            "horse_id": hid,
            "horse_name": text_of(a_horse),
            "horse_url": HORSE_URL.format(horse_id=hid),
            "frame_no": to_int(
                text_of(tr.select_one("td[class*=Waku]"))
            ),
            "horse_no": to_int(
                text_of(tr.select_one("td[class*=Umaban]"))
            ),
            "sex": m_sa[1] if m_sa else None,
            "age": int(m_sa[2]) if m_sa else None,
            "weight_carried": (
                wc if wc is not None and 40 <= wc <= 70 else None
            ),
            "jockey": {
                "name": text_of(a_jk),
                "id": rx(r"/jockey/(?:[a-z]+/)*(\d{4,6})", jk_href),
            },
            "trainer": text_of(tr.select_one("td.Trainer a")),
            "body_weight": int(m_bw[1]) if m_bw else None,
            "body_weight_diff": int(m_bw[2]) if m_bw else None,
            "odds": to_float(text_of(tr.select_one("td.Popular"))),
            "popularity": to_int(
                text_of(tr.select_one("td.Popular_Ninki"))
            ),
            "scratched": "Cancel" in (tr.get("class") or []),
        })

    if not horses:
        raise ParseError("出馬表から出走馬を1頭も解析できませんでした")

    race["head_count"] = len(horses)
    return race, horses


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """JSONを一時ファイル経由で安全に保存する。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="出馬表だけを取得してJSONに保存"
    )
    parser.add_argument(
        "race_ids",
        nargs="+",
        help="12桁のレースID",
    )
    parser.add_argument(
        "--out",
        default="data/shutuba.json",
        help="保存先JSON（既定: data/shutuba.json）",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=2.5,
        help="リクエスト間隔の下限（秒）",
    )
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("KEIBA_UA", DEFAULT_UA),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.sleep < 1.0:
        parser.error("--sleep は1.0秒以上を指定してください")

    invalid = [
        rid for rid in args.race_ids
        if not re.fullmatch(r"\d{12}", rid)
    ]
    if invalid:
        parser.error(f"race_id は12桁の数字です: {invalid}")

    out = Path(args.out)

    # 既存ファイルがあれば読み込み、他のレース情報を保持する。
    if out.exists():
        try:
            with out.open("r", encoding="utf-8") as f:
                store = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.error("既存JSONを読み込めません。上書きしません: %s", e)
            return 1

        if not isinstance(store, dict) or not isinstance(
            store.get("races"), dict
        ):
            log.error("JSONの形式が想定外です。上書きしません")
            return 1
    else:
        store = {"schema_version": 1, "races": {}}

    fetcher = Fetcher(
        args.sleep,
        args.user_agent,
        respect_robots=True,
    )

    for rid in args.race_ids:
        log.info("出馬表を取得: race_id=%s", rid)

        try:
            html = fetcher.get(SHUTUBA_URL.format(race_id=rid))
            race, horses = parse_shutuba(html, rid)
        except (FetchError, ParseError) as e:
            log.error("race_id=%s をスキップ: %s", rid, e)
            continue

        race["horses"] = horses
        store["races"][rid] = race
        store["updated_at"] = now_iso()
        atomic_write_json(out, store)

        log.info(
            "保存しました: %s（出走馬 %d頭）",
            out,
            len(horses),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
