"""The one place outbound HTTP clients are made, so tests can swap in a mock transport."""

from collections.abc import Iterator

import httpx

USER_AGENT = "ai-research-assistant/0.1 (https://github.com/andres9403/ai-research-assistant)"


def make_client(timeout: float = 10.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def get_http_client() -> Iterator[httpx.Client]:
    with make_client() as client:
        yield client
