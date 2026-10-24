# validate_results.py
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

INPUT = Path("data/raw_race_results.json")

TOP_LEVEL_KEYS = {
    "schema_version",
    "race_id",
    "fetched_at",
    "race",
    "results",
    "payouts",
    "corner_orders",
    "lap_times",
    "sources",
}

RACE_KEYS = {
    "race_id",
    "name",
    "date",
    "venue",
    "race_number",
    "start_time",
    "grade",
    "surface",
    "distance_m",
    "course_direction",
    "course_layout",
    "weather",
    "track_condition",
    "number_of_runners",
    "race_data_text",
}

RESULT_KEYS = {
    "finish_position",
    "frame_number",
    "horse_number",
    "horse_name",
    "horse_id",
    "sex",
    "age",
    "carried_weight_kg",
    "jockey",
    "finish_time",
    "margin",
    "popularity",
    "win_odds",
    "last_3f_seconds",
    "passing_order",
    "trainer",
    "body_weight_kg",
    "body_weight_change_kg",
}

LAP_KEYS = {
    "pace",
    "sectional_200m",
    "cumulative_200m",
    "last_4f",
    "last_3f",
    "raw_text",
}


class ValidationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def is_number(value: Any) -> bool:
    # bool は int の派生型なので明示的に除外する
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(data: Any) -> list[str]:
    errors: list[str] = []

    try:
        require(isinstance(data, dict), "トップレベルはJSONオブジェクトである必要があります")

        missing = TOP_LEVEL_KEYS - data.keys()
        require(not missing, f"トップレベルの必須キー不足: {sorted(missing)}")

        require(data["schema_version"] == "1.0", "schema_version は '1.0' が必要です")
        require(
            isinstance(data["race_id"], str)
            and re.fullmatch(r"\d{12}", data["race_id"]) is not None,
            "race_id は12桁の数字である必要があります",
        )

        require(isinstance(data["fetched_at"], str), "fetched_at は文字列が必要です")
        try:
            datetime.fromisoformat(data["fetched_at"].replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError("fetched_at がISO 8601形式ではありません")

        race = data["race"]
        require(isinstance(race, dict), "race はオブジェクトが必要です")
        missing = RACE_KEYS - race.keys()
        require(not missing, f"race の必須キー不足: {sorted(missing)}")
        require(
            race["race_id"] == data["race_id"],
            "race.race_id とトップレベルの race_id が一致しません",
        )
        require(
            isinstance(race["name"], str) and bool(race["name"].strip()),
            "race.name は空でない文字列が必要です",
        )
        require(
            isinstance(race["race_number"], int)
            and not isinstance(race["race_number"], bool)
            and 1 <= race["race_number"] <= 12,
            "race.race_number は1〜12の整数が必要です",
        )
        require(
            race["distance_m"] is None
            or (
                isinstance(race["distance_m"], int)
                and not isinstance(race["distance_m"], bool)
                and race["distance_m"] > 0
            ),
            "race.distance_m は正の整数または null が必要です",
        )
        require(
            race["number_of_runners"] is None
            or (
                isinstance(race["number_of_runners"], int)
                and not isinstance(race["number_of_runners"], bool)
                and race["number_of_runners"] >= 1
            ),
            "race.number_of_runners は正の整数または null が必要です",
        )
        require(
            isinstance(race["race_data_text"], list)
            and all(isinstance(x, str) for x in race["race_data_text"]),
            "race.race_data_text は文字列の配列が必要です",
        )

        results = data["results"]
        require(isinstance(results, list) and len(results) > 0, "results が空です")

        horse_numbers = []
        for i, horse in enumerate(results):
            require(isinstance(horse, dict), f"results[{i}] はオブジェクトが必要です")
            missing = RESULT_KEYS - horse.keys()
            require(not missing, f"results[{i}] のキー不足: {sorted(missing)}")
            require(
                isinstance(horse["horse_name"], str) and bool(horse["horse_name"].strip()),
                f"results[{i}].horse_name が空です",
            )

            number = horse["horse_number"]
            require(
                isinstance(number, int)
                and not isinstance(number, bool)
                and 1 <= number <= 18,
                f"results[{i}].horse_number は1〜18の整数が必要です",
            )
            horse_numbers.append(number)

            for key in (
                "finish_position",
                "frame_number",
                "age",
                "popularity",
                "body_weight_kg",
                "body_weight_change_kg",
            ):
                value = horse[key]
                require(
                    value is None or (
                        isinstance(value, int) and not isinstance(value, bool)
                    ),
                    f"results[{i}].{key} は整数または null が必要です",
                )

            for key in (
                "carried_weight_kg",
                "win_odds",
                "last_3f_seconds",
            ):
                value = horse[key]
                require(
                    value is None or is_number(value),
                    f"results[{i}].{key} は数値または null が必要です",
                )

            for key in (
                "horse_id", "sex", "jockey", "finish_time", "margin",
                "passing_order", "trainer",
            ):
                value = horse[key]
                require(
                    value is None or isinstance(value, str),
                    f"results[{i}].{key} は文字列または null が必要です",
                )

        require(
            len(horse_numbers) == len(set(horse_numbers)),
            "results に重複した horse_number があります",
        )

        require(
            race["number_of_runners"] == len(results),
            "number_of_runners と results の件数が一致しません",
        )

        payouts = data["payouts"]
        require(isinstance(payouts, list) and len(payouts) > 0, "payouts が空です")
        for i, item in enumerate(payouts):
            require(isinstance(item, dict), f"payouts[{i}] はオブジェクトが必要です")
            require(
                isinstance(item.get("raw_cells"), list)
                and all(isinstance(x, str) for x in item["raw_cells"]),
                f"payouts[{i}].raw_cells は文字列の配列が必要です",
            )
            require(
                isinstance(item.get("raw_text"), str) and item["raw_text"],
                f"payouts[{i}].raw_text が空です",
            )

        corners = data["corner_orders"]
        require(isinstance(corners, list) and len(corners) > 0, "corner_orders が空です")
        for i, item in enumerate(corners):
            require(
                isinstance(item, dict)
                and isinstance(item.get("raw_text"), str)
                and bool(item["raw_text"]),
                f"corner_orders[{i}].raw_text がありません",
            )

        laps = data["lap_times"]
        require(isinstance(laps, dict), "lap_times はオブジェクトが必要です")
        missing = LAP_KEYS - laps.keys()
        require(not missing, f"lap_times のキー不足: {sorted(missing)}")

        for key in ("sectional_200m", "cumulative_200m"):
            values = laps[key]
            require(isinstance(values, list), f"lap_times.{key} は配列が必要です")
            require(
                all(is_number(x) for x in values),
                f"lap_times.{key} に数値以外が含まれています",
            )

        require(
            len(laps["sectional_200m"]) > 0,
            "lap_times.sectional_200m が空です",
        )
        require(
            isinstance(laps["raw_text"], list)
            and all(isinstance(x, str) for x in laps["raw_text"]),
            "lap_times.raw_text は文字列の配列が必要です",
        )

        sources = data["sources"]
        require(isinstance(sources, dict), "sources はオブジェクトが必要です")
        for key in (
            "race_info", "results", "payouts", "corner_orders", "lap_times"
        ):
            require(
                isinstance(sources.get(key), str) and bool(sources[key]),
                f"sources.{key} がありません",
            )

    except ValidationError as exc:
        errors.append(str(exc))

    return errors


def main() -> int:
    if not INPUT.exists():
        print(f"ERROR: ファイルがありません: {INPUT}", file=sys.stderr)
        return 1

    try:
        with INPUT.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: JSONを読み込めません: {exc}", file=sys.stderr)
        return 1

    errors = validate(data)
    if errors:
        print("VALIDATION FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"VALIDATION OK: {INPUT}")
    print(f"Race ID: {data['race_id']}")
    print(f"出走馬データ件数: {len(data['results'])}")
    print(f"払戻行数: {len(data['payouts'])}")
    print(f"コーナー情報数: {len(data['corner_orders'])}")
    print(f"ラップ区間数: {len(data['lap_times']['sectional_200m'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
