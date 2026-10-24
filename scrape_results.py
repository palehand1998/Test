from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from validate_results import validate

OUTPUT = Path("data/raw_race_results.json")
BASE_URL = "https://race.netkeiba.com/race/result.html"


def text(element):
    return element.get_text(" ", strip=True) if element else ""


def integer(value):
    if value is None:
        return None
    match = re.search(r"-?\d+", str(value).replace(",", ""))
    return int(match.group()) if match else None


def number(value):
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def fetch_page(race_id):
    response = requests.get(
        BASE_URL,
        params={"race_id": race_id},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
            )
        },
        timeout=30,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "euc-jp"
    time.sleep(2)
    return response.text


def first_text(soup, selectors):
    for selector in selectors:
        element = soup.select_one(selector)
        value = text(element)
        if value:
            return value
    return ""


def parse_race(soup, race_id, result_count):
    name = first_text(
        soup,
        [".RaceName", ".RaceList_NameBox .RaceName", "title"],
    )
    if not name:
        raise ValueError("レース名を検出できません。")

    race_data_text = [
        text(el)
        for el in soup.select(".RaceData01, .RaceData02")
        if text(el)
    ]

    combined = " ".join(race_data_text)
    date_match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", combined)
    date_value = (
        f"{int(date_match.group(1)):04d}-"
        f"{int(date_match.group(2)):02d}-"
        f"{int(date_match.group(3)):02d}"
        if date_match
        else None
    )

    distance_match = re.search(r"(芝|ダート?|障害)\s*[,：:]?\s*(\d{3,4})\s*m", combined)
    surface = None
    distance = None
    if distance_match:
        surface = distance_match.group(1)
        distance = int(distance_match.group(2))

    race_number = integer(race_id[-2:])
    if race_number is None or not 1 <= race_number <= 12:
        raise ValueError(f"レース番号を判定できません: {race_id}")

    return {
        "race_id": race_id,
        "name": name,
        "date": date_value,
        "venue": None,
        "race_number": race_number,
        "start_time": None,
        "grade": None,
        "surface": surface,
        "distance_m": distance,
        "course_direction": None,
        "course_layout": None,
        "weather": None,
        "track_condition": None,
        "number_of_runners": result_count,
        "race_data_text": race_data_text,
    }


def parse_results(soup):
    table = soup.select_one("#All_Result_Table")
    if table is None:
        table = soup.select_one("table.RaceTable01")

    if table is None:
        raise ValueError("着順テーブルが見つかりません。")

    results = []

    for row in table.select("tr.HorseList"):
        horse_link = row.select_one("a[href*='/horse/']")
        if horse_link is None:
            continue

        cells = row.find_all("td")

        def cell_text(class_name):
            return text(row.select_one(f"td.{class_name}"))

        rank_text = cell_text("Result_Num")
        frame_text = cell_text("Waku")
        horse_number_text = cell_text("Umaban")

        # クラス名が異なる場合に備えて、馬番は3番目のセルも確認
        if not horse_number_text and len(cells) >= 3:
            horse_number_text = text(cells[2])

        name = text(horse_link)
        if not name:
            continue

        horse_id_match = re.search(
            r"/horse/(\d+)", horse_link.get("href", "")
        )

        sex = None
        age = None
        sex_age = text(row.select_one(".Barei, .Txt_C"))
        sex_age_match = re.search(r"([牡牝セ騸])\s*(\d+)", sex_age)
        if sex_age_match:
            sex = sex_age_match.group(1)
            age = int(sex_age_match.group(2))

        weight_text = cell_text("Weight")
        body_match = re.search(
            r"(\d+)\s*\(([+-]?\d+)\)", weight_text
        )

        time_text = cell_text("Time")
        time_match = re.search(r"\d+:\d{2}(?:\.\d+)?", time_text)
        finish_time = time_match.group() if time_match else (
            time_text or None
        )

        # 人気と単勝オッズは別々に取得する
        odds_text = text(row.select_one("td.Odds"))
        odds_values = re.findall(r"\d+(?:\.\d+)?", odds_text)

        popularity = None
        popularity_el = row.select_one(".OddsPeople")
        if popularity_el:
            popularity = integer(text(popularity_el))

        win_odds = number(odds_values[0]) if odds_values else None

        passing = text(
            row.select_one(".PassageRate, .Passage, .Corner")
        )

        last_3f = number(cell_text("Agari"))
        if last_3f is None:
            last_3f = number(cell_text("Last3F"))

        results.append({
            "finish_position": integer(rank_text),
            "frame_number": integer(frame_text),
            "horse_number": integer(horse_number_text),
            "horse_name": name,
            "horse_id": horse_id_match.group(1) if horse_id_match else None,
            "sex": sex,
            "age": age,
            "carried_weight_kg": number(
                cell_text("JockeyWeight") or cell_text("Kisyu")
            ),
            "jockey": text(row.select_one("td.Jockey a")) or None,
            "finish_time": finish_time,
            "margin": cell_text("Mgn") or None,
            "popularity": popularity,
            "win_odds": win_odds,
            "last_3f_seconds": last_3f,
            "passing_order": passing or None,
            "trainer": text(row.select_one("td.Trainer a")) or None,
            "body_weight_kg": (
                int(body_match.group(1)) if body_match else None
            ),
            "body_weight_change_kg": (
                int(body_match.group(2)) if body_match else None
            ),
        })

    if not results:
        raise ValueError("着順データを1頭も取得できませんでした。")

    numbers = [item["horse_number"] for item in results]
    if any(n is None or not 1 <= n <= 18 for n in numbers):
        raise ValueError(
            "馬番を正しく読み取れません。HTML構造の確認が必要です。"
        )

    if len(numbers) != len(set(numbers)):
        raise ValueError("取得結果に重複した馬番があります。")

    return results


def parse_payouts(soup):
    payouts = []
    seen = set()

    for table in soup.select("table"):
        for row in table.select("tr"):
            cells = [text(cell) for cell in row.find_all(["th", "td"])]
            raw_text = " ".join(value for value in cells if value)

            if not re.search(
                r"単勝|複勝|枠連|馬連|ワイド|馬単|3連複|3連単|三連複|三連単",
                raw_text,
            ):
                continue

            if not raw_text:
                continue

            key = tuple(cells)
            if key in seen:
                continue

            seen.add(key)
            payouts.append({
                "raw_cells": cells,
                "raw_text": raw_text,
            })

    if not payouts:
        raise ValueError("払戻データを検出できません。")

    return payouts


def parse_corner_orders(soup):
    corners = []
    seen = set()

    # コーナー通過順位を含む見出しやブロックを探す
    for element in soup.find_all(True):
        label = text(element)
        if not label or not re.search(
            r"コーナー通過順位|[１-４1-4]コーナー|[１-４1-4]角",
            label,
        ):
            continue

        # 大きな親要素の全文を重複収集しない
        if len(label) > 500:
            continue

        if label not in seen:
            seen.add(label)
            corners.append({"raw_text": label})

    if not corners:
        # 個別のコーナー情報が見つからない場合、通過順列を保存
        for result in []:
            pass

    if not corners:
        raise ValueError(
            "コーナー通過順を検出できません。HTML構造の確認が必要です。"
        )

    return corners


def parse_laps(soup):
    # HTML内でラップタイム関連の文字列を検索
    candidates = []

    for element in soup.find_all(True):
        cls = " ".join(element.get("class", []))
        element_id = element.get("id", "")
        if not re.search(r"lap|haron|pace|ラップ|ハロン", cls + " " + element_id, re.I):
            continue

        value = text(element)
        if value:
            candidates.append(value)

    page_text = soup.get_text("\n", strip=True)
    for line in page_text.splitlines():
        if re.search(r"ラップ|ハロンタイム|ペース", line):
            candidates.append(line)

    for raw in dict.fromkeys(candidates):
        values = re.findall(r"(?<!\d)\d{1,2}\.\d(?!\d)", raw)

        if len(values) >= 3:
            sectional = [float(v) for v in values]
            cumulative = []
            total = 0.0

            for value in sectional:
                total = round(total + value, 1)
                cumulative.append(total)

            return {
                "pace": None,
                "sectional_200m": sectional,
                "cumulative_200m": cumulative,
                "last_4f": round(sum(sectional[-4:]), 1),
                "last_3f": round(sum(sectional[-3:]), 1),
                "raw_text": [raw],
            }

    raise ValueError(
        "ラップタイムを検出できません。"
        "不完全なJSONの保存を防ぐため処理を中止しました。"
    )


def build_payload(race_id, html):
    soup = BeautifulSoup(html, "html.parser")
    results = parse_results(soup)

    race = parse_race(soup, race_id, len(results))
    payouts = parse_payouts(soup)
    corner_orders = parse_corner_orders(soup)
    lap_times = parse_laps(soup)

    url = f"{BASE_URL}?race_id={race_id}"

    return {
        "schema_version": "1.0",
        "race_id": race_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "race": race,
        "results": results,
        "payouts": payouts,
        "corner_orders": corner_orders,
        "lap_times": lap_times,
        "sources": {
            "race_info": url,
            "results": url,
            "payouts": url,
            "corner_orders": url,
            "lap_times": url,
        },
    }


def save_atomically(payload):
    # 既存の検証関数を使い、保存前にJSON全体を検証する
    errors = validate(payload)
    if errors:
        raise ValueError(
            "保存前の検証に失敗しました:\n- " + "\n- ".join(errors)
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp_path = OUTPUT.with_suffix(".json.tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    temp_path.replace(OUTPUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--race-id", default="202505021211")
    args = parser.parse_args()

    race_id = args.race_id.strip()
    if not re.fullmatch(r"\d{12}", race_id):
        print("ERROR: race-id は12桁の数字で指定してください。")
        return 1

    try:
        print(f"レース結果を取得中: {race_id}")
        html = fetch_page(race_id)
        payload = build_payload(race_id, html)
        save_atomically(payload)

        print("取得と検証に成功しました。")
        print(f"出走馬: {len(payload['results'])}")
        print(f"払戻行数: {len(payload['payouts'])}")
        print(f"コーナー情報数: {len(payload['corner_orders'])}")
        print(
            "ラップ区間数: "
            f"{len(payload['lap_times']['sectional_200m'])}"
        )
        print(f"保存先: {OUTPUT}")
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("既存JSONは置換していません。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
