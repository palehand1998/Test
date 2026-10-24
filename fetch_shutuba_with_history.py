#!/usr/bin/env python3
"""出馬表(shutuba) + 各馬の過去N走 を統合して JSON に保存する。

使い方:
    pip install requests beautifulsoup4
    python fetch_shutuba_with_history.py 202505021211 \
        --history 3 --sleep 2.5 --out data/raw_shutuba_with_history.json

設計方針:
  * 過去成績は horses[].past_results に格納する。
  * 対象レース自身・対象日以降の結果は過去成績から除外する。
  * 取得できない項目は推測せず null / [] にする。
  * JSON は一時ファイル経由で安全に保存し、上書き前にバックアップを残す。
  * 馬ごとの履歴はキャッシュし、同じ馬への重複アクセスを減らす。
  * 403 / 429 のアクセス制限を検知した場合は取得を中断する。
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
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


SCHEMA_VERSION = 1
SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
HORSE_URL = "https://db.netkeiba.com/horse/{horse_id}/"
DEFAULT_UA = (
    "Mozilla/5.0 (compatible; keiba-pwa-personal/0.1; "
    "+contact:your-email@example.com)"
)
CACHE_KEEP_ROWS = 10

log = logging.getLogger("shutuba")


class FetchError(RuntimeError):
    """通常の取得失敗。"""


class BlockedError(RuntimeError):
    """アクセス制限の可能性がある場合の例外。"""


class ParseError(RuntimeError):
    """HTML構造が想定と異なる場合の例外。"""


# --------------------------------------------------------------------------
# 小物関数
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# HTTP取得
# --------------------------------------------------------------------------

class Fetcher:
    def __init__(
        self,
        sleep: float,
        user_agent: str,
        timeout: float = 15.0,
        retries: int = 3,
        respect_robots: bool = True,
    ):
        self.sleep = sleep
        self.timeout = timeout
        self.retries = retries
        self.ua = user_agent
        self.respect_robots = respect_robots

        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "ja,en;q=0.7",
        })

        self._last = 0.0
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self.requests_made = 0

    def _wait(self) -> None:
        gap = self.sleep + random.uniform(0, self.sleep * 0.3)
        delta = time.monotonic() - self._last

        if delta < gap:
            time.sleep(gap - delta)

    def _raw_get(self, url: str) -> requests.Response:
        self._wait()

        try:
            return self.s.get(url, timeout=self.timeout)
        finally:
            self._last = time.monotonic()
            self.requests_made += 1

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True

        host = urlparse(url).netloc
        rp = self._robots.get(host)

        if rp is None:
            rp = robotparser.RobotFileParser()

            try:
                resp = self._raw_get(f"https://{host}/robots.txt")

                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp.allow_all = True

            except requests.RequestException as e:
                log.warning(
                    "robots.txt を取得できませんでした(%s): %s",
                    host,
                    e,
                )
                rp.allow_all = True

            self._robots[host] = rp

        return rp.can_fetch(self.ua, url)

    def get(self, url: str) -> str:
        if not self._allowed(url):
            raise FetchError(f"robots.txt により取得不可: {url}")

        rate_limited = 0

        for attempt in range(1, self.retries + 1):
            try:
                r = self._raw_get(url)

            except requests.RequestException as e:
                log.warning(
                    "通信エラー(%d/%d) %s: %s",
                    attempt,
                    self.retries,
                    url,
                    e,
                )
                time.sleep(5 * attempt)
                continue

            code = r.status_code

            if code == 200:
                return decode_html(r.content)

            if code == 403:
                raise BlockedError(f"403 Forbidden: {url}")

            if code == 429:
                rate_limited += 1

                if rate_limited >= 2:
                    raise BlockedError(
                        f"429 Too Many Requests が続きました: {url}"
                    )

                ra = r.headers.get("Retry-After", "")
                wait = int(ra) if ra.isdigit() else 60

                log.warning("429 を受信。%d秒待機します", wait)
                time.sleep(wait)
                continue

            if code == 404:
                raise FetchError(f"404 Not Found: {url}")

            log.warning(
                "HTTP %d (%d/%d) %s",
                code,
                attempt,
                self.retries,
                url,
            )
            time.sleep(5 * attempt)

        raise FetchError(f"取得に失敗しました: {url}")


# --------------------------------------------------------------------------
# 出馬表パーサ
# --------------------------------------------------------------------------

def parse_shutuba(
    html: str,
    race_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:

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

    # 出馬表テーブルを確認する
    table = soup.select_one("table.Shutuba_Table")

    if table is not None:

        # 通常の出走馬行を取得する
        for tr in table.select("tr.HorseList"):

            # まずHorseInfo内の馬リンクを探す
            a_horse = tr.select_one(
                'td.HorseInfo a[href*="/horse/"]'
            )

            # HTML構造が異なる場合は行全体から探す
            if a_horse is None:
                a_horse = tr.select_one(
                    'a[href*="/horse/"]'
                )

            # 馬リンクがない行は読み飛ばす
            if a_horse is None:
                continue

            href = a_horse.get("href", "")
            hid = rx(r"/horse/(\d+)", href)

            a_jk = tr.select_one("td.Jockey a")
            jk_href = a_jk.get("href", "") if a_jk else ""

            td_barei = tr.select_one("td.Barei")
            sexage = nfkc(text_of(td_barei))

            m_sa = re.search(r"([牡牝セ])\s*(\d+)", sexage)

            # 斤量は従来どおりBarei列の次のセルから取得
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
                "horse_url": (
                    HORSE_URL.format(horse_id=hid)
                    if hid
                    else None
                ),
                "frame_no": to_int(
                    text_of(tr.select_one("td[class*=Waku]"))
                ),
                "horse_no": to_int(
                    text_of(tr.select_one("td[class*=Umaban]"))
                ),
                "sex": m_sa[1] if m_sa else None,
                "age": int(m_sa[2]) if m_sa else None,
                "weight_carried": (
                    wc
                    if wc is not None and 40 <= wc <= 70
                    else None
                ),
                "jockey": {
                    "name": text_of(a_jk),
                    "id": rx(
                        r"/jockey/(?:[a-z]+/)*(\d{4,6})",
                        jk_href,
                    ),
                },
                "trainer": text_of(
                    tr.select_one("td.Trainer a")
                ),
                "body_weight": (
                    int(m_bw[1]) if m_bw else None
                ),
                "body_weight_diff": (
                    int(m_bw[2]) if m_bw else None
                ),
                "odds": to_float(
                    text_of(tr.select_one("td.Popular"))
                ),
                "popularity": to_int(
                    text_of(tr.select_one("td.Popular_Ninki"))
                ),
                "scratched": "Cancel" in (tr.get("class") or []),
                "history_status": "not_fetched",
                "history_fetched_at": None,
                "past_results": [],
            })

    return race, horses


# --------------------------------------------------------------------------
# 馬の過去成績パーサ
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# JSON保存・キャッシュ
# --------------------------------------------------------------------------

def atomic_write_json(
    path: Path,
    obj: Any,
    backup: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                obj,
                f,
                ensure_ascii=False,
                indent=2,
            )
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        if backup and path.exists():
            shutil.copy2(
                path,
                path.with_name(path.name + ".bak"),
            )

        os.replace(tmp, path)

    except BaseException:
        try:
            os.unlink(tmp)
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
            raise ValueError("races がオブジェクトではありません")

        return data

    except (
        json.JSONDecodeError,
        ValueError,
        AttributeError,
    ) as e:
        aside = path.with_name(
            f"{path.name}.corrupt.{int(time.time())}"
        )

        shutil.move(path, aside)

        log.error(
            "既存ファイルを読めないため退避しました(%s): %s",
            aside,
            e,
        )

        return empty


class HorseHistoryStore:
    def __init__(
        self,
        cache_dir: Path,
        ttl_hours: float,
    ):
        self.dir = cache_dir
        self.ttl = ttl_hours * 3600

    def _p(self, horse_id: str) -> Path:
        return self.dir / f"{horse_id}.json"

    def read(
        self,
        horse_id: str,
    ) -> Optional[dict[str, Any]]:
        try:
            return json.loads(
                self._p(horse_id).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None

    def is_fresh(
        self,
        entry: Optional[dict[str, Any]],
    ) -> bool:
        return (
            bool(entry)
            and (
                time.time() - entry.get("fetched_epoch", 0)
            ) < self.ttl
        )

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
            self._p(horse_id),
            entry,
            backup=False,
        )

        return entry


# --------------------------------------------------------------------------
# 過去成績の選択・統合
# --------------------------------------------------------------------------

def select_history(
    rows: list[dict[str, Any]],
    race_id: str,
    race_date: Optional[str],
    n: int,
) -> list[dict[str, Any]]:
    """対象レース自身・対象日以降を除外して最大n走を選ぶ。"""

    out = []

    for r in rows:
        if r.get("race_id") == race_id:
            continue

        if (
            race_date
            and r.get("date")
            and r["date"] >= race_date
        ):
            continue

        out.append(r)

        if len(out) >= n:
            break

    return out


def fill_history(
    fetcher: Fetcher,
    cache: HorseHistoryStore,
    horse: dict[str, Any],
    race_id: str,
    race_date: Optional[str],
    n: int,
) -> None:
    hid = horse["horse_id"]

    entry = cache.read(hid)
    status = "cache"

    if not cache.is_fresh(entry):
        try:
            rows = parse_history(
                fetcher.get(HORSE_URL.format(horse_id=hid))
            )

            entry = cache.write(hid, rows)
            status = "ok"

        except (FetchError, ParseError) as e:
            if entry is None:
                raise

            log.warning(
                "%s 再取得に失敗。古いキャッシュを使用: %s",
                hid,
                e,
            )
            status = "stale"

    if not entry["has_table"]:
        horse.update(
            history_status="no_table",
            history_fetched_at=entry["fetched_at"],
            past_results=[],
        )
        return

    picked = select_history(
        entry["rows"],
        race_id,
        race_date,
        n,
    )

    if status in ("ok", "cache"):
        status = "ok" if picked else "no_data"

    horse.update(
        history_status=status,
        history_fetched_at=entry["fetched_at"],
        past_results=picked,
    )


def build_race(
    fetcher: Fetcher,
    cache: HorseHistoryStore,
    race_id: str,
    n: int,
    race_date_override: Optional[str],
    prev_race: Optional[dict[str, Any]],
    limit_horses: Optional[int],
) -> tuple[dict[str, Any], bool]:

    race, horses = parse_shutuba(
        fetcher.get(SHUTUBA_URL.format(race_id=race_id)),
        race_id,
    )

    if not horses:
        raise ParseError(
            f"出走馬を1頭も取得できませんでした "
            f"(race_id={race_id})"
        )

    if race["date"] is None and not race_date_override:
        log.warning(
            "開催日を取得できませんでした。"
            "--race-date の指定を推奨します"
        )

    race_date = race_date_override or race["date"]

    if race_date_override:
        race["date"] = race_date_override

    prev = {
        h["horse_id"]: h
        for h in (prev_race or {}).get("horses", [])
        if h.get("horse_id")
    }

    blocked = False

    targets = [
        h for h in horses
        if h["horse_id"] and not h["scratched"]
    ]

    if limit_horses:
        targets = targets[:limit_horses]

    target_ids = {id(h) for h in targets}

    for i, h in enumerate(horses, 1):

        if not h["horse_id"]:
            h["history_status"] = "no_horse_id"
            continue

        if h["scratched"]:
            h["history_status"] = "scratched"
            continue

        if id(h) not in target_ids:
            continue

        log.info(
            "[%d/%d] %s (%s)",
            i,
            len(horses),
            h["horse_name"],
            h["horse_id"],
        )

        try:
            if blocked:
                raise FetchError("中断中")

            fill_history(
                fetcher,
                cache,
                h,
                race_id,
                race_date,
                n,
            )

        except BlockedError as e:
            log.error(
                "アクセス制限の可能性: %s "
                "→ 残りの取得を中断します",
                e,
            )
            blocked = True

        except (FetchError, ParseError) as e:
            log.warning(
                "%s の履歴取得に失敗: %s",
                h["horse_name"],
                e,
            )

        if h["history_status"] == "not_fetched":
            old = prev.get(h["horse_id"])

            if old and old.get("past_results"):
                h.update(
                    history_status="stale",
                    history_fetched_at=old.get(
                        "history_fetched_at"
                    ),
                    past_results=old["past_results"],
                )
            else:
                h["history_status"] = "error"

    race["history_depth"] = n
    race["horses"] = horses

    return race, blocked


# --------------------------------------------------------------------------
# メイン処理
# --------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="出馬表 + 各馬の過去成績を統合取得"
    )

    ap.add_argument(
        "race_ids",
        nargs="+",
        help="12桁のrace_id(例: 202505021211)",
    )

    ap.add_argument(
        "--out",
        default="data/raw_shutuba_with_history.json",
    )
    ap.add_argument(
        "--cache-dir",
        default="data/cache/horse_history",
    )
    ap.add_argument(
        "--cache-ttl-hours",
        type=float,
        default=168.0,
    )
    ap.add_argument(
        "--history",
        type=int,
        default=3,
        help="格納する過去走数(既定3)",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=2.5,
        help="リクエスト間隔の下限(秒)",
    )
    ap.add_argument(
        "--race-date",
        help="YYYY-MM-DD。開催日が取れない場合に指定",
    )
    ap.add_argument(
        "--limit-horses",
        type=int,
        help="テスト用: 履歴を取る頭数の上限",
    )
    ap.add_argument(
        "--user-agent",
        default=os.environ.get("KEIBA_UA", DEFAULT_UA),
    )
    ap.add_argument(
        "--ignore-robots",
        action="store_true",
        help="robots.txt を確認しない(非推奨)",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="store_true",
    )

    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.sleep < 1.0:
        log.warning(
            "--sleep が短すぎます。1.0秒に引き上げます"
        )
        args.sleep = 1.0

    if args.history < 1:
        ap.error("--history は1以上を指定してください")

    if args.limit_horses is not None and args.limit_horses < 1:
        ap.error("--limit-horses は1以上を指定してください")

    bad = [
        r for r in args.race_ids
        if not re.fullmatch(r"\d{12}", r)
    ]

    if bad:
        ap.error(f"race_id は12桁の数字です: {bad}")

    out = Path(args.out)
    store = load_store(out)

    fetcher = Fetcher(
        args.sleep,
        args.user_agent,
        respect_robots=not args.ignore_robots,
    )

    cache = HorseHistoryStore(
        Path(args.cache_dir),
        args.cache_ttl_hours,
    )

    exit_code = 0

    for rid in args.race_ids:
        log.info("=== race_id=%s ===", rid)

        try:
            race, blocked = build_race(
                fetcher,
                cache,
                rid,
                args.history,
                args.race_date,
                store["races"].get(rid),
                args.limit_horses,
            )

        except BlockedError as e:
            log.error(
                "出馬表の取得でアクセス制限: %s",
                e,
            )
            exit_code = 2
            break

        except (FetchError, ParseError) as e:
            log.error(
                "race_id=%s をスキップ"
                "(既存データは変更しません): %s",
                rid,
                e,
            )
            exit_code = max(exit_code, 1)
            continue

        store["races"][rid] = race
        store["updated_at"] = now_iso()

        atomic_write_json(out, store)

        ok = sum(
            h["history_status"] in ("ok", "no_data", "cache")
            for h in race["horses"]
        )

        log.info(
            "保存: %s (%d頭 / 履歴OK %d頭) "
            "リクエスト累計 %d回",
            out,
            len(race["horses"]),
            ok,
            fetcher.requests_made,
        )

        if blocked:
            exit_code = 2
            break

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
