
from __future__ import annotations

import logging
import time
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests

from keiba_common import decode_html

log = logging.getLogger("shutuba")

SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
HORSE_URL = "https://db.netkeiba.com/horse/{horse_id}/"

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; keiba-pwa-personal/0.1; "
    "+contact:coelacanth1014@gmail.com)"
)

MAX_REDIRECTS = 5


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
            return self.s.get(
                url,
                timeout=self.timeout,
                allow_redirects=False,
            )
        finally:
            self._last = time.monotonic()
            self.requests_made += 1

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(url)

        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise FetchError(f"許可できないURLです: {url}")

        return url

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True

        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise FetchError(f"確認できないURLです: {url}")

        host = parsed.netloc
        rp = self._robots.get(host)

        if rp is None:
            rp = robotparser.RobotFileParser()
            robots_url = f"https://{host}/robots.txt"

            try:
                response = self._raw_get(robots_url)

                if response.status_code == 404:
                    log.warning(
                        "robots.txt が見つかりません (404): %s",
                        host,
                    )
                    rp.parse([])

                elif response.status_code != 200:
                    raise FetchError(
                        "robots.txt を確認できません: "
                        f"HTTP {response.status_code} ({host})"
                    )

                else:
                    rp.parse(response.text.splitlines())

            except requests.RequestException as exc:
                raise FetchError(
                    f"robots.txt の取得に失敗したため中止: {exc}"
                ) from exc

            self._robots[host] = rp

        return rp.can_fetch(self.ua, url)

    def get(self, url: str) -> str:
        current_url = self._validate_url(url)
        visited = set()
        rate_limited = 0

        for attempt in range(1, self.retries + 1):
            try:
                # リトライのたびに、開始URLから確認し直す。
                current_url = self._validate_url(url)
                visited.clear()
                redirects = 0

                while True:
                    if current_url in visited:
                        raise FetchError(
                            f"リダイレクトループを検出しました: "
                            f"{current_url}"
                        )
                    visited.add(current_url)

                    if not self._allowed(current_url):
                        raise FetchError(
                            "robots.txt により取得不可: "
                            f"{current_url}"
                        )

                    response = self._raw_get(current_url)
                    code = response.status_code

                    if code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise FetchError(
                                "転送先が指定されていません: "
                                f"{current_url}"
                            )

                        if redirects >= MAX_REDIRECTS:
                            raise FetchError(
                                "リダイレクト回数が上限を超えました: "
                                f"{current_url}"
                            )

                        next_url = urljoin(current_url, location)
                        current_url = self._validate_url(next_url)
                        redirects += 1
                        continue

                    if code == 200:
                        return decode_html(response.content)

                    if code == 403:
                        raise BlockedError(
                            f"HTTP 403: アクセス拒否: {current_url}"
                        )

                    if code == 429:
                        rate_limited += 1
                        if rate_limited >= 2:
                            raise BlockedError(
                                "HTTP 429: 制限が続いています: "
                                f"{current_url}"
                            )

                        retry_after = response.headers.get(
                            "Retry-After", ""
                        )
                        wait_seconds = (
                            int(retry_after)
                            if retry_after.isdigit()
                            else 60
                        )
                        log.warning(
                            "HTTP 429: %s秒待機します",
                            wait_seconds,
                        )
                        time.sleep(wait_seconds)
                        break

                    if code == 404:
                        raise FetchError(
                            "HTTP 404: ページが見つかりません: "
                            f"{current_url}"
                        )

                    log.warning(
                        "HTTP %s (%s/%s): %s",
                        code,
                        attempt,
                        self.retries,
                        current_url,
                    )
                    break

            except (BlockedError, FetchError):
                raise

            except requests.RequestException as exc:
                log.warning(
                    "通信エラー (%s/%s): %s",
                    attempt,
                    self.retries,
                    exc,
                )

            if attempt < self.retries:
                time.sleep(5 * attempt)

        raise FetchError(f"取得に失敗しました: {url}")
