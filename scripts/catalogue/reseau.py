import os
import urllib.request
from email.utils import parsedate_to_datetime


TIMEOUT_SECONDS = 60


USER_AGENT = "bonk-catalog"


TEXT_LIMIT = 4 << 20


class SourceError(Exception):
    pass


def request(url, method="GET"):
    headers = {"User-Agent": USER_AGENT}
    # Without a token GitHub allows 60 API calls an hour per address, shared by every runner on it.
    token = os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS)


def fetch_bytes(url):
    with request(url) as response:
        return response.read(TEXT_LIMIT)


def fetch_text(url):
    return fetch_bytes(url).decode("utf-8", "replace")


def reachable(url):
    with request(url) as response:
        if not response.geturl().startswith("https://"):
            raise SourceError(f"{url}: redirected out of https")


def head(url):
    """Size and date of a file; the final address must still be https after redirects."""
    with request(url, "HEAD") as response:
        if not response.geturl().startswith("https://"):
            raise SourceError(f"{url}: redirected out of https")
        size = int(response.headers.get("Content-Length") or 0)
        modified = response.headers.get("Last-Modified")
    if size <= 0:
        raise SourceError(f"{url}: no size")
    if not modified:
        raise SourceError(f"{url}: no date")
    return size, parsedate_to_datetime(modified).date().isoformat()
