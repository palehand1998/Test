
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

PAYOUT_TYPES = {
    "単勝",
    "複勝",
    "枠連",
    "馬連",
    "ワイド",
    "馬単",
    "3連複",
    "3連単",
    "三連複",
    "三連単",
}


def text(element):
    """HTML要素から余分な空白を除いて文字列を取得する。"""
    return element.get_text(" ", strip=True) if element else ""


def integer(value):
    """文字列から整数を取得する。取得できない場合はNone。"""
    if value is None:
        return None

    value = str(value).replace(",", "")
    match = re.search(r"-?\d+", value)
    return int(match.group()) if match else None


def number(value):
    """文字列から小数を取得する。取得できない場合はNone。"""
    if value is None:
        return None

    value = str(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None


def normalize_label(value):
    """見出し用文字列の空白を正規化する。"""
    return re.sub(r"\s+", "", value or "")


def fetch_page(race_id):
    """レース結果ページを取得する。"""
    url = f"{BASE_URL}?race_id={race_id}"

    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        },
        timeout=30,
    )
    response.raise_for_status()

    response.encoding = (
        response.apparent_encoding or "euc-jp"
    )

    # サーバーに連続アクセスしない
    time.sleep(2)

    return response.text


def first_text(soup, selectors):
    """候補セレクターから最初に見つかった文字列を返す。"""
    for selector in selectors:
        element = soup.select_one(selector)
        value = text(element)

        if value:
            return value

    return ""


def get_result_table(soup):
    """着順テーブルを取得する。"""
    selectors = [
        "#All_Result_Table",
        "table.RaceTable01",
    ]

    for selector in selectors:
        table = soup.select_one(selector)

        if table and table.select("tr.HorseList"):
            return table

    # クラス名が変更された場合の限定的なフォールバック
    for table in soup.select("table"):
        rows = table.select("tr.HorseList")

        if rows and any(
            row.select_one("a[href*='/horse/']")
            for row in rows
        ):
            return table

    raise ValueError(
        "着順テーブルが見つかりません。"
        "NetkeibaのHTML構造を確認してください。"
    )


def get_cell_text(cells, index):
    """指定位置のセル文字列を返す。"""
    if 0 <= index < len(cells):
        return text(cells[index])

    return ""


def get_cell_by_class(row, class_names, fallback_index):
    """クラス名を優先し、見つからなければ列位置を使う。"""
    for class_name in class_names:
        element = row.select_one(f"td.{class_name}")

        if element:
            return text(element)

    cells = row.find_all("td")
    return get_cell_text(cells, fallback_index)


def parse_results(soup):
    """全出走馬の着順データを取得する。"""
    table = get_result_table(soup)
    results = []

    for row in table.select("tr.HorseList"):
        horse_link = row.select_one("a[href*='/horse/']")

        if horse_link is None:
            continue

        cells = row.find_all("td")
        horse_name = text(horse_link)

        if not horse_name:
            continue

        href = horse_link.get("href", "")
        horse_id_match = re.search(r"/horse/(\d+)", href)

        # 標準的な着順表の列順をフォールバックに使用する
        # 0着順、1枠、2馬番、3馬名、4性齢、5斤量、
        # 6騎手、7タイム、8着差、9人気、10オッズ、
        # 11後3F、12通過順、13厩舎、14馬体重
        rank_text = get_cell_by_class(
            row, ["Result_Num"], 0
        )
        frame_text = get_cell_by_class(
            row, ["Waku"], 1
        )
        horse_number_text = get_cell_by_class(
            row, ["Umaban"], 2
        )

        if not horse_number_text:
            horse_number_text = get_cell_text(cells, 2)

        sex_age_text = get_cell_by_class(
            row, ["Barei", "Txt_C"], 4
        )
        sex_age_match = re.search(
            r"([牡牝セ騸])\s*(\d+)",
            sex_age_text,
        )

        sex = sex_age_match.group(1) if sex_age_match else None
        age = int(sex_age_match.group(2)) if sex_age_match else None

        carried_weight_text = get_cell_by_class(
            row, ["JockeyWeight", "KisyuWeight"], 5
        )

        jockey = (
            text(row.select_one("td.Jockey a"))
            or get_cell_text(cells, 6)
        )

        finish_time_text = get_cell_by_class(
            row, ["Time"], 7
        )
        finish_time_match = re.search(
            r"\d+:\d{2}(?:\.\d+)?",
            finish_time_text,
        )
        finish_time = (
            finish_time_match.group()
            if finish_time_match
            else finish_time_text or None
        )

        margin = get_cell_by_class(
            row, ["Mgn"], 8
        ) or None

        # 人気順位は専用のセルから取得する
        popularity_text = get_cell_by_class(
            row, ["OddsPeople", "Ninki"], 9
        )
        popularity = integer(popularity_text)

        # 単勝オッズは「Odds」だけで探さない。
        # 「Odds」だけだと人気順位のセルが先に選ばれるため、
        # 実際の単勝オッズを示す「Odds Txt_R」を指定する。
        odds_element = row.select_one("td.Odds.Txt_R")
        odds_text = text(odds_element) if odds_element else ""
        win_odds = number(odds_text) if odds_text else None

        last_3f_text = get_cell_by_class(
            row, ["Agari", "Last3F"], 11
        )
        last_3f = number(last_3f_text)

        passing_order = get_cell_by_class(
            row, ["PassageRate", "Passage", "Corner"], 12
        ) or None

        trainer = (
            text(row.select_one("td.Trainer a"))
            or get_cell_text(cells, 13)
            or None
        )

        body_weight_text = get_cell_by_class(
            row, ["Weight"], 14
        )
        body_match = re.search(
            r"(\d+)\s*\(([+-]?\d+)\)",
            body_weight_text,
        )

        body_weight = (
            int(body_match.group(1))
            if body_match
            else None
        )
        body_weight_change = (
            int(body_match.group(2))
            if body_match
            else None
        )

        results.append({
            "finish_position": integer(rank_text),
            "frame_number": integer(frame_text),
            "horse_number": integer(horse_number_text),
            "horse_name": horse_name,
            "horse_id": (
                horse_id_match.group(1)
                if horse_id_match
                else None
            ),
            "sex": sex,
            "age": age,
            "carried_weight_kg": number(
                carried_weight_text
            ),
            "jockey": jockey or None,
            "finish_time": finish_time,
            "margin": margin,
            "popularity": popularity,
            "win_odds": win_odds,
            "last_3f_seconds": last_3f,
            "passing_order": passing_order,
            "trainer": trainer,
            "body_weight_kg": body_weight,
            "body_weight_change_kg": body_weight_change,
        })

    if not results:
        raise ValueError("着順データを取得できませんでした。")

    horse_numbers = [
        item["horse_number"] for item in results
    ]

    if any(
        value is None or not 1 <= value <= 18
        for value in horse_numbers
    ):
        raise ValueError(
            "馬番を正しく読み取れません。"
            "着順テーブルの列構造を確認してください。"
        )

    if len(horse_numbers) != len(set(horse_numbers)):
        raise ValueError("重複した馬番が検出されました。")

    positions = [
        item["finish_position"] for item in results
    ]

    if any(value is None for value in positions):
        raise ValueError("着順を正しく読み取れません。")

    return results


def parse_race(soup, race_id, result_count):
    """レースの基本情報を取得する。"""
    name = first_text(
        soup,
        [
            ".RaceName",
            ".RaceList_NameBox .RaceName",
        ],
    )

    if not name:
        title = text(soup.select_one("title"))
        if title:
            name = re.split(r"\s*[｜|]\s*", title)[0].strip()

    if not name:
        raise ValueError("レース名を取得できません。")

    race_data_text = [
        text(element)
        for element in soup.select(
            ".RaceData01, .RaceData02"
        )
        if text(element)
    ]

    combined = " ".join(race_data_text)

    date_match = re.search(
        r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日",
        combined,
    )

    date_value = None

    if date_match:
        date_value = (
            f"{int(date_match.group(1)):04d}-"
            f"{int(date_match.group(2)):02d}-"
            f"{int(date_match.group(3)):02d}"
        )

    distance_match = re.search(
        r"(芝|ダート?|障害)\s*[,：:]?\s*(\d{3,4})\s*m",
        combined,
        re.I,
    )

    surface = None
    distance = None

    if distance_match:
        surface = distance_match.group(1)
        distance = int(distance_match.group(2))

    race_number = integer(race_id[-2:])

    if race_number is None or not 1 <= race_number <= 12:
        raise ValueError(
            f"レース番号を判定できません: {race_id}"
        )

    venue_match = re.search(
        r"\d+回\s*([^\s]+)\s*\d+日目",
        combined,
    )
    venue = venue_match.group(1) if venue_match else None

    start_time_match = re.search(
        r"(\d{1,2}:\d{2})発走",
        combined,
    )
    start_time = (
        start_time_match.group(1)
        if start_time_match
        else None
    )

    grade_match = re.search(
        r"\b(GI{1,3}|G[123]|Jpn[123])\b",
        name,
        re.I,
    )
    grade = grade_match.group(1) if grade_match else None

    weather_match = re.search(
        r"天候\s*[:：]\s*([^\s/]+)",
        combined,
    )
    track_match = re.search(
        r"馬場\s*[:：]\s*([^\s/]+)",
        combined,
    )

    direction = None
    layout = None

    course_match = re.search(
        r"\((右|左|直線)\s*([^)]*)\)",
        combined,
    )

    if course_match:
        direction = course_match.group(1)
        layout = course_match.group(2).strip() or None

    return {
        "race_id": race_id,
        "name": name,
        "date": date_value,
        "venue": venue,
        "race_number": race_number,
        "start_time": start_time,
        "grade": grade,
        "surface": surface,
        "distance_m": distance,
        "course_direction": direction,
        "course_layout": layout,
        "weather": (
            weather_match.group(1)
            if weather_match
            else None
        ),
        "track_condition": (
            track_match.group(1)
            if track_match
            else None
        ),
        "number_of_runners": result_count,
        "race_data_text": race_data_text,
    }


def parse_payouts(soup):
    """払戻テーブルから馬券種ごとの情報を取得する。"""
    payouts = []
    seen = set()

    for table in soup.select("table"):
        for row in table.select("tr"):
            cells = [
                text(cell)
                for cell in row.find_all(["th", "td"])
            ]

            if not cells:
                continue

            label = normalize_label(cells[0])

            # 「単勝オッズ」などの着順表ヘッダーを除外する
            if label not in PAYOUT_TYPES:
                continue

            raw_text = " ".join(
                value for value in cells if value
            )

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

    # 必要な券種がどこまで取得できたかをログに出す。
    found_types = {
        normalize_label(item["raw_cells"][0])
        for item in payouts
        if item["raw_cells"]
    }

    missing = PAYOUT_TYPES - found_types

    if missing:
        print(
            "WARNING: 一部の払戻券種が見つかりません: "
            + ", ".join(sorted(missing)),
            file=sys.stderr,
        )

    return payouts


def parse_corner_orders(soup):
    """コーナー通過順位を取得する。"""
    page_text = soup.get_text("\n", strip=True)

    # 説明文を含めず、通過順位の見出しから説明見出しの直前までを抽出
    match = re.search(
        r"コーナー通過順位\s*"
        r"(.*?)"
        r"コーナー通過順位の見方",
        page_text,
        re.S,
    )

    if match:
        raw = re.sub(
            r"\s+",
            " ",
            match.group(1),
        ).strip()

        # 実際の1～4コーナーのデータが含まれる場合だけ採用
        if re.search(
            r"[1１]\s*コーナー",
            raw,
        ) and re.search(
            r"[4４]\s*コーナー",
            raw,
        ):
            return [{"raw_text": raw}]

    # HTML上の要素からのフォールバック
    for element in soup.find_all(True):
        value = text(element)

        if (
            "コーナー通過順位" in value
            and re.search(r"1\s*コーナー", value)
            and re.search(r"4\s*コーナー", value)
            and len(value) < 2000
        ):
            value = re.split(
                r"コーナー通過順位の見方",
                value,
            )[0].strip()

            if value:
                return [{"raw_text": value}]

    raise ValueError(
        "コーナー通過順位を取得できません。"
        "既存JSONは置き換えません。"
    )


def parse_laps(soup, expected_finish_time=None):
    """200mごとの区間ラップと累計タイムを分けて取得する。"""
    candidates = []

    for element in soup.find_all(True):
        cls = " ".join(element.get("class", []))
        element_id = element.get("id", "")

        if re.search(
            r"lap|haron|pace|ラップ|ハロン",
            cls + " " + element_id,
            re.I,
        ):
            value = text(element)

            if value:
                candidates.append(value)

    page_text = soup.get_text("\n", strip=True)

    for line in page_text.splitlines():
        if re.search(
            r"ラップ|ハロンタイム|ペース",
            line,
        ):
            candidates.append(line)

    # 距離が取得できない場合は2400mを前提にしない。
    # 既知の距離から200mごとの区間数を算出する。
    expected_sections = None

    if expected_finish_time and expected_finish_time > 0:
        # 走破タイムから距離を推測しない
        pass

    for raw in dict.fromkeys(candidates):
        # 時刻表記（1:00.0）と小数表記（12.6）を抽出する
        tokens = re.findall(
            r"\d+:\d{2}\.\d|\d{1,2}\.\d",
            raw,
        )

        clock_indexes = [
            index
            for index, token in enumerate(tokens)
            if ":" in token
        ]

        if not clock_indexes:
            continue

        # 最後の累計タイムの後ろにある数値を区間ラップとする
        sectional_tokens = tokens[clock_indexes[-1] + 1:]

        if expected_sections is not None:
            if len(sectional_tokens) != expected_sections:
                continue
        elif len(sectional_tokens) < 3:
            continue

        try:
            sectional = [
                float(value)
                for value in sectional_tokens
            ]
        except ValueError:
            continue

        if any(
            value <= 0 or value >= 40
            for value in sectional
        ):
            continue

        cumulative = []
        total = 0.0

        for value in sectional:
            total = round(total + value, 1)
            cumulative.append(total)

        if (
            expected_finish_time is not None
            and abs(cumulative[-1] - expected_finish_time) > 0.2
        ):
            continue

        return {
            "pace": None,
            "sectional_200m": sectional,
            "cumulative_200m": cumulative,
            "last_4f": (
                round(sum(sectional[-4:]), 1)
                if len(sectional) >= 4
                else None
            ),
            "last_3f": (
                round(sum(sectional[-3:]), 1)
                if len(sectional) >= 3
                else None
            ),
            "raw_text": [raw],
        }

    raise ValueError(
        "区間ラップを正しく検出できません。"
        "既存JSONは置き換えません。"
    )


def finish_time_to_seconds(value):
    """2:23.7形式を秒に変換する。"""
    if not value:
        return None

    match = re.fullmatch(
        r"(\d+):(\d{2})(?:\.(\d+))?",
        value.strip(),
    )

    if not match:
        return None

    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = float("0." + match.group(3)) if match.group(3) else 0

    return minutes * 60 + seconds + fraction


def build_payload(race_id, html):
    """取得データを統合し、JSONの内容を作成する。"""
    soup = BeautifulSoup(html, "html.parser")

    results = parse_results(soup)

    race = parse_race(
        soup,
        race_id,
        len(results),
    )

    payouts = parse_payouts(soup)
    corner_orders = parse_corner_orders(soup)

    winner_time = next(
        (
            finish_time_to_seconds(item["finish_time"])
            for item in results
            if item["finish_position"] == 1
        ),
        None,
    )

    lap_times = parse_laps(
        soup,
        expected_finish_time=winner_time,
    )

    url = f"{BASE_URL}?race_id={race_id}"

    return {
        "schema_version": "1.0",
        "race_id": race_id,
        "fetched_at": datetime.now(
            timezone.utc
        ).isoformat(),
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
    """検証成功後だけ一時ファイル経由でJSONを置換する。"""
    errors = validate(payload)

    if errors:
        raise ValueError(
            "保存前の検証に失敗しました:\n- "
            + "\n- ".join(errors)
        )

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = OUTPUT.with_suffix(".json.tmp")

    try:
        with temp_path.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        temp_path.replace(OUTPUT)

    finally:
        if temp_path.exists():
            temp_path.unlink()


def main():
    parser = argparse.ArgumentParser(
        description="Netkeibaのレース結果をJSONに保存する"
    )

    parser.add_argument(
        "--race-id",
        default="202505021211",
        help="Netkeibaの12桁のrace_id",
    )

    args = parser.parse_args()
    race_id = args.race_id.strip()

    if not re.fullmatch(r"\d{12}", race_id):
        print(
            "ERROR: race-idは12桁の数字で指定してください。",
            file=sys.stderr,
        )
        return 1

    try:
        print(f"レース結果を取得中: {race_id}")

        html = fetch_page(race_id)
        payload = build_payload(race_id, html)

        save_atomically(payload)

        print("取得・検証・保存に成功しました。")
        print(f"レース名: {payload['race']['name']}")
        print(f"出走馬: {len(payload['results'])}")
        print(f"払戻行数: {len(payload['payouts'])}")
        print(
            "コーナー情報数: "
            f"{len(payload['corner_orders'])}"
        )
        print(
            "ラップ区間数: "
            f"{len(payload['lap_times']['sectional_200m'])}"
        )
        print(f"保存先: {OUTPUT}")

        # 取得できていない項目を確認しやすくする
        missing = []

        for item in payload["results"]:
            for key in (
                "frame_number",
                "sex",
                "age",
                "carried_weight_kg",
                "last_3f_seconds",
                "win_odds",
            ):
                if item[key] is None:
                    missing.append(
                        f"馬番{item['horse_number']}:{key}"
                    )

        if missing:
            print(
                "WARNING: 一部の馬データが未取得です:"
            )
            for item in missing[:30]:
                print(f"  - {item}")

        return 0

    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        print(
            "既存JSONは置き換えていません。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
