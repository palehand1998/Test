#!/usr/bin/env python3
"""Netkeibaの出馬表HTMLを確認するデバッグ用スクリプト"""

import argparse
import logging

from bs4 import BeautifulSoup

from fetch_shutuba_with_history import (
    Fetcher,
    SHUTUBA_URL,
    parse_shutuba,
)


def main():
    parser = argparse.ArgumentParser(
        description="Netkeibaの出馬表HTMLを確認する"
    )
    parser.add_argument(
        "--race-id",
        default="202606050811",
        help="確認する12桁のレースID",
    )
    args = parser.parse_args()

    if len(args.race_id) != 12 or not args.race_id.isdigit():
        parser.error("race-id は12桁の数字で指定してください")

    logging.basicConfig(level=logging.INFO)

    fetcher = Fetcher(
        user_agent="Mozilla/5.0 (compatible; RaceDataBot/1.0)",
        sleep=2.5,
    )

    url = SHUTUBA_URL.format(race_id=args.race_id)

    print("出馬表を取得しています...", flush=True)
    html = fetcher.get(url)

    # 取得できたHTMLの基本情報
    print("\n=== HTMLの基本情報 ===", flush=True)
    print("URL:", url)
    print("HTML文字数:", len(html))
    print("HTML冒頭:", repr(html[:500]))
    print("HorseList件数:", html.count("HorseList"))

    soup = BeautifulSoup(html, "html.parser")

    print(
        "ページタイトル:",
        soup.title.get_text(strip=True) if soup.title else None,
    )

    # テーブルの構造を調べる
    print("\n=== テーブル一覧 ===", flush=True)

    for table in soup.find_all("table")[:10]:
        print("class:", table.get("class"))

    # 出馬表テーブルを探す
    table = soup.select_one("table.Shutuba_Table")

    if table:
        print("\n=== 出馬表テーブルの先頭部分 ===", flush=True)
        print(table.get_text(" ", strip=True)[:2000])

        print("\n=== 最初の3行のHTML ===", flush=True)

        for tr in table.find_all("tr")[:3]:
            print(tr.prettify()[:3000])
            print("--- 行の区切り ---")
    else:
        print("\nShutuba_Table が見つかりません", flush=True)

    # 馬のリンクを調べる
    print("\n=== 馬名らしいリンク ===", flush=True)

    horse_links = soup.select('a[href*="/horse/"]')

    for a in horse_links[:10]:
        print(
            "名前:", a.get_text(strip=True),
            "| URL:", a.get("href"),
        )

    print("馬リンク件数:", len(horse_links))

    # 既存の解析処理の結果も確認
    print("\n=== 現在の解析処理の結果 ===", flush=True)

    race, horses = parse_shutuba(html, args.race_id)

    print("レースID:", race.get("race_id"))
    print("開催日:", race.get("date"))
    print("レース名:", race.get("name"))
    print("コース:", race.get("surface"), race.get("distance_m"))
    print("取得馬数:", len(horses))

    for horse in horses[:5]:
        print(
            "馬番:", horse.get("horse_no"),
            "| 馬名:", horse.get("horse_name"),
            "| 馬ID:", horse.get("horse_id"),
        )

    if not horses:
        raise RuntimeError(
            "出走馬を取得できませんでした。"
            "上記のHTML構造を確認してください。"
        )


if __name__ == "__main__":
    main()
