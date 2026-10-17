"""An HTTP client that behaves well toward hospital web servers (brief §7.6).

- Identifies itself in the User-Agent, with a contact address.
- Obeys robots.txt, read per host and cached for the run.
- Waits a minimum interval between requests to the same host.
"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from opencharge import __version__

ROBOTS_AGENT = "OpenCharge"
DEFAULT_MIN_INTERVAL_SECONDS = 2.0
# Generous read timeout: some hospital servers pause for a long time mid-way through big files.
DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0)


def user_agent(contact_email: str) -> str:
    return f"{ROBOTS_AGENT}/{__version__} (hospital price transparency research; {contact_email})"


def build_http_client(
    contact_email: str, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """The one place an httpx.Client is configured. Tests pass a MockTransport."""
    if not contact_email or "@" not in contact_email:
        raise ValueError(
            "a contact email is required for the User-Agent (OPENCHARGE_CONTACT_EMAIL)"
        )
    return httpx.Client(
        headers={"User-Agent": user_agent(contact_email)},
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT,
        transport=transport,
    )


class PoliteClient:
    def __init__(
        self,
        client: httpx.Client,
        min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}

    def _wait_turn(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last_request_at.get(host)
        if last is not None:
            remaining = self.min_interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at[host] = self._clock()

    def allowed(self, url: str) -> bool:
        """True if robots.txt lets us fetch this URL."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        return self._robots[origin].can_fetch(ROBOTS_AGENT, url)

    def _load_robots(self, origin: str) -> RobotFileParser:
        # RFC 9309 §2.3.1: a 4xx robots.txt means no rules (allow all); a 5xx or an
        # unreachable server means assume everything is disallowed.
        disallow_everything = ["User-agent: *", "Disallow: /"]
        allow_everything: list[str] = []
        robots_url = f"{origin}/robots.txt"
        self._wait_turn(robots_url)
        try:
            response = self.client.get(robots_url)
        except httpx.HTTPError:
            rules = disallow_everything
        else:
            if response.status_code >= 500:
                rules = disallow_everything
            elif response.status_code >= 400:
                rules = allow_everything
            else:
                rules = response.text.splitlines()
        parser = RobotFileParser()
        parser.parse(rules)
        return parser

    @contextmanager
    def stream(self, url: str) -> Iterator[httpx.Response]:
        """Stream a GET response; the body is never read into memory all at once."""
        self._wait_turn(url)
        with self.client.stream("GET", url) as response:
            yield response
