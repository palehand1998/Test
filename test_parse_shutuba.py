
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


if __name__ == "__main__":
    unittest.main()
