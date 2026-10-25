
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
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        if backup and path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))

        os.replace(tmp, path)

    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class HorseHistoryStore:
    def __init__(self, cache_dir: Path, ttl_hours: float):
        self.dir = cache_dir
        self.ttl = ttl_hours * 3600

    def _p(self, horse_id: str) -> Path:
        return self.dir / f"{horse_id}.json"

    def read(self, horse_id: str) -> Optional[dict[str, Any]]:
        try:
            return json.loads(
                self._p(horse_id).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None

    def is_fresh(
        self, entry: Optional[dict[str, Any]]
    ) -> bool:
        return (
            bool(entry)
            and time.time() - entry.get("fetched_epoch", 0)
            < self.ttl
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

        atomic_write_json(self._p(horse_id), entry, backup=False)
        return entry


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

        if race_date and r.get("date") and r["date"] >= race_date:
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

        except BlockedError:
            raise

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


def load_shutuba(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"出馬表JSONがありません: {path}"
        )

    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data.get("races"), dict):
        raise ValueError(
            "出馬表JSONの形式が不正です（races がありません）"
        )

    return data


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="保存済み出馬表から各馬の過去成績を取得"
    )

    ap.add_argument(
        "race_ids",
        nargs="+",
        help="12桁のrace_id",
    )
    ap.add_argument(
        "--in",
        dest="input_path",
        default="data/shutuba.json",
        help="入力する出馬表JSON",
    )
    ap.add_argument(
        "--out",
        default="data/horse_history.json",
        help="過去成績の保存先",
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
        help="保存する過去走数",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=2.5,
    )
    ap.add_argument(
        "--user-agent",
        default=os.environ.get("KEIBA_UA", DEFAULT_UA),
    )
    ap.add_argument("-v", "--verbose", action="store_true")

    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.history < 1:
        ap.error("--history は1以上を指定してください")

    if args.cache_ttl_hours <= 0:
        ap.error("--cache-ttl-hours は0より大きい値を指定してください")
    
    if args.sleep < 1.0:
        log.warning("--sleep が短すぎるため1.0秒に引き上げます")
        args.sleep = 1.0

    bad = [
        rid for rid in args.race_ids
        if not re.fullmatch(r"\d{12}", rid)
    ]
    if bad:
        ap.error(f"race_id は12桁の数字です: {bad}")

    try:
        shutuba = load_shutuba(Path(args.input_path))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        log.error("出馬表の読み込みに失敗: %s", e)
        return 1

    out = Path(args.out)
    store = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "races": {},
    }

    if out.exists():
        try:
            existing = json.loads(
                out.read_text(encoding="utf-8")
            )

            if (
                not isinstance(existing, dict)
                or not isinstance(existing.get("races"), dict)
            ):
                log.error(
                    "既存の履歴JSONの形式が不正です。保存を中止します"
                )
                return 1

            store = existing

        except (OSError, json.JSONDecodeError):
            log.error(
                "既存の履歴JSONを読み込めません。保存を中止します"
            )
            return 1
    fetcher = Fetcher(
        args.sleep,
        args.user_agent,
        respect_robots=True,
    )
    cache = HorseHistoryStore(
        Path(args.cache_dir),
        args.cache_ttl_hours,
    )

    exit_code = 0

    for rid in args.race_ids:
        race_entry = shutuba["races"].get(rid)
        if not isinstance(race_entry, dict):
            log.error("出馬表JSONに race_id=%s がありません", rid)
            exit_code = 1
            continue

        horses = race_entry.get("horses", [])
        if not isinstance(horses, list):
            log.error("出走馬データの形式が不正です: %s", rid)
            exit_code = 1
            continue

        race_date = race_entry.get("date")
        previous_race = store["races"].get(rid, {})
        results = dict(previous_race.get("horses", {}))

        for i, horse in enumerate(horses, 1):
            if not isinstance(horse, dict):
                continue

            hid = horse.get("horse_id")
            if not hid:
                continue

            if horse.get("scratched"):
                continue

            history_horse = {"horse_id": str(hid)}
            log.info(
                "[%d/%d] race_id=%s horse_id=%s",
                i, len(horses), rid, hid,
            )

            try:
                fill_history(
                    fetcher,
                    cache,
                    history_horse,
                    rid,
                    race_date,
                    args.history,
                )
            except BlockedError as e:
                log.error("アクセス制限の可能性: %s", e)
                exit_code = 2
                break
            except (FetchError, ParseError) as e:
                log.warning("horse_id=%s の履歴取得に失敗: %s", hid, e)
                history_horse.update(
                    history_status="error",
                    past_results=[],
                )
                exit_code = max(exit_code, 1)

            results[str(hid)] = history_horse


        if results:
            store["races"][rid] = {
                "race_id": rid,
                "date": race_date,
                "history_depth": args.history,
                "horses": results,
            }
            store["updated_at"] = now_iso()
            atomic_write_json(out, store)
            log.info("保存しました: %s", out)

        if exit_code == 2:
            break

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
