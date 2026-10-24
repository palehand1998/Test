# scrape_results.py
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://race.netkeiba.com/race/result.html"
OUTPUT = Path("data/raw_race_results.json")
USER_AGENT = (
    "Mozilla/5.0 (compatible; RaceDataResearch/1.0; "
    "+https://github.com/)"
)
REQUEST_DELAY_SECONDS = 2.0
TIMEOUT_SECONDS = 20


def clean(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def first_text(soup, selectors: list[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = clean(node)
            if value:
                return value
    return ""


def parse_number(value: str):
    """数値に変換できる場合のみ変換。未取得・特殊表記は None。"""
    value = value.strip().replace(",", "")
    if not value or value in {"-", "--", "取消", "除外", "中止"}:
        return None
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return None


def parse_weight(value: str):
    """例: '504(+4)' -> (504, 4)"""
    match = re.search(r"(\d+)\s*\(([+-]?\d+)\)", value)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"\b(\d+)\b", value)
    return (int(match.group(1)), None) if match else (None, None)


def get_page(race_id: str) -> BeautifulSoup:
    url = f"{BASE_URL}?race_id={race_id}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
    }

    # 連続アクセスを避ける。リトライによる連続取得もしない。
    time.sleep(REQUEST_DELAY_SECONDS)

    response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"

    soup = BeautifulSoup(response.text, "html.parser")

    # エラーページや空ページを結果ページと誤認しない。
    if not soup.select_one(
        "#All_Result_Table, table.ResultTable01, table.RaceTable01"
    ):
        raise ValueError(
            "結果テーブルが見つかりません。HTML構造変更・アクセス制限・"
            "レースIDを確認してください。"
        )
    return soup


def parse_race_info(soup: BeautifulSoup, race_id: str) -> dict:
    title = first_text(soup, ["h1.RaceName", ".RaceName", "title"])
    data1 = first_text(soup, [".RaceData01"])
    data2 = first_text(soup, [".RaceData02"])
    combined = f"{data1} {data2}"

    distance_match = re.search(r"(芝|ダート?|障害)\s*(\d{3,4})m", combined)
    start_match = re.search(r"発走\s*[:：]?\s*(\d{1,2}:\d{2})", combined)
    weather_match = re.search(r"天候\s*[:：]\s*([^\s/]+)", combined)
    condition_match = re.search(
        r"(?:芝|ダート?|ダート|馬場)\s*[:：]\s*(良|稍重|重|不良)",
        combined,
    )

    surface = None
    distance_m = None
    if distance_match:
        surface = distance_match.group(1)
        distance_m = int(distance_match.group(2))

    race_number = int(race_id[-2:])

    return {
        "race_id": race_id,
        "name": title or None,
        "date": None,  # ページ内の開催日表示を確認できた場合に追加
        "venue": None,  # ページ内の競馬場表示を確認できた場合に追加
        "race_number": race_number,
        "start_time": start_match.group(1) if start_match else None,
        "grade": None,
        "surface": surface,
        "distance_m": distance_m,
        "course_direction": None,
        "course_layout": None,
        "weather": weather_match.group(1) if weather_match else None,
        "track_condition": condition_match.group(1) if condition_match else None,
        "number_of_runners": None,
        "race_data_text": [x for x in (data1, data2) if x],
    }


def parse_results(soup: BeautifulSoup) -> list[dict]:
    table = soup.select_one(
        "#All_Result_Table, table.ResultTable01, table.RaceTable01"
    )
    if not table:
        raise ValueError("着順テーブルが見つかりません")

    headers = [clean(th) for th in table.select("thead th")]
    if not headers:
        header_row = table.select_one("tr.Header")
        headers = [clean(th) for th in header_row.select("th")] if header_row else []

    def col_index(pattern: str):
        for i, header in enumerate(headers):
            if re.search(pattern, header):
                return i
        return None

    columns = {
        "finish": col_index(r"着順"),
        "frame": col_index(r"枠"),
        "number": col_index(r"馬番"),
        "horse": col_index(r"馬名"),
        "sex_age": col_index(r"性齢"),
        "weight": col_index(r"斤量"),
        "jockey": col_index(r"騎手"),
        "time": col_index(r"タイム"),
        "margin": col_index(r"着差"),
        "popularity": col_index(r"人気"),
        "odds": col_index(r"単勝オッズ|オッズ"),
        "last3f": col_index(r"後?3F"),
        "passing": col_index(r"コーナー通過順"),
        "trainer": col_index(r"厩舎"),
        "body_weight": col_index(r"馬体重"),
    }

    rows = table.select("tbody tr.HorseList")
    if not rows:
        rows = table.select("tbody tr")

    results = []
    for row in rows:
        cells = row.select("td")
        if not cells:
            continue

        def cell(key: str) -> str:
            idx = columns.get(key)
            return clean(cells[idx]) if idx is not None and idx < len(cells) else ""

        horse_cell = (
            cells[columns["horse"]]
            if columns["horse"] is not None and columns["horse"] < len(cells)
            else None
        )
        horse_link = horse_cell.select_one("a[href*='/horse/']") if horse_cell else None
        horse_name = clean(horse_link) if horse_link else cell("horse")
        horse_href = horse_link.get("href", "") if horse_link else ""
        horse_id_match = re.search(r"/horse/(\d+)", horse_href)

        if not horse_name:
            continue

        sex_age = cell("sex_age")
        sex = sex_age[0] if sex_age and sex_age[0] in "牡牝セ" else None
        age_match = re.search(r"\d+", sex_age)
        body_weight, body_weight_change = parse_weight(cell("body_weight"))

        results.append({
            "finish_position": parse_number(cell("finish")),
            "frame_number": parse_number(cell("frame")),
            "horse_number": parse_number(cell("number")),
            "horse_name": horse_name,
            "horse_id": horse_id_match.group(1) if horse_id_match else None,
            "sex": sex,
            "age": int(age_match.group()) if age_match else None,
            "carried_weight_kg": parse_number(cell("weight")),
            "jockey": cell("jockey") or None,
            "finish_time": cell("time") or None,
            "margin": cell("margin") or None,
            "popularity": parse_number(cell("popularity")),
            "win_odds": parse_number(cell("odds")),
            "last_3f_seconds": parse_number(cell("last3f")),
            "passing_order": cell("passing") or None,
            "trainer": cell("trainer") or None,
            "body_weight_kg": body_weight,
            "body_weight_change_kg": body_weight_change,
        })

    if not results:
        raise ValueError("着順データを1頭も抽出できませんでした")

    return results


def parse_payouts(soup: BeautifulSoup) -> list[dict]:
    """払戻テーブルを検出。DOM変更時は未検出となるため検証で確認する。"""
    payouts = []
    payout_tables = soup.select(
        "table.Pay_Table_01, table.Payout_Detail_Table, "
        "table[class*='Pay_Table'], table[class*='Payout']"
    )

    seen = set()
    for table in payout_tables:
        for row in table.select("tr"):
            cells = [clean(x) for x in row.select("th, td")]
            cells = [x for x in cells if x]
            if len(cells) < 2:
                continue

            text = " ".join(cells)
            if not re.search(
                r"単勝|複勝|枠連|馬連|ワイド|馬単|3連複|3連単|三連複|三連単",
                text,
            ):
                continue

            key = tuple(cells)
            if key in seen:
                continue
            seen.add(key)

            payouts.append({
                "raw_cells": cells,
                "raw_text": text,
            })

    return payouts


def parse_corner_orders(soup: BeautifulSoup) -> list[dict]:
    corners = []
    selectors = [
        ".Corner_Num",
        ".RaceCorner",
        ".Corner_Info",
        "div[class*='Corner']",
        "table[class*='Corner']",
    ]

    seen = set()
    for selector in selectors:
        for node in soup.select(selector):
            text = clean(node)
            if not text or text in seen:
                continue
            if re.search(r"[1-4]\s*コーナー|[１-４]コーナー|コーナー通過", text):
                seen.add(text)
                corners.append({"raw_text": text})

    return corners


def parse_lap_times(soup: BeautifulSoup) -> dict:
    candidates = []
    selectors = [
        ".LapTime",
        ".RaceLapTime",
        ".Lap_Time",
        "table.Lap_Table",
        "table[class*='Lap']",
        "div[class*='Lap']",
    ]

    seen = set()
    for selector in selectors:
        for node in soup.select(selector):
            text = clean(node)
            if text and re.search(r"\d+\.\d+", text) and text not in seen:
                seen.add(text)
                candidates.append(text)

    # ペースやラップの数字列を候補から抽出する。
    sectional = []
    for text in candidates:
        values = re.findall(r"(?<!\d)(\d{1,2}\.\d)(?!\d)", text)
        if len(values) >= 3:
            sectional = [float(v) for v in values]
            break

    return {
        "pace": None,
        "sectional_200m": sectional,
        "cumulative_200m": [],
        "last_4f": None,
        "last_3f": None,
        "raw_text": candidates,
    }


def atomic_json_write(path: Path, payload: dict) -> None:
    """一時ファイルへ書いてから置換。書き込み失敗で既存JSONを壊さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None

    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp_name = temp.name
            json.dump(payload, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp.flush()

        Path(temp_name).replace(path)
    finally:
        if temp_name and Path(temp_name).exists():
            Path(temp_name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--race-id", default="202505021211")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    if not re.fullmatch(r"\d{12}", args.race_id):
        print("ERROR: race_id は12桁の数字で指定してください", file=sys.stderr)
        return 2

    try:
        soup = get_page(args.race_id)
        race = parse_race_info(soup, args.race_id)
        results = parse_results(soup)
        payouts = parse_payouts(soup)
        corners = parse_corner_orders(soup)
        laps = parse_lap_times(soup)

        race["number_of_runners"] = len(results)

        # 必須カテゴリの欠落を検出。未対応DOMを成功扱いで保存しない。
        if not race.get("name"):
            raise ValueError("レース名を取得できません")
        if not payouts:
            raise ValueError("払戻データを検出できません。HTML構造を確認してください")
        if not corners:
            raise ValueError("コーナー通過順位を検出できません。HTML構造を確認してください")
        if not laps["sectional_200m"]:
            raise ValueError("ラップタイムを検出できません。HTML構造を確認してください")

        payload = {
            "schema_version": "1.0",
            "race_id": args.race_id,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "race": race,
            "results": results,
            "payouts": payouts,
            "corner_orders": corners,
            "lap_times": laps,
            "sources": {
                "race_info": "netkeiba_result_page",
                "results": "netkeiba_result_page",
                "payouts": "netkeiba_result_page",
                "corner_orders": "netkeiba_result_page",
                "lap_times": "netkeiba_result_page",
            },
        }

        # 最終的な型・キー検査を通過してから既存ファイルを置き換える。
        validate_payload(payload)
        atomic_json_write(Path(args.output), payload)

        print(f"OK: {args.output} に {len(results)} 頭分を保存しました")
        return 0

    except (requests.RequestException, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("既存JSONは置換していません。", file=sys.stderr)
        return 1


def validate_payload(data: dict) -> None:
    """保存前の最低限のスキーマ検査。詳細検査は validate_results.py で実施。"""
    required = {
        "schema_version", "race_id", "fetched_at", "race",
        "results", "payouts", "corner_orders", "lap_times", "sources",
    }
    missing = required - data.keys()
    if missing:
        raise ValueError(f"必須キー不足: {sorted(missing)}")
    if not isinstance(data["race"], dict) or not data["race"].get("name"):
        raise ValueError("race.name がありません")
    if not isinstance(data["results"], list) or not data["results"]:
        raise ValueError("results が空です")
    if not isinstance(data["payouts"], list) or not data["payouts"]:
        raise ValueError("payouts が空です")
    if not isinstance(data["corner_orders"], list) or not data["corner_orders"]:
        raise ValueError("corner_orders が空です")
    if not isinstance(data["lap_times"], dict):
        raise ValueError("lap_times がオブジェクトではありません")


if __name__ == "__main__":
    raise SystemExit(main())
