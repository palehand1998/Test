
import unittest

from fetch_shutuba import parse_shutuba
from keiba_http import ParseError

class TestFetchShutuba(unittest.TestCase):
    def test_parse_entry_table_without_history(self):
        html = """
        <html>
          <head><title>テストレース</title></head>
          <body>
            <div class="RaceData01">
              15:45発走 / 芝1600m (左) / 天候:晴 / 馬場:良
            </div>
            <div class="RaceData02">
              3回 東京 2日目 1頭
            </div>
            <h1 class="RaceName">テストレース</h1>
            <table class="Shutuba_Table">
              <tbody>
                <tr class="HorseList">
                  <td class="Waku">1</td>
                  <td class="Umaban">1</td>
                  <td class="HorseInfo">
                    <a href="/horse/2021104321/">テストホース</a>
                  </td>
                  <td class="Barei">牡4</td>
                  <td class="WeightCarried">58.0</td>
                  <td class="Jockey">
                    <a href="/jockey/01234/">テスト騎手</a>
                  </td>
                  <td class="Trainer">
                    <a href="/trainer/123/">テスト調教師</a>
                  </td>
                  <td class="Weight">482 (+2)</td>
                  <td class="Popular">3.4</td>
                  <td class="Popular_Ninki">1</td>
                </tr>
              </tbody>
            </table>
          </body>
        </html>
        """

        race, horses = parse_shutuba(html, "202505010101")

        self.assertEqual(race["race_id"], "202505010101")
        self.assertEqual(len(horses), 1)
        self.assertEqual(horses[0]["horse_name"], "テストホース")
        self.assertNotIn("past_results", horses[0])

    def test_missing_entry_table_raises_error(self):
        html = "<html><body><h1>テスト</h1></body></html>"

        with self.assertRaises(ParseError):
            parse_shutuba(html, "202505010101")


if __name__ == "__main__":
    unittest.main()
