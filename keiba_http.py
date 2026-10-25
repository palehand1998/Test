
from __future__ import annotations

import logging
import time
from urllib import robotparser
from urllib.parse import urlparse

import requests

from keiba_common import decode_html

log = logging.getLogger("shutuba")

SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
HORSE_URL = "https://db.netkeiba.com/horse/{horse_id}/"

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; keiba-pwa-personal/0.1; "
    "+contact:coelacanth1014@gmail.com)"
)


class FetchError(RuntimeError):
    pass


class BlockedError(RuntimeError):
    pass


class ParseError(RuntimeError):
    pass


class Fetcher:
    def __init__(
        self,
        sleep: float,
        user_agent: str,
        timeout: float = 15.0,
        retries: int = 3,
        respect_robots: bool = True,
    ):
        self.sleep = sleep
        self.timeout = timeout
        self.retries = retries
        self.ua = user_agent
        self.respect_robots = respect_robots

        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "ja,en;q=0.7",
        })

        self._last = 0.0
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self.requests_made = 0

    def _wait(self):
        elapsed = time.monotonic() - self._last
        if elapsed < self.sleep:
            time.sleep(self.sleep - elapsed)

    def _raw_get(self, url: str):
        self._wait()
        try:
            return self.s.get(url, timeout=self.timeout)
        finally:
            self._last = time.monotonic()
            self.requests_made += 1

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True

        host = urlparse(url).netloc
        rp = self._robots.get(host)

        if rp is None:
            rp = robotparser.RobotFileParser()
            try:
                response = self._raw_get(f"https://{host}/robots.txt")
                if response.status_code == 200:
                    rp.parse(response.text.splitlines())
                else:
                    rp.allow_all = True
            except requests.RequestException as exc:
                log.warning("robots.txt の取得に失敗: %s", exc)
                rp.allow_all = True

            self._robots[host] = rp

        return rp.can_fetch(self.ua, url)

    def get(self, url: str) -> str:
        if not self._allowed(url):
            raise FetchError(f"robots.txt により取得不可: {url}")

        rate_limited = 0

        for attempt in range(1, self.retries + 1):
            try:
                response = self._raw_get(url)
            except requests.RequestException as exc:
                log.warning("通信エラー (%s/%s): %s", attempt, self.retries, exc)
                if attempt < self.retries:
                    time.sleep(5 * attempt)
                continue

            code = response.status_code

            if code == 200:
                return decode_html(response.content)

            if code == 403:
                raise BlockedError(f"HTTP 403: アクセス拒否: {url}")

            if code == 429:
                rate_limited += 1
                if rate_limited >= 2:
                    raise BlockedError(f"HTTP 429: 制限が続いています: {url}")

                retry_after = response.headers.get("Retry-After", "")
                wait_seconds = int(retry_after) if retry_after.isdigit() else 60
                log.warning("HTTP 429: %s秒待機します", wait_seconds)
                time.sleep(wait_seconds)
                continue

            if code == 404:
                raise FetchError(f"HTTP 404: ページが見つかりません: {url}")

            log.warning("HTTP %s (%s/%s): %s", code, attempt, self.retries, url)
            if attempt < self.retries:
                time.sleep(5 * attempt)

        raise FetchError(f"取得に失敗しました: {url}")

