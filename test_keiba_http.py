
import unittest
from unittest.mock import Mock, patch

import requests

from keiba_http import (
    BlockedError,
    FetchError,
    Fetcher,
)


class TestFetcher(unittest.TestCase):
    @patch.object(Fetcher, "_raw_get")
    def test_robots_non_200_stops_request(self, mock_get):
        mock_get.return_value = Mock(status_code=503)

        fetcher = Fetcher(
            sleep=0,
            user_agent="test-agent",
            respect_robots=True,
        )

        with self.assertRaises(FetchError):
            fetcher.get("https://example.com/test")

        mock_get.assert_called_once_with(
            "https://example.com/robots.txt"
        )
    @patch.object(Fetcher, "_raw_get")
    def test_robots_404_allows_fetch(self, mock_get):
        robots_response = Mock(status_code=404)
        page_response = Mock(
            status_code=200,
            content=b"test html",
        )
        mock_get.side_effect = [
            robots_response,
            page_response,
        ]

        fetcher = Fetcher(
            sleep=0,
            user_agent="test-agent",
            respect_robots=True,
        )

        result = fetcher.get("https://example.com/test")

        self.assertEqual(result, "test html")
        self.assertEqual(mock_get.call_count, 2)
    def make_fetcher(self):
        return Fetcher(
            sleep=0,
            user_agent="test-agent",
            respect_robots=False,
        )

    @patch.object(Fetcher, "_raw_get")
    def test_get_success(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.content = b"test html"
        mock_get.return_value = response

        fetcher = self.make_fetcher()
        result = fetcher.get("https://example.com/test")

        self.assertEqual(result, "test html")
        mock_get.assert_called_once()

    @patch.object(Fetcher, "_raw_get")
    def test_get_403_raises_blocked_error(self, mock_get):
        response = Mock()
        response.status_code = 403
        mock_get.return_value = response

        fetcher = self.make_fetcher()

        with self.assertRaises(BlockedError):
            fetcher.get("https://example.com/test")

    @patch.object(Fetcher, "_raw_get")
    def test_get_404_raises_fetch_error(self, mock_get):
        response = Mock()
        response.status_code = 404
        mock_get.return_value = response

        fetcher = self.make_fetcher()

        with self.assertRaises(FetchError):
            fetcher.get("https://example.com/test")

    @patch.object(Fetcher, "_raw_get")
    def test_network_error_retries(self, mock_get):
        mock_get.side_effect = [
            requests.ConnectionError("test error"),
            Mock(status_code=200, content=b"ok"),
        ]

        fetcher = Fetcher(
            sleep=0,
            user_agent="test-agent",
            retries=2,
            respect_robots=False,
        )

        with patch("keiba_http.time.sleep"):
            result = fetcher.get("https://example.com/test")

        self.assertEqual(result, "ok")
        self.assertEqual(mock_get.call_count, 2)

if __name__ == "__main__":
    unittest.main()
