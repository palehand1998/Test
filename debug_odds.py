
import requests
from bs4 import BeautifulSoup

url = "https://race.netkeiba.com/race/result.html?race_id=202505021211"

response = requests.get(
    url,
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=20,
)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")
rows = soup.select("tr.HorseList")

print("HorseList rows:", len(rows))

if not rows:
    print("結果行が見つかりません。HTMLの一部を確認します:")
    print(soup.get_text(" ", strip=True)[:2000])
else:
    row = rows[0]
    print("First horse row:")
    for i, cell in enumerate(row.find_all("td")):
        classes = " ".join(cell.get("class", []))
        value = cell.get_text(" ", strip=True)
        print(f"{i}: class={classes!r}, text={value!r}")
