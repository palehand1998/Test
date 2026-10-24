#!/usr/bin/env python3
"""Netkeiba出馬表のHTML構造を調べるデバッグ用スクリプト"""

import argparse
import logging

from bs4 import BeautifulSoup

from fetch_shutuba_with_history import Fetcher, SHUTUBA_URL


def main():
    parser = argparse.ArgumentParser()
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
    soup = BeautifulSoup(html, "html.parser")

    print("\n=== ページ情報 ===")
    print("URL:", url)
    print("HTML文字数:", len(html))
    print(
        "タイトル:",
        soup.title.get_text(strip=True) if soup.title else None,
    )

    table = soup.select_one("table.Shutuba_Table")

    if not table:
        print("Shutuba_Table が見つかりません")
    else:
        rows = table.find_all("tr")
        print("\n=== 出馬表テーブル ===")
        print("行数:", len(rows))

        for i, tr in enumerate(rows[:12]):
            print(f"\n--- 行 {i + 1} ---")
            print("class:", tr.get("class"))
            print("テキスト:", tr.get_text(" | ", strip=True)[:500])

            for a in tr.find_all("a", href=True)[:5]:
                print(
                    "リンク:",
                    repr(a.get_text(strip=True)),
                    a["href"],
                )

        print("\n=== テーブル末尾のHTML ===")
        print(table.prettify()[-5000:])

    print("\n=== HTML内の馬データ関連キーワード ===")
    for keyword in [
        "HorseList",
        "HorseInfo",
        "horse_id",
        "horseId",
        "出走馬",
        "出馬表",
        "RaceTable",
    ]:
        print(f"{keyword}: {html.count(keyword)}件")

    print("\n=== JSONらしいデータの手がかり ===")
    for script in soup.find_all("script"):
        content = script.string or script.get_text()
        if any(
            keyword in content
            for keyword in ["horse_id", "horseId", "HorseList"]
        ):
            print(content[:1500])
            print("---")


if __name__ == "__main__":
    main()
