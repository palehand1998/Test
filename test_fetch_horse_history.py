
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


class TestAtomicWriteJson(unittest.TestCase):
    def test_writes_json_and_creates_parent_directory(self):
        import json
        import tempfile
        from pathlib import Path

        from fetch_horse_history import atomic_write_json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "history.json"
            data = {
                "schema_version": 1,
                "races": {
                    "202505021211": {
                        "horses": {
                            "1234567890": {
                                "history_status": "ok",
                                "past_results": [],
                            }
                        }
                    }
                },
            }

            atomic_write_json(path, data, backup=False)

            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded, data)

    def test_preserves_previous_file_as_backup(self):
        import json
        import tempfile
        from pathlib import Path

        from fetch_horse_history import atomic_write_json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text('{"version": 1}\n', encoding="utf-8")

            atomic_write_json(path, {"version": 2})

            backup = path.with_name("history.json.bak")
            self.assertTrue(backup.exists())
            self.assertEqual(
                json.loads(backup.read_text(encoding="utf-8")),
                {"version": 1},
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"version": 2},
            )

class TestLoadShutuba(unittest.TestCase):
    def test_missing_file(self):
        from pathlib import Path
        from fetch_horse_history import load_shutuba

        with self.assertRaises(FileNotFoundError):
            load_shutuba(Path("this_file_does_not_exist.json"))
    def test_invalid_races(self):
        import json
        import tempfile
        from pathlib import Path
        from fetch_horse_history import load_shutuba

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shutuba.json"
            path.write_text(
                json.dumps({"races": []}),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_shutuba(path)

    def test_invalid_json(self):
        import tempfile
        from pathlib import Path
        from fetch_horse_history import load_shutuba

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shutuba.json"
            path.write_text(
                "{invalid json",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_shutuba(path)

    def test_missing_races(self):
        import json
        import tempfile
        from pathlib import Path
        from fetch_horse_history import load_shutuba

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shutuba.json"
            path.write_text(
                json.dumps({"other": "value"}),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_shutuba(path)

    def test_top_level_list_is_rejected(self):
        import json
        import tempfile
        from pathlib import Path
        from fetch_horse_history import load_shutuba

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shutuba.json"
            path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

            with self.assertRaises(ValueError):
                load_shutuba(path)

    def test_top_level_null_is_rejected(self):
        import tempfile
        from pathlib import Path
        from fetch_horse_history import load_shutuba

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shutuba.json"
            path.write_text("null", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_shutuba(path)

    def test_top_level_string_is_rejected(self):
        import tempfile
        from pathlib import Path
        from fetch_horse_history import load_shutuba

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shutuba.json"
            path.write_text('"hello"', encoding="utf-8")

            with self.assertRaises(ValueError):
                load_shutuba(path)

    def test_top_level_number_is_rejected(self):
        import tempfile
        from pathlib import Path
        from fetch_horse_history import load_shutuba

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shutuba.json"
            path.write_text("123", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_shutuba(path)

class TestHorseHistoryStore(unittest.TestCase):
    def test_fresh_cache_is_valid(self):
        import tempfile
        import time
        from pathlib import Path
        from fetch_horse_history import HorseHistoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = HorseHistoryStore(Path(tmp), ttl_hours=168)

            entry = {
                "fetched_epoch": time.time(),
                "rows": [],
                "has_table": True,
            }

            self.assertTrue(store.is_fresh(entry))

    def test_old_cache_is_invalid(self):
        import tempfile
        import time
        from pathlib import Path
        from fetch_horse_history import HorseHistoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = HorseHistoryStore(Path(tmp), ttl_hours=168)

            entry = {
                "fetched_epoch": time.time() - (169 * 3600),
                "rows": [],
                "has_table": True,
            }

            self.assertFalse(store.is_fresh(entry))

    def test_write_and_read_cache(self):
        import tempfile
        from pathlib import Path
        from fetch_horse_history import HorseHistoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = HorseHistoryStore(Path(tmp), ttl_hours=168)
            rows = [
                {"race_id": "202505021210", "date": "2025-05-25"}
            ]

            written = store.write("1234567890", rows)
            loaded = store.read("1234567890")

            self.assertEqual(loaded["rows"], rows)
            self.assertEqual(loaded["has_table"], True)
            self.assertEqual(written["rows"], rows)

    def test_missing_cache_returns_none(self):
        import tempfile
        from pathlib import Path
        from fetch_horse_history import HorseHistoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = HorseHistoryStore(Path(tmp), ttl_hours=168)
            self.assertIsNone(store.read("no_such_horse"))

    def test_corrupt_cache_returns_none(self):
        import tempfile
        from pathlib import Path
        from fetch_horse_history import HorseHistoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = HorseHistoryStore(Path(tmp), ttl_hours=168)
            cache_file = Path(tmp) / "1234567890.json"
            cache_file.write_text(
                "{broken json",
                encoding="utf-8",
            )

            self.assertIsNone(store.read("1234567890"))

class TestExistingOutputValidation(unittest.TestCase):
    def test_invalid_existing_output_is_not_overwritten(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from fetch_horse_history import main

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "shutuba.json"
            output_path = Path(tmp) / "history.json"

            input_path.write_text(
                json.dumps({"races": {}}),
                encoding="utf-8",
            )
            original = '{"races": []}\n'
            output_path.write_text(original, encoding="utf-8")

            with patch("fetch_horse_history.Fetcher") as mock_fetcher:
                result = main([
                    "202505021211",
                    "--in", str(input_path),
                    "--out", str(output_path),
                ])

            self.assertEqual(result, 1)
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                original,
            )
            mock_fetcher.assert_not_called()

    def test_invalid_previous_race_is_not_overwritten(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from fetch_horse_history import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "shutuba.json"
            output_path = root / "history.json"

            input_path.write_text(
                json.dumps({
                    "races": {
                        "202505021211": {
                            "date": "2025-05-04",
                            "horses": [],
                        }
                    }
                }),
                encoding="utf-8",
            )
            original = {
                "schema_version": 1,
                "races": {
                    "202505021211": ["不正なデータ"],
                },
            }
            output_path.write_text(
                json.dumps(original),
                encoding="utf-8",
            )

            with patch("fetch_horse_history.Fetcher"):
                result = main([
                    "202505021211",
                    "--in", str(input_path),
                    "--out", str(output_path),
                    "--cache-dir", str(root / "cache"),
                ])

            self.assertEqual(result, 1)
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                original,
            )

    def test_invalid_previous_horses_is_not_overwritten(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from fetch_horse_history import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "shutuba.json"
            output_path = root / "history.json"

            input_path.write_text(
                json.dumps({
                    "races": {
                        "202505021211": {
                            "date": "2025-05-04",
                            "horses": [],
                        }
                    }
                }),
                encoding="utf-8",
            )

            original = {
                "schema_version": 1,
                "races": {
                    "202505021211": {
                        "horses": ["不正なデータ"],
                    }
                },
            }
            output_path.write_text(
                json.dumps(original),
                encoding="utf-8",
            )

            with patch("fetch_horse_history.Fetcher"):
                result = main([
                    "202505021211",
                    "--in", str(input_path),
                    "--out", str(output_path),
                    "--cache-dir", str(root / "cache"),
                ])

            self.assertEqual(result, 1)
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                original,
            )


class TestArgumentValidation(unittest.TestCase):
    def test_zero_cache_ttl_is_rejected(self):
        from unittest.mock import patch
        from fetch_horse_history import main

        with patch(
            "sys.stderr",
        ):
            with self.assertRaises(SystemExit) as ctx:
                main([
                    "202505021211",
                    "--cache-ttl-hours", "0",
                ])

        self.assertEqual(ctx.exception.code, 2)

    def test_negative_cache_ttl_is_rejected(self):
        from unittest.mock import patch
        from fetch_horse_history import main

        with patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                main([
                    "202505021211",
                    "--cache-ttl-hours", "-1",
                ])

        self.assertEqual(ctx.exception.code, 2)

    def test_zero_history_is_rejected(self):
        from unittest.mock import patch
        from fetch_horse_history import main

        with patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                main([
                    "202505021211",
                    "--history", "0",
                ])

        self.assertEqual(ctx.exception.code, 2)

    def test_negative_history_is_rejected(self):
        from unittest.mock import patch
        from fetch_horse_history import main

        with patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                main([
                    "202505021211",
                    "--history", "-1",
                ])

        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_race_id_is_rejected(self):
        from unittest.mock import patch
        from fetch_horse_history import main

        with patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                main(["12345"])

        self.assertEqual(ctx.exception.code, 2)

    def test_non_numeric_race_id_is_rejected(self):
        from unittest.mock import patch
        from fetch_horse_history import main

        with patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                main(["2025AB021211"])

        self.assertEqual(ctx.exception.code, 2)

    def test_thirteen_digit_race_id_is_rejected(self):
        from unittest.mock import patch
        from fetch_horse_history import main

        with patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                main(["2025050212111"])

        self.assertEqual(ctx.exception.code, 2)

    def test_valid_race_id_passes_validation(self):
        from unittest.mock import patch
        from fetch_horse_history import main

        with patch("sys.stderr"):
            result = main([
                "202505021211",
                "--in",
                "file_that_does_not_exist.json",
            ])

        self.assertEqual(result, 1)

if __name__ == "__main__":
    unittest.main()
