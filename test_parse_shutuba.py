
import unittest

from fetch_shutuba_with_history import (
    Fetcher,
    SHUTUBA_URL,
    parse_shutuba,
)


class TestParseShutuba(unittest.TestCase):
    def test_daihonten_shutuba_has_18_horses(self):
        race_id = "202505021211"
        fetcher = Fetcher(user_agent="ShutubaParserTest/1.0", sleep=0)
        html = fetcher.get(SHUTUBA_URL.format(race_id=race_id))

        race, horses = parse_shutuba(html, race_id)

        self.assertEqual(race["race_id"], race_id)
        self.assertEqual(len(horses), 18)

        horse_ids = [horse.get("horse_id") for horse in horses]
        self.assertTrue(all(horse_ids))
        self.assertEqual(len(set(horse_ids)), 18)


    def test_parse_history_returns_none_without_table(self):
        from fetch_shutuba_with_history import parse_history

        html = "<html><body><p>戦績データなし</p></body></html>"
        result = parse_history(html)

        self.assertIsNone(result)

    def test_parse_history_parses_valid_table(self):
        from fetch_shutuba_with_history import parse_history

        html = """
        <html><body>
        <table class="db_h_race_results">
          <thead>
            <tr>
              <th>日付</th>
              <th>開催</th>
              <th>レース名</th>
              <th>頭数</th>
              <th>枠番</th>
              <th>馬番</th>
              <th>オッズ</th>
              <th>人気</th>
              <th>着順</th>
              <th>騎手</th>
              <th>斤量</th>
              <th>距離</th>
              <th>馬場</th>
              <th>タイム</th>
              <th>着差</th>
              <th>通過</th>
              <th>上り</th>
              <th>馬体重</th>
              <th>天気</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>2025/05/04</td>
              <td>東京</td>
              <td><a href="/race/202505020511/">テストレース</a></td>
              <td>16</td>
              <td>3</td>
              <td>5</td>
              <td>4.2</td>
              <td>2</td>
              <td>1</td>
              <td>テスト騎手</td>
              <td>57.0</td>
              <td>芝1600</td>
              <td>良</td>
              <td>1:32.5</td>
              <td>-0.2</td>
              <td>3-3</td>
              <td>34.1</td>
              <td>480(+2)</td>
              <td>晴</td>
            </tr>
          </tbody>
        </table>
        </body></html>
        """

        result = parse_history(html)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], "2025-05-04")
        self.assertEqual(result[0]["race_id"], "202505020511")
        self.assertEqual(result[0]["finish_pos"], 1)
        self.assertEqual(result[0]["surface"], "芝")
        self.assertEqual(result[0]["distance_m"], 1600)
        self.assertEqual(result[0]["time_sec"], 92.5)
        self.assertEqual(result[0]["body_weight"], 480)
        self.assertEqual(result[0]["body_weight_diff"], 2)


    def test_select_history_excludes_target_and_future_races(self):
        from fetch_shutuba_with_history import select_history

        rows = [
            {"race_id": "OLD001", "date": "2025-05-01"},
            {"race_id": "TARGET01", "date": "2025-05-10"},
            {"race_id": "FUTURE01", "date": "2025-05-15"},
            {"race_id": "OLD002", "date": "2025-04-20"},
        ]

        result = select_history(
            rows=rows,
            race_id="TARGET01",
            race_date="2025-05-10",
            n=3,
        )

        self.assertEqual(
            [row["race_id"] for row in result],
            ["OLD001", "OLD002"],
        )

    def test_select_history_limits_number_of_results(self):
        from fetch_shutuba_with_history import select_history

        rows = [
            {"race_id": "RACE001", "date": "2025-05-01"},
            {"race_id": "RACE002", "date": "2025-04-20"},
            {"race_id": "RACE003", "date": "2025-04-10"},
        ]

        result = select_history(
            rows=rows,
            race_id="TARGET01",
            race_date="2025-05-10",
            n=2,
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(
            [row["race_id"] for row in result],
            ["RACE001", "RACE002"],
        )


if __name__ == "__main__":
    unittest.main()
