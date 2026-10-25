
#!/usr/bin/env python3
"""出馬表JSONと過去成績JSONを統合してWeb表示用JSONを生成する。"""

import json
import tempfile
import os
from pathlib import Path

from keiba_common import now_iso


SHUTUBA_PATH = Path("data/shutuba.json")
HISTORY_PATH = Path("data/horse_history.json")
OUTPUT_PATH = Path("data/raw_shutuba_with_history.json")


def read_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def main():
    shutuba = read_json(SHUTUBA_PATH)
    history = read_json(HISTORY_PATH)

    shutuba_races = shutuba.get("races", {})
    history_races = history.get("races", {})
    output_races = {}

    for race_id, race in shutuba_races.items():
        if not isinstance(race, dict):
            continue

        history_race = history_races.get(race_id, {})
        history_horses = history_race.get("horses", {})
        horses = []

        for original in race.get("horses", []):
            horse = dict(original)
            horse_id = str(horse.get("horse_id") or "")
            entry = history_horses.get(horse_id, {})

            horse["past_results"] = entry.get("past_results", [])
            horse["history_status"] = entry.get(
                "history_status",
                "not_fetched",
            )
            horse["history_fetched_at"] = entry.get(
                "history_fetched_at"
            )

            horses.append(horse)

        merged = dict(race)
        merged["horses"] = horses
        output_races[race_id] = merged

    output = {
        "schema_version": 1,
        "updated_at": now_iso(),
        "races": output_races,
    }

    write_json(OUTPUT_PATH, output)

    total = sum(len(r.get("horses", [])) for r in output_races.values())
    with_history = sum(
        bool(h.get("past_results"))
        for r in output_races.values()
        for h in r.get("horses", [])
    )

    print(f"生成完了: {OUTPUT_PATH}")
    print(f"レース数: {len(output_races)}")
    print(f"馬数: {total}")
    print(f"過去成績あり: {with_history}頭")


if __name__ == "__main__":
    main()
