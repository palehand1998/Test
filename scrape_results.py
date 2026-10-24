import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


OUTPUT_FILE = Path("data/raw_race_results.json")
BASE_URL = "https://race.netkeiba.com/race/result.html"


def clean_text(element):
    if element is None:
        return ""
    return element.get_text(" ", strip=True)


def to_int(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def to_float(value):
    value = (value or "").strip().replace(",", "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_html(race_id):
    url = BASE_URL
    response = requests.get(
        url,
        params={"race_id": race_id},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; RaceResultsResearch/1.0)"
            )
        },
        timeout=30,
    )
    response.raise_for_status()

    # Netkeibaのページによって文字コードが異なる場合に備える
    response.encoding = response.apparent_encoding or "euc-jp"
    html = response.text

    if "All_Result_Table" not in html and "全着順" not in html:
        raise RuntimeError(
            "結果テーブルが見つかりません。"
            "ページ構造、アクセス制限、レースIDを確認してください。"
        )

    return html


def parse_race_info(soup, race_id):
    title = clean_text(soup.select_one(".RaceName"))
    if not title:
        title = clean_text(soup.title)

    info = {
        "race_id": race_id,
        "race_name": title,
        "url": f"{BASE_URL}?race_id={race_id}",
    }

    # レース条件などの情報を可能な範囲で取得
    for selector in (
        ".RaceData01",
        ".RaceData02",
        ".RaceList_NameBox",
    ):
        element = soup.select_one(selector)
        if element:
            info[selector.lstrip(".")] = clean_text(element)

    return info


def parse_horses(soup):
    table = soup.select_one("table#All_Result_Table")
    if table is None:
        # クラス名が変わった場合の予備検索
        table = soup.find(
            "table", attrs={"summary": re.compile("全着順")}
        )

    if table is None:
        raise RuntimeError("全着順テーブルを検出できません。")

    horses = []

    for row in table.select("tr.HorseList"):
        cells = row.find_all("td")
        if not cells:
            continue

        rank_element = row.select_one("td.Result_Num .Rank")
        if rank_element is None:
            rank_element = row.select_one("td.Result_Num")

        frame_cell = row.find(
            "td", class_=re.compile(r"\bWaku\d+\b")
        )
        horse_name_link = row.select_one(
            "td.Horse_Info a[href*='/horse/']"
        )

        # 馬番は通常3番目のセル
        horse_number = clean_text(cells[2]) if len(cells) > 2 else ""

        time_cells = row.select("td.Time")
        finish_time = (
            clean_text(time_cells[0]) if len(time_cells) > 0 else ""
        )
        margin = (
            clean_text(time_cells[1]) if len(time_cells) > 1 else ""
        )
        last_3f = (
            clean_text(time_cells[2]) if len(time_cells) > 2 else ""
        )

        sex_age = clean_text(
            row.select_one("td.Horse_Info.Txt_C .Lgt_Txt")
        )
        carried_weight = clean_text(
            row.select_one("td.Jockey_Info .JockeyWeight")
        )
        jockey = clean_text(row.select_one("td.Jockey a"))
        trainer = clean_text(
            row.select_one("td.Trainer .TrainerNameSpan")
        )

        popularity = clean_text(
            row.select_one("td.Odds.Txt_C .OddsPeople")
        )
        odds = clean_text(
            row.select_one("td.Odds.Txt_R span")
        )
        passage = clean_text(row.select_one("td.PassageRate"))

        weight_text = clean_text(row.select_one("td.Weight"))
        weight_match = re.search(
            r"(\d+)\s*\(([+-]?\d+)\)", weight_text
        )

        horses.append({
            "rank": to_int(clean_text(rank_element)),
            "frame": to_int(clean_text(frame_cell)),
            "horse_number": to_int(horse_number),
            "horse_name": (
                horse_name_link.get("title")
                or clean_text(horse_name_link)
                if horse_name_link else ""
            ),
            "horse_id": (
                re.search(
                    r"/horse/(\d+)",
                    horse_name_link.get("href", "")
                ).group(1)
                if horse_name_link and re.search(
                    r"/horse/(\d+)",
                    horse_name_link.get("href", "")
                )
                else ""
            ),
            "sex_age": sex_age,
            "carried_weight": to_float(carried_weight),
            "jockey": jockey,
            "finish_time": finish_time,
            "margin": margin,
            "popularity": to_int(popularity),
            "win_odds": to_float(odds),
            "last_3f": to_float(last_3f),
            "passage_order": passage,
            "trainer": trainer,
            "body_weight": (
                int(weight_match.group(1)) if weight_match else None
            ),
            "weight_change": (
                int(weight_match.group(2)) if weight_match else None
            ),
        })

    if not horses:
        raise RuntimeError("馬の着順データが0件でした。")

    return horses


def parse_payouts(soup):
    payouts = []

    for table in soup.select("table"):
        rows = table.select("tr")

        for row in rows:
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue

            values = [clean_text(cell) for cell in cells]
            row_text = " ".join(values)

            # 払戻表らしい行のみを対象にする
            if not re.search(
                r"単勝|複勝|枠連|馬連|ワイド|馬単|3連複|3連単|三連複|三連単",
                row_text,
            ):
                continue

            payouts.append({
                "bet_type": values[0],
                "details": values[1:],
            })

    # 重複行を除去
    unique = []
    seen = set()

    for item in payouts:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


def parse_corners(soup):
    corners = []

    # コーナー通過順位の専用エリアがあれば優先
    for element in soup.select(
        ".Corner_Num, .Corner_Num01, .RaceCorner, .Corner"
    ):
        text = clean_text(element)
        if text:
            corners.append(text)

    # 馬ごとのコーナー通過順も保持するため、
    # 結果テーブルの passage_order は horses に含めている
    return corners


def parse_lap_times(soup):
    """
    ラップタイムを複数のHTMLパターンから探す。
    見つからない場合は空リストを返し、取得処理自体は続行する。
    """
    candidates = []

    # 1. ラップ関連のclass/idを持つ要素
    for element in soup.find_all(
        True,
        attrs={
            "class": re.compile(r"lap|haron|pace", re.I)
        },
    ):
        text = clean_text(element)
        if text and re.search(r"\d+\.\d+", text):
            candidates.append(text)

    for element in soup.find_all(
        True,
        id=re.compile(r"lap|haron|pace", re.I),
    ):
        text = clean_text(element)
        if text and re.search(r"\d+\.\d+", text):
            candidates.append(text)

    # 2. テーブル行に「ラップ」などの見出しがある場合
    for row in soup.select("tr"):
        row_text = clean_text(row)
        if re.search(r"ラップ|ハロンタイム", row_text):
            candidates.append(row_text)

    # 重複を除き、数値の並びを抽出
    unique_candidates = list(dict.fromkeys(candidates))

    for text in unique_candidates:
        numbers = re.findall(r"(?<!\d)\d{1,3}\.\d(?!\d)", text)

        # 競走ラップは通常複数区間あるため、複数値を採用
        if len(numbers) >= 3:
            return [
                {
                    "raw": text,
                    "values": [float(value) for value in numbers],
                }
            ]

    return []


def validate_minimum_data(payload):
    if not payload.get("race_id"):
        raise RuntimeError("race_idがありません。")

    if not payload.get("horses"):
        raise RuntimeError("馬の結果データがありません。")

    names = [
        horse.get("horse_name")
        for horse in payload["horses"]
        if horse.get("horse_name")
    ]
    if not names:
        raise RuntimeError(
            "馬名が取得できていません。ページ構造を確認してください。"
        )


def save_json_safely(payload):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    temp_file = OUTPUT_FILE.with_suffix(".json.tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    # 完全に書き終わった後にのみ置換
    temp_file.replace(OUTPUT_FILE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--race-id",
        default="202505021211",
        help="Netkeibaの12桁のレースID",
    )
    args = parser.parse_args()

    race_id = args.race_id.strip()
    if not re.fullmatch(r"\d{12}", race_id):
        print("ERROR: race-idは12桁の数字で指定してください。")
        return 1

    try:
        print(f"レース結果を取得します: {race_id}")
        html = fetch_html(race_id)
        soup = BeautifulSoup(html, "html.parser")

        time.sleep(2)

        horses = parse_horses(soup)
        lap_times = parse_lap_times(soup)

        payload = {
            "race_id": race_id,
            "scraped_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "race_info": parse_race_info(soup, race_id),
            "horses": horses,
            "payouts": parse_payouts(soup),
            "corners": parse_corners(soup),
            "lap_times": lap_times,
        }

        validate_minimum_data(payload)
        save_json_safely(payload)

        print(f"取得成功: 馬 {len(horses)}頭")
        print(f"払戻データ: {len(payload['payouts'])}行")

        if lap_times:
            print("ラップタイム: 検出しました")
        else:
            print(
                "WARNING: ラップタイムは検出できませんでした。"
                "ほかのデータを保存しました。"
            )

        print(f"保存先: {OUTPUT_FILE}")
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}")
        print("既存JSONは置換していません。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
