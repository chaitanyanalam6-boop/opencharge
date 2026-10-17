import httpx
import pytest

from opencharge.ingest.polite import PoliteClient, build_http_client, user_agent


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_polite(handler, clock: FakeClock | None = None) -> PoliteClient:  # type: ignore[no-untyped-def]
    clock = clock or FakeClock()
    client = build_http_client("test@example.org", transport=httpx.MockTransport(handler))
    return PoliteClient(client, min_interval=2.0, clock=clock, sleep=clock.sleep)


def test_user_agent_carries_contact_address() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, text="ok")

    with make_polite(handler).stream("https://h.example/file.csv") as response:
        response.read()
    assert seen == [user_agent("test@example.org")]
    assert "test@example.org" in seen[0]


def test_client_refuses_to_start_without_contact_address() -> None:
    with pytest.raises(ValueError, match="contact"):
        build_http_client("")


def test_waits_between_requests_to_the_same_host() -> None:
    clock = FakeClock()
    polite = make_polite(lambda r: httpx.Response(200, text="ok"), clock)
    for _ in range(3):
        with polite.stream("https://h.example/a.csv") as r:
            r.read()
    assert clock.sleeps == [2.0, 2.0]


def test_different_hosts_do_not_wait_for_each_other() -> None:
    clock = FakeClock()
    polite = make_polite(lambda r: httpx.Response(200, text="ok"), clock)
    for host in ("a.example", "b.example"):
        with polite.stream(f"https://{host}/f.csv") as r:
            r.read()
    assert clock.sleeps == []


def robots_handler(status: int, body: str = ""):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(status, text=body)
        return httpx.Response(200, text="ok")

    return handler


def test_robots_disallow_is_obeyed() -> None:
    polite = make_polite(robots_handler(200, "User-agent: *\nDisallow: /private/\n"))
    assert polite.allowed("https://h.example/public/file.csv")
    assert not polite.allowed("https://h.example/private/file.csv")


def test_robots_rule_for_our_agent_is_obeyed() -> None:
    polite = make_polite(robots_handler(200, "User-agent: OpenCharge\nDisallow: /\n"))
    assert not polite.allowed("https://h.example/file.csv")


def test_missing_robots_txt_allows_everything() -> None:
    assert make_polite(robots_handler(404)).allowed("https://h.example/file.csv")


def test_robots_server_error_disallows_everything() -> None:
    assert not make_polite(robots_handler(503)).allowed("https://h.example/file.csv")


def test_robots_txt_is_fetched_once_per_host() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404)

    polite = make_polite(handler)
    polite.allowed("https://h.example/a.csv")
    polite.allowed("https://h.example/b.csv")
    assert calls.count("/robots.txt") == 1
