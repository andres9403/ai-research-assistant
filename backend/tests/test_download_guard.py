"""PDF links come from untrusted data, so downloads must never reach internal addresses."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpcore
import pytest

from app.services import http
from app.services.http import make_download_client
from app.services.pipeline import DownloadError, fetch_pdf
from tests.test_papers import paper as paper_payload

PUBLIC_IP = "93.184.216.34"
real_getaddrinfo = socket.getaddrinfo


@pytest.fixture()
def local_server():
    """An HTTP server on loopback that records the paths it was asked for."""
    hits: list[str] = []
    redirect_to: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if redirect_to:
                self.send_response(302)
                self.send_header("Location", redirect_to[0])
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"%PDF-1.4 internal data")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.hits, server.redirect_to = hits, redirect_to
    yield server
    server.shutdown()
    server.server_close()


def fake_dns(monkeypatch, table: dict[str, list[str]]):
    def getaddrinfo(host, port, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8123/metadata",
        "http://localhost/a.pdf",
        "http://10.0.0.1/a.pdf",
        "http://172.16.5.4/a.pdf",
        "http://192.168.1.1/a.pdf",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/a.pdf",
        "http://0.0.0.0/a.pdf",
        "http://[::1]/a.pdf",
        "http://[fe80::1]/a.pdf",
        "http://[fd00::1]/a.pdf",
        "http://[::ffff:127.0.0.1]/a.pdf",
    ],
)
def test_non_public_addresses_are_refused(url):
    with make_download_client() as client, pytest.raises(DownloadError, match="private or local"):
        fetch_pdf(client, url)


def test_a_name_resolving_to_any_internal_address_is_refused(monkeypatch):
    fake_dns(monkeypatch, {"mixed.test": [PUBLIC_IP, "10.0.0.5"]})
    with make_download_client() as client, pytest.raises(DownloadError, match="private or local"):
        fetch_pdf(client, "http://mixed.test/a.pdf")


def test_connects_to_the_address_it_checked(monkeypatch):
    """Connecting by IP, not by name, means a second DNS answer can't rebind the host."""
    fake_dns(monkeypatch, {"pub.test": [PUBLIC_IP]})
    dialed = []

    def create_connection(address, *args, **kwargs):
        dialed.append(address)
        raise OSError("no network in tests")

    monkeypatch.setattr(socket, "create_connection", create_connection)
    with make_download_client() as client, pytest.raises(DownloadError, match="ConnectError"):
        fetch_pdf(client, "http://pub.test/a.pdf")
    assert dialed == [(PUBLIC_IP, 80)]


def test_redirects_to_internal_addresses_are_refused(monkeypatch, local_server):
    # pub.test is a public host; route its connections to the local server, which
    # then redirects to the cloud metadata address.
    fake_dns(monkeypatch, {"pub.test": [PUBLIC_IP]})
    local_port = local_server.server_address[1]
    dialed = []
    real_connect = httpcore.SyncBackend.connect_tcp

    def connect_tcp(self, host, port, *args, **kwargs):
        dialed.append(host)
        if host == PUBLIC_IP:
            host, port = "127.0.0.1", local_port
        return real_connect(self, host, port, *args, **kwargs)

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_tcp)
    local_server.redirect_to.append("http://169.254.169.254/latest/meta-data/")

    with make_download_client() as client, pytest.raises(DownloadError, match="private or local"):
        fetch_pdf(client, "http://pub.test/a.pdf")
    assert local_server.hits == ["/a.pdf"]
    assert dialed == [PUBLIC_IP]


def test_saved_paper_with_internal_pdf_link_is_not_fetched(client, monkeypatch, local_server):
    monkeypatch.setattr(http, "make_download_client", make_download_client)
    url = f"http://127.0.0.1:{local_server.server_address[1]}/metadata"
    saved = client.post("/api/papers", json=paper_payload(pdf_url=url)).json()

    paper = client.get(f"/api/papers/{saved['id']}").json()
    assert paper["status"] == "no_pdf"
    assert "private or local" in paper["status_detail"]
    assert local_server.hits == []
