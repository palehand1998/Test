#!/usr/bin/env python3
"""出馬表 + 各馬の過去N走を取得してJSONに保存する。

使い方:
    pip install requests beautifulsoup4
    python fetch_shutuba_with_history.py 202605021211 --history 3 --sleep 2.5

注意:
    netkeibaのHTML構造やアクセス条件が変わると取得できない場合があります。
    取得できなかった情報は推測せず、ステータスで区別します。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


SCHEMA_VERSION = 1

SHUTUBA_URL = (
    "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
)

# 競走馬の競走成績ページを使用
HORSE_URL = "https://db.netkeiba.com/horse/result/{horse_id}/"

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; keiba-pwa-personal/0.1)"
)

CACHE_KEEP_ROWS = 20

log = logging.getLogger("shutuba")


class FetchError(RuntimeError):
    """通常の取得失敗。"""


class BlockedError(RuntimeError):
    """アクセス制限の可能性がある場合。"""


class ParseError(RuntimeError):
    """HTML解析に失敗した場合。"""


# ----------------------------------------------------------------------
# 共通関数
# ----------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def nfkc(value: Optional[str]) -> str:
    return unicodedata.normalize("NFKC", value or "")


def normalize_header(value: str) -> str:
    value = nfkc(value)
    value = re.sub(r"\s+", "", value)
    return value.replace(" ", "")


def to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    m = re.search(r"-?\d+", nfkc(value).replace(",", ""))
    return int(m.group()) if m else None


def to_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", nfkc(value).replace(",", ""))
    return float(m.group()) if m else None


def text_of(node) -> Optional[str]:
    return clean(node.get_text(" ", strip=True)) if node else None


def decode_html(content: bytes) -> str:
    """HTMLの文字コードを推定してデコードする。"""
    for encoding in ("utf-8", "euc_jp", "cp932"):
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def first_text(soup: BeautifulSoup, selectors: list[str]) -> Optional[str]:
    for selector in selectors:
        node = soup.select_one(selector)
        value = text_of(node)
        if value:
            return value
    return None


def parse_date(value: Optional[str]) -> Optional[str]:
    """YYYY-MM-DD形式に変換する。"""
    if not value:
        return None

    value = nfkc(value)

    m = re.search(
        r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})",
        value,
    )

    if not m:
        return None

    year, month, day = map(int, m.groups())

    try:
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return None


# ----------------------------------------------------------------------
# HTTP取得
# ----------------------------------------------------------------------

class Fetcher:
    def __init__(
        self,
        sleep: float,
        user_agent: str,
        timeout: float = 20.0,
        retries: int = 3,
        respect_robots: bool = True,
    ):
        self.sleep = sleep
        self.timeout = timeout
        self.retries = retries
        self.ua = user_agent
        self.respect_robots = respect_robots

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.7",
        })

        self.last_request = 0.0
        self.robots: dict[str, robotparser.RobotFileParser] = {}
        self.requests_made = 0

    def _wait(self) -> None:
        gap = self.sleep + random.uniform(0, self.sleep * 0.3)
        elapsed = time.monotonic() - self.last_request

        if elapsed < gap:
            time.sleep(gap - elapsed)

    def _raw_get(self, url: str) -> requests.Response:
        self._wait()

        try:
            return self.session.get(url, timeout=self.timeout)
        finally:
            self.last_request = time.monotonic()
            self.requests_made += 1

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True

        host = urlparse(url).netloc
        parser = self.robots.get(host)

        if parser is None:
            parser = robotparser.RobotFileParser()
            robots_url = f"https://{host}/robots.txt"
            parser.set_url(robots_url)

            try:
                response = self._raw_get(robots_url)

                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    # 取得できない場合は安全側に倒して取得しない
                    parser.parse(["User-agent: *", "Disallow: /"])

            except requests.RequestException as exc:
                log.warning("robots.txtの取得失敗: %s", exc)
                parser.parse(["User-agent: *", "Disallow: /"])

            self.robots[host] = parser

        return parser.can_fetch(self.ua, url)

    def get(self, url: str) -> str:
        if not self._allowed(url):
            raise FetchError(f"robots.txtにより取得不可: {url}")

        for attempt in range(1, self.retries + 1):
            try:
                response = self._raw_get(url)
            except requests.RequestException as exc:
                log.warning(
                    "通信エラー(%d/%d): %s",
                    attempt,
                    self.retries,
                    exc,
                )
                if attempt < self.retries:
                    time.sleep(3 * attempt)
                continue

            if response.status_code == 200:
                return decode_html(response.content)

            if response.status_code in (403, 429):
                raise BlockedError(
                    f"HTTP {response.status_code}: {url}"
                )

            if response.status_code == 404:
                raise FetchError(f"404 Not Found: {url}")

            log.warning(
                "HTTP %d (%d/%d): %s",
                response.status_code,
                attempt,
                self.retries,
                url,
            )

            if attempt < self.retries:
                time.sleep(3 * attempt)

        raise FetchError(f"取得に失敗しました: {url}")


# ----------------------------------------------------------------------
# 出馬表パーサ
# ----------------------------------------------------------------------

def parse_shutuba(
    html: str,
    race_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:

    soup = BeautifulSoup(html, "html.parser")

    d1 = first_text(soup, [
        "div.RaceData01",
        ".RaceData01",
        ".RaceData01 span",
    ])

    d2 = first_text(soup, [
        "div.RaceData02",
        ".RaceData02",
    ])

    title = text_of(soup.title)

    race_name = first_text(soup, [
        "div.RaceName",
        ".RaceName",
        ".RaceName span",
        "h1.RaceName",
    ])

    # RaceNameが見つからない場合、タイトルから候補を抽出
    if not race_name and title:
        cleaned_title = re.sub(
            r"\s*｜\s*競馬データベース.*$",
            "",
            title,
        )
        cleaned_title = re.sub(
            r"\s*-\s*netkeiba.*$",
            "",
            cleaned_title,
            flags=re.IGNORECASE,
        )
        if cleaned_title.strip():
            race_name = clean(cleaned_title)

    def rx(pattern: str, value: Optional[str], group: int = 1):
        m = re.search(pattern, nfkc(value)) if value else None
        return m.group(group) if m else None

    race_date = parse_date(title)

    if not race_date:
        race_date = parse_date(d2)

    if not race_date:
        race_date = parse_date(
            first_text(soup, [
                ".RaceData02",
                ".RaceData01",
                ".RaceData",
            ])
        )

    race = {
        "race_id": race_id,
        "source_url": SHUTUBA_URL.format(race_id=race_id),
        "fetched_at": now_iso(),
        "date": race_date,
        "name": race_name,
        "start_time": rx(r"(\d{1,2}:\d{2})発走", d1),
        "surface": rx(r"(芝|ダ|障)\s*\d{3,4}\s*m?", d1),
        "distance_m": to_int(
            rx(r"(?:芝|ダ|障)\s*(\d{3,4})\s*m?", d1)
        ),
        "direction": rx(r"[（(](右|左|直線)", d1),
        "weather": rx(r"天候\s*[:：]\s*(\S+)", d1),
        "track_condition": rx(r"馬場\s*[:：]\s*(\S+)", d1),
        "head_count": to_int(rx(r"(\d+)頭", d2)),
        "race_data01_raw": d1,
        "race_data02_raw": d2,
    }

    horses: list[dict[str, Any]] = []

    table = soup.select_one("table.Shutuba_Table")

    if table is None:
        table = soup.select_one("table.RaceTable01")

    if table is None:
        raise ParseError("出馬表テーブルが見つかりません")

    rows = table.select("tr.HorseList")

    if not rows:
        rows = [
            tr for tr in table.select("tr")
            if tr.select_one('a[href*="/horse/"]')
        ]

    for tr in rows:
        a_horse = tr.select_one('a[href*="/horse/"]')

        if a_horse is None:
            continue

        href = a_horse.get("href", "")
        horse_match = re.search(r"/horse/(\d+)", href)

        if not horse_match:
            continue

        horse_id = horse_match.group(1)

        # 馬名リンク以外のリンクを拾わないよう、馬情報セルを優先
        horse_name = text_of(a_horse)

        td_barei = tr.select_one("td.Barei")
        sexage = nfkc(text_of(td_barei))

        sexage_match = re.search(r"([牡牝セ])\s*(\d+)", sexage)

        # 馬体重
        weight_text = nfkc(text_of(tr.select_one("td.Weight")))
        body_weight_match = re.search(
            r"(\d{3})\s*[（(]\s*([+-]?\d+)\s*[）)]",
            weight_text,
        )

        # 騎手
        jockey_link = tr.select_one('a[href*="/jockey/"]')
        jockey_href = jockey_link.get("href", "") if jockey_link else ""
        jockey_match = re.search(r"/jockey/(?:[a-z]+/)*(\d{4,6})", jockey_href)

        # 斤量は斤量らしい数値のセルから取得
        weight_carried = None
        for td in tr.select("td"):
            candidate = to_float(text_of(td))
            if candidate is not None and 40 <= candidate <= 70:
                # 馬齢やオッズなどの誤検出を避けるため、
                # 斤量クラスのセルを優先
                classes = " ".join(td.get("class", []))
                if "Barei" not in classes:
                    if any(k in classes.lower() for k in ("weight", "carry", "kinryo")):
                        weight_carried = candidate
                        break

        if weight_carried is None:
            # 一般的な出馬表では馬齢セルの直後が斤量セル
            if td_barei:
                next_td = td_barei.find_next_sibling("td")
                candidate = to_float(text_of(next_td))
                if candidate is not None and 40 <= candidate <= 70:
                    weight_carried = candidate

        def cell_int(selector: str) -> Optional[int]:
            return to_int(text_of(tr.select_one(selector)))

        odds = to_float(text_of(
            tr.select_one("td.Popular, td.Odds, td[class*=Odds]")
        ))

        popularity = to_int(text_of(
            tr.select_one("td.Popular_Ninki, td.Ninki, td[class*=Ninki]")
        ))

        horses.append({
            "horse_id": horse_id,
            "horse_name": horse_name,
            "horse_url": HORSE_URL.format(horse_id=horse_id),
            "frame_no": cell_int("td[class*=Waku]"),
            "horse_no": cell_int("td[class*=Umaban]"),
            "sex": sexage_match.group(1) if sexage_match else None,
            "age": int(sexage_match.group(2)) if sexage_match else None,
            "weight_carried": weight_carried,
            "jockey": {
                "name": text_of(jockey_link),
                "id": jockey_match.group(1) if jockey_match else None,
            },
            "trainer": text_of(tr.select_one('a[href*="/trainer/"]')),
            "body_weight": (
                int(body_weight_match.group(1))
                if body_weight_match else None
            ),
            "body_weight_diff": (
                int(body_weight_match.group(2))
                if body_weight_match else None
            ),
            "odds": odds,
            "popularity": popularity,
            "scratched": "Cancel" in (tr.get("class") or []),
            "history_status": "not_fetched",
            "history_fetched_at": None,
            "past_results": [],
        })

    if not horses:
        raise ParseError("出馬表から馬を1頭も解析できませんでした")

    return race, horses


# ----------------------------------------------------------------------
# 過去成績パーサ
# ----------------------------------------------------------------------

def _header_map(table) -> dict[str, int]:
    """テーブル見出しを列番号に変換する。"""
    header_row = table.select_one("thead tr")

    if header_row is None:
        for tr in table.select("tr"):
            if tr.find("th"):
                header_row = tr
                break

    if header_row is None:
        return {}

    headers = []
    for cell in header_row.find_all(["th", "td"], recursive=False):
        headers.append(normalize_header(cell.get_text(" ", strip=True)))

    return {name: index for index, name in enumerate(headers) if name}


def _find_column(columns: dict[str, int], *names: str) -> Optional[int]:
    for name in names:
        target = normalize_header(name)
        if target in columns:
            return columns[target]

    # 見出しの末尾に注釈などがついている場合も許容
    for existing, index in columns.items():
        for name in names:
            target = normalize_header(name)
            if target and target in existing:
                return index

    return None


def parse_history(html: str) -> Optional[list[dict[str, Any]]]:
    """競走成績テーブルを解析する。検出不能ならNoneを返す。"""

    soup = BeautifulSoup(html, "html.parser")

    tables = soup.select(
        "table.db_h_race_results, "
        "table.race_table_01, "
        "table"
    )

    selected_table = None
    selected_columns = {}

    for table in tables:
        columns = _header_map(table)

        date_col = _find_column(columns, "日付")
        finish_col = _find_column(columns, "着順")

        if date_col is not None and finish_col is not None:
            selected_table = table
            selected_columns = columns
            break

    if selected_table is None:
        log.warning(
            "競走成績テーブルが見つかりません。HTML内のテーブル構造を確認してください"
        )
        return None

    col = {
        "date": _find_column(selected_columns, "日付"),
        "venue": _find_column(selected_columns, "開催"),
        "race_name": _find_column(selected_columns, "レース名"),
        "head_count": _find_column(selected_columns, "頭数"),
        "frame_no": _find_column(selected_columns, "枠番"),
        "horse_no": _find_column(selected_columns, "馬番"),
        "odds": _find_column(selected_columns, "オッズ"),
        "popularity": _find_column(selected_columns, "人気"),
        "finish": _find_column(selected_columns, "着順"),
        "jockey": _find_column(selected_columns, "騎手"),
        "weight_carried": _find_column(selected_columns, "斤量"),
        "distance": _find_column(selected_columns, "距離"),
        "track_condition": _find_column(selected_columns, "馬場"),
        "time": _find_column(selected_columns, "タイム"),
        "margin": _find_column(selected_columns, "着差"),
        "passing": _find_column(selected_columns, "通過"),
        "last_3f": _find_column(selected_columns, "上り"),
        "body_weight": _find_column(selected_columns, "馬体重"),
        "weather": _find_column(selected_columns, "天気", "天候"),
    }

    def get_cell(cells, key: str):
        index = col.get(key)
        return cells[index] if index is not None and index < len(cells) else None

    def get_value(cells, key: str) -> Optional[str]:
        return text_of(get_cell(cells, key))

    rows: list[dict[str, Any]] = []

    body_rows = selected_table.select("tbody tr")
    if not body_rows:
        body_rows = [
            tr for tr in selected_table.select("tr")
            if tr.find("td")
        ]

    for tr in body_rows:
        cells = tr.find_all("td", recursive=False)

        if not cells:
            continue

        date_raw = get_value(cells, "date")
        race_date = parse_date(date_raw)

        if not race_date:
            continue

        race_cell = get_cell(cells, "race_name")
        race_link = race_cell.find("a") if race_cell else None

        race_id = None
        if race_link:
            match = re.search(
                r"/race/(\d{12})",
                race_link.get("href", ""),
            )
            if match:
                race_id = match.group(1)

        distance_raw = nfkc(get_value(cells, "distance") or "")
        distance_match = re.search(
            r"(芝|ダ|障)\s*(\d{3,4})",
            distance_raw,
        )

        time_raw = nfkc(get_value(cells, "time") or "")
        time_match = re.fullmatch(
            r"(\d+):(\d{2})[.．](\d)",
            time_raw,
        )

        body_weight_raw = nfkc(get_value(cells, "body_weight") or "")
        body_weight_match = re.search(
            r"(\d{3})\s*[（(]\s*([+-]?\d+)\s*[）)]",
            body_weight_raw,
        )

        finish_raw = get_value(cells, "finish")

        rows.append({
            "race_id": race_id,
            "date": race_date,
            "venue": get_value(cells, "venue"),
            "race_name": (
                text_of(race_link)
                if race_link else get_value(cells, "race_name")
            ),
            "head_count": to_int(get_value(cells, "head_count")),
            "frame_no": to_int(get_value(cells, "frame_no")),
            "horse_no": to_int(get_value(cells, "horse_no")),
            "odds": to_float(get_value(cells, "odds")),
            "popularity": to_int(get_value(cells, "popularity")),
            "finish_raw": finish_raw,
            "finish_pos": to_int(finish_raw),
            "jockey": get_value(cells, "jockey"),
            "weight_carried": to_float(get_value(cells, "weight_carried")),
            "surface": distance_match.group(1) if distance_match else None,
            "distance_m": (
                int(distance_match.group(2))
                if distance_match else None
            ),
            "track_condition": get_value(cells, "track_condition"),
            "time": get_value(cells, "time"),
            "time_sec": (
                round(
                    int(time_match.group(1)) * 60
                    + int(time_match.group(2))
                    + int(time_match.group(3)) / 10,
                    1,
                )
                if time_match else None
            ),
            "margin": get_value(cells, "margin"),
            "passing": get_value(cells, "passing"),
            "last_3f": to_float(get_value(cells, "last_3f")),
            "body_weight": (
                int(body_weight_match.group(1))
                if body_weight_match else None
            ),
            "body_weight_diff": (
                int(body_weight_match.group(2))
                if body_weight_match else None
            ),
            "weather": get_value(cells, "weather"),
        })

    return rows


# ----------------------------------------------------------------------
# JSON保存・キャッシュ
# ----------------------------------------------------------------------

def atomic_write_json(
    path: Path,
    obj: Any,
    backup: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(obj, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        if backup and path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))

        os.replace(temp_path, path)

    except BaseException:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def load_store(path: Path) -> dict[str, Any]:
    empty = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "races": {},
    }

    if not path.exists():
        return empty

    try:
        data = json.loads(path.read_text(encoding="utf-8"))

        if not isinstance(data.get("races"), dict):
            raise ValueError("racesがオブジェクトではありません")

        return data

    except (json.JSONDecodeError, ValueError, AttributeError) as exc:
        backup_path = path.with_name(
            f"{path.name}.corrupt.{int(time.time())}"
        )
        shutil.move(path, backup_path)
        log.error("JSONを退避しました: %s (%s)", backup_path, exc)
        return empty


class HorseHistoryStore:
    def __init__(self, cache_dir: Path, ttl_hours: float):
        self.directory = cache_dir
        self.ttl_seconds = ttl_hours * 3600

    def path_for(self, horse_id: str) -> Path:
        return self.directory / f"{horse_id}.json"

    def read(self, horse_id: str) -> Optional[dict[str, Any]]:
        try:
            return json.loads(
                self.path_for(horse_id).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None

    def is_fresh(self, entry: Optional[dict[str, Any]]) -> bool:
        if not entry:
            return False

        fetched_epoch = entry.get("fetched_epoch", 0)

        if not isinstance(fetched_epoch, (int, float)):
            return False

        return time.time() - fetched_epoch < self.ttl_seconds

    def write(
        self,
        horse_id: str,
        rows: Optional[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        entry = {
            "fetched_at": now_iso(),
            "fetched_epoch": time.time(),
            "has_table": rows is not None,
            "rows": (rows or [])[:CACHE_KEEP_ROWS],
        }

        atomic_write_json(
            self.path_for(horse_id),
            entry,
            backup=False,
        )

        return entry


# ----------------------------------------------------------------------
# 過去成績の選択・統合
# ----------------------------------------------------------------------

def select_history(
    rows: list[dict[str, Any]],
    race_id: str,
    race_date: Optional[str],
    count: int,
) -> list[dict[str, Any]]:
    """対象レース自身・対象日以降を除外し、最大count走を返す。"""

    selected = []

    # 日付の新しい順に処理
    sorted_rows = sorted(
        rows,
        key=lambda row: row.get("date") or "",
        reverse=True,
    )

    for row in sorted_rows:
        if row.get("race_id") == race_id:
            continue

        row_date = row.get("date")

        if race_date and row_date and row_date >= race_date:
            continue

        selected.append(row)

        if len(selected) >= count:
            break

    return selected


def fill_history(
    fetcher: Fetcher,
    cache: HorseHistoryStore,
    horse: dict[str, Any],
    race_id: str,
    race_date: Optional[str],
    count: int,
) -> None:
    horse_id = horse["horse_id"]

    entry = cache.read(horse_id)
    status = "cache"

    # 古い形式や、過去にテーブルを発見できなかったキャッシュは再取得
    if entry and not entry.get("has_table", False):
        entry = None

    if not cache.is_fresh(entry):
        history_url = HORSE_URL.format(horse_id=horse_id)

        html = fetcher.get(history_url)
        rows = parse_history(html)

        entry = cache.write(horse_id, rows)
        status = "ok"

    if not entry or not entry.get("has_table", False):
        horse.update({
            "history_status": "no_table",
            "history_fetched_at": (
                entry.get("fetched_at") if entry else None
            ),
            "past_results": [],
        })
        return

    rows = entry.get("rows", [])

    if not isinstance(rows, list):
        rows = []

    selected = select_history(
        rows,
        race_id,
        race_date,
        count,
    )

    if selected:
        status = "ok" if status == "ok" else "cache"
    else:
        status = "no_data"

    horse.update({
        "history_status": status,
        "history_fetched_at": entry.get("fetched_at"),
        "past_results": selected,
    })


def build_race(
    fetcher: Fetcher,
    cache: HorseHistoryStore,
    race_id: str,
    count: int,
    race_date_override: Optional[str],
    previous_race: Optional[dict[str, Any]],
    limit_horses: Optional[int],
) -> tuple[dict[str, Any], bool]:

    html = fetcher.get(SHUTUBA_URL.format(race_id=race_id))
    race, horses = parse_shutuba(html, race_id)

    race_date = race_date_override or race.get("date")

    if race_date_override:
        race["date"] = race_date_override

    if not race_date:
        log.warning(
            "開催日をHTMLから取得できませんでした。"
            "--race-date YYYY-MM-DD の指定を検討してください"
        )

    previous_horses = {
        horse.get("horse_id"): horse
        for horse in (previous_race or {}).get("horses", [])
        if horse.get("horse_id")
    }

    targets = [
        horse for horse in horses
        if horse.get("horse_id") and not horse.get("scratched")
    ]

    if limit_horses is not None:
        targets = targets[:limit_horses]

    target_ids = {horse["horse_id"] for horse in targets}
    blocked = False

    for index, horse in enumerate(horses, start=1):
        horse_id = horse.get("horse_id")

        if not horse_id:
            horse["history_status"] = "no_horse_id"
            continue

        if horse.get("scratched"):
            horse["history_status"] = "scratched"
            continue

        if horse_id not in target_ids:
            continue

        log.info(
            "[%d/%d] %s (%s)",
            index,
            len(horses),
            horse.get("horse_name"),
            horse_id,
        )

        try:
            if blocked:
                raise FetchError("アクセス制限により取得を中断しました")

            fill_history(
                fetcher,
                cache,
                horse,
                race_id,
                race_date,
                count,
            )

        except BlockedError as exc:
            log.error("アクセス制限を検知: %s", exc)
            blocked = True

        except (FetchError, ParseError) as exc:
            log.warning(
                "%sの履歴取得失敗: %s",
                horse.get("horse_name"),
                exc,
            )

        if horse.get("history_status") == "not_fetched":
            old = previous_horses.get(horse_id)

            if old and old.get("past_results"):
                horse.update({
                    "history_status": "stale",
                    "history_fetched_at": old.get("history_fetched_at"),
                    "past_results": old.get("past_results", []),
                })
            else:
                horse["history_status"] = "error"

        if blocked:
            break

    race["history_depth"] = count
    race["horses"] = horses

    return race, blocked


# ----------------------------------------------------------------------
# メイン処理
# ----------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="出馬表と各馬の過去成績をJSONに保存"
    )

    parser.add_argument(
        "race_ids",
        nargs="+",
        help="12桁のrace_id（例: 202605021211）",
    )
    parser.add_argument(
        "--out",
        default="data/raw_shutuba_with_history.json",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/cache/horse_history",
    )
    parser.add_argument(
        "--cache-ttl-hours",
        type=float,
        default=168.0,
    )
    parser.add_argument(
        "--history",
        type=int,
        default=3,
        help="取得する過去走数",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=2.5,
        help="リクエスト間隔の下限（秒）",
    )
    parser.add_argument(
        "--race-date",
        help="対象レース日 YYYY-MM-DD",
    )
    parser.add_argument(
        "--limit-horses",
        type=int,
        help="テスト用に履歴取得頭数を制限",
    )
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("KEIBA_UA", DEFAULT_UA),
    )
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="robots.txtを無視する（非推奨）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.sleep < 1.0:
        log.warning("--sleepが短すぎるため1秒に引き上げます")
        args.sleep = 1.0

    if args.history < 1:
        parser.error("--historyは1以上を指定してください")

    if args.limit_horses is not None and args.limit_horses < 1:
        parser.error("--limit-horsesは1以上を指定してください")

    if args.cache_ttl_hours < 0:
        parser.error("--cache-ttl-hoursは0以上を指定してください")

    invalid_ids = [
        race_id for race_id in args.race_ids
        if not re.fullmatch(r"\d{12}", race_id)
    ]

    if invalid_ids:
        parser.error(f"race_idは12桁の数字です: {invalid_ids}")

    if args.race_date and not parse_date(args.race_date):
        parser.error("--race-dateはYYYY-MM-DD形式で指定してください")

    output_path = Path(args.out)
    store = load_store(output_path)

    fetcher = Fetcher(
        sleep=args.sleep,
        user_agent=args.user_agent,
        respect_robots=not args.ignore_robots,
    )

    cache = HorseHistoryStore(
        Path(args.cache_dir),
        args.cache_ttl_hours,
    )

    exit_code = 0

    for race_id in args.race_ids:
        log.info("=== race_id=%s ===", race_id)

        try:
            race, blocked = build_race(
                fetcher=fetcher,
                cache=cache,
                race_id=race_id,
                count=args.history,
                race_date_override=args.race_date,
                previous_race=store["races"].get(race_id),
                limit_horses=args.limit_horses,
            )

        except BlockedError as exc:
            log.error("出馬表の取得でアクセス制限: %s", exc)
            exit_code = 2
            break

        except (FetchError, ParseError) as exc:
            log.error(
                "race_id=%sをスキップ（既存データは維持）: %s",
                race_id,
                exc,
            )
            exit_code = max(exit_code, 1)
            continue

        store["races"][race_id] = race
        store["updated_at"] = now_iso()

        atomic_write_json(output_path, store)

        horses = race["horses"]
        with_history = sum(
            bool(horse.get("past_results"))
            for horse in horses
        )
        no_table = sum(
            horse.get("history_status") == "no_table"
            for horse in horses
        )
        errors = sum(
            horse.get("history_status") == "error"
            for horse in horses
        )

        log.info(
            "保存: %s / 出走馬%d頭 / 過去成績あり%d頭 "
            "/ no_table=%d / error=%d / リクエスト累計%d回",
            output_path,
            len(horses),
            with_history,
            no_table,
            errors,
            fetcher.requests_made,
        )

        # レース名や日付が取れない場合も警告
        if not race.get("name"):
            log.warning("レース名が取得できていません")

        if not race.get("date"):
            log.warning("レース開催日が取得できていません")

        # 履歴取得が全頭失敗なら、Actions側で成功扱いにしない
        active_horses = [
            horse for horse in horses
            if horse.get("horse_id") and not horse.get("scratched")
        ]

        if active_horses and with_history == 0:
            log.error("過去成績が1頭も取得できませんでした")
            exit_code = max(exit_code, 1)

        if blocked:
            exit_code = 2
            break

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
