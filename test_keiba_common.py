
import unittest

from keiba_common import (
    clean,
    decode_html,
    nfkc,
    to_float,
    to_int,
)


class TestKeibaCommon(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(clean("  hello \n world  "), "hello world")
        self.assertIsNone(clean("   "))

    def test_nfkc(self):
        self.assertEqual(nfkc("１２３"), "123")

    def test_to_int(self):
        self.assertEqual(to_int("１２"), 12)
        self.assertIsNone(to_int("12.5"))

    def test_to_float(self):
        self.assertEqual(to_float("３.５"), 3.5)
        self.assertIsNone(to_float("abc"))

    def test_decode_html(self):
        self.assertEqual(decode_html("テスト".encode("utf-8")), "テスト")
        self.assertEqual(decode_html("テスト".encode("euc_jp")), "テスト")


if __name__ == "__main__":
    unittest.main()
