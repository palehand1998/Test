
import unittest

from fetch_horse_history import select_history


class TestSelectHistory(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"race_id": "202505021211", "date": "2025-06-01"},
            {"race_id": "202505021210", "date": "2025-05-25"},
            {"race_id": "202505021209", "date": "2025-05-18"},
            {"race_id": "202505021208", "date": "2025-05-11"},
        ]

    def test_excludes_target_race(self):
        result = select_history(
            self.rows,
            "202505021211",
            "2025-06-08",
            3,
        )
        self.assertEqual(
            [r["race_id"] for r in result],
            [
                "202505021210",
                "202505021209",
                "202505021208",
            ],
        )

    def test_excludes_races_on_or_after_race_date(self):
        result = select_history(
            self.rows,
            "202505029999",
            "2025-05-20",
            3,
        )
        self.assertEqual(
            [r["race_id"] for r in result],
            [
                "202505021209",
                "202505021208",
            ],
        )

    def test_limits_number_of_results(self):
        result = select_history(
            self.rows,
            "202505029999",
            None,
            2,
        )
        self.assertEqual(len(result), 2)

    def test_empty_history(self):
        result = select_history(
            [],
            "202505029999",
            "2025-06-08",
            3,
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
