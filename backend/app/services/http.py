"""The one place outbound HTTP clients are made, so tests can swap in a mock transport."""

import ipaddress
import socket
from collections.abc import Iterator

import httpcore
import httpx

USER_AGENT = "ai-research-assistant/0.1 (https://github.com/andres9403/ai-research-assistant)"


def make_client(timeout: float = 10.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def get_http_client() -> Iterator[httpx.Client]:
    with make_client() as client:
        yield client


class BlockedAddress(Exception):
    """The host resolves to a loopback, private, link-local or otherwise non-public address."""


def public_addresses(host: str, port: int) -> list[str]:
    """Resolve `host` and return its addresses, or raise BlockedAddress if any is not public.

    Refusing the whole host when any address is non-public stops a name that
    resolves to both a public and an internal address from slipping through.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpcore.ConnectError(str(exc)) from exc
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise BlockedAddress(f"{host} resolves to non-public address {ip}")
    return addresses


class PublicNetworkBackend(httpcore.SyncBackend):
    """Connects only to public addresses, and to the exact address it checked.

    Checking at connect time covers every redirect hop, and connecting to the
    validated IP (not the name) means a second DNS answer can't swap in an
    internal address. TLS still verifies the original hostname.
    """

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        last_error: Exception | None = None
        for address in public_addresses(host, port):
            try:
                return super().connect_tcp(address, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        raise last_error or httpcore.ConnectError(f"no addresses for {host}")


class PublicOnlyTransport(httpx.HTTPTransport):
    def __init__(self):
        # Ignore proxy env vars: the guard must see the real destination, not a proxy.
        super().__init__(trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=PublicNetworkBackend(),
        )


def make_download_client(timeout: float = 30.0) -> httpx.Client:
    """A client for URLs that come from untrusted data, such as a paper's PDF link."""
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=5,
        headers={"User-Agent": USER_AGENT},
        transport=PublicOnlyTransport(),
        trust_env=False,
    )
