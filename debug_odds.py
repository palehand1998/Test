
from bs4 import BeautifulSoup

from scrape_results import fetch_page, parse_results

race_id = "202505021211"

print("レース結果を取得しています...")
html = fetch_page(race_id)
soup = BeautifulSoup(html, "html.parser")

results = parse_results(soup)

print("\n馬番 | 馬名 | 人気 | 単勝オッズ")
print("-" * 45)

for horse in results:
    print(
        f"{horse['horse_number']} | "
        f"{horse['horse_name']} | "
        f"{horse['popularity']} | "
        f"{horse['win_odds']}"
    )

winner = next(
    (
        horse for horse in results
        if horse["horse_number"] == 13
    ),
    None,
)

if winner:
    print("\n馬番13の確認:")
    print("馬名:", winner["horse_name"])
    print("人気:", winner["popularity"])
    print("単勝オッズ:", winner["win_odds"])
