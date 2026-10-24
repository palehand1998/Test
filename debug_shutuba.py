
#!/usr/bin/env python3
"""出馬表の取得・解析だけを確認するテスト"""

import argparse
import logging

from fetch_shutuba_with_history import (
    Fetcher,
    SHUTUBA_URL,
    parse_shutuba,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--race-id",
        default="202606050811",
        help="確認する12桁のレースID",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if len(args.race_id) != 12 or not args.race_id.isdigit():
        parser.error("race-id は12桁の数字で指定してください")

    fetcher = Fetcher(user_agent="Mozilla/5.0 (compatible; RaceDataBot/1.0)", sleep=2.5)

    url = SHUTUBA_URL.format(race_id=args.race_id)
    print("出馬表を取得しています...")
    html = fetcher.get(url)

    print("HTML文字数:", len(html))
    print("HTML冒頭:", repr(html[:500]))
    print("HorseList件数:", html.count("HorseList"))

    from bs4 import BeautifulSoup
    
    race, horses = parse_shutuba(html, args.race_id)

    print("\n=== レース情報 ===")
    print("レースID:", race["race_id"])
    print("開催日:", race["date"])
    print("レース名:", race["name"])
    print("コース:", race["surface"], race["distance_m"])
    print("取得馬数:", len(horses))

    print("\n=== 出馬表 ===")
    print("馬番 | 枠番 | 馬名 | 性齢 | 斤量 | 騎手 | オッズ | 人気")

    for horse in horses:
        print(
            f'{horse["horse_no"]} | '
            f'{horse["frame_no"]} | '
            f'{horse["horse_name"]} | '
            f'{horse["sex"]}{horse["age"]} | '
            f'{horse["weight_carried"]} | '
            f'{horse["jockey"]["name"]} | '
            f'{horse["odds"]} | '
            f'{horse["popularity"]}'
        )

    missing_names = sum(
        not horse["horse_name"] for horse in horses
    )
    missing_ids = sum(
        not horse["horse_id"] for horse in horses
    )

    print("\n=== 確認結果 ===")
    print("馬名の欠損:", missing_names)
    print("馬IDの欠損:", missing_ids)

    if not horses:
        raise RuntimeError("出走馬を取得できませんでした")

    if missing_names or missing_ids:
        print("WARNING: 馬名または馬IDの欠損があります")
    else:
        print("馬名・馬IDは全頭取得できています")


if __name__ == "__main__":
    main()
