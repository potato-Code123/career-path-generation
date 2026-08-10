"""Polite, cached fetcher for the CMU course catalog (CourseLeaf).

Crawl frontier comes from the catalog's own sitemap.xml, which robots.txt
points at. We never follow in-page links, so we can't wander into the
disallowed paths (/search/, /ribbit/, /dataFile/, /previous/, ...).
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CATALOG = "http://coursecatalog.web.cmu.edu"
SITEMAP = f"{CATALOG}/sitemap.xml"

# Schedule of Classes. Public, unauthenticated, and the host serves no
# robots.txt (404 as of this writing).
SOC = "https://enr-apps.as.cmu.edu/open/SOC/SOCServlet"
SOC_SEARCH = f"{SOC}/search"
SOC_DETAILS = f"{SOC}/courseDetails"

# Offered by the search form. Only these five are available; the archive is shallow.
SEMESTERS = ["F26", "M26", "S26", "F25", "M25"]

CACHE_DIR = Path(__file__).parent / "cache"

# Identify ourselves with a working contact address. Override if someone else
# runs this; a real address is what lets CMU tell us to stop.
CONTACT = os.environ.get("CATALOG_CONTACT", "tbess@andrew.cmu.edu")
USER_AGENT = f"cmu-catalog/0.1 (student project; {CONTACT})"

DELAY_SECONDS = 1.0

# www.cs.cmu.edu's robots.txt sets "Crawl-delay: 10" for User-agent: *.
# Honour it rather than applying the global 1/sec everywhere.
HOST_DELAYS = {"www.cs.cmu.edu": 10.0}

# Paths robots.txt disallows. We shouldn't hit these given we only use the
# sitemap, but assert it rather than assume it.
DISALLOWED = (
    "/previous/", "/admin/", "/pagewiz/", "/courseleaf/", "/wiztest/",
    "/navbar/", "/gallery/", "/clmail/", "/dbleaf/", "/depts/",
    "/responseform/", "/mig/", "/tmp/", "/ribbit/", "/azindex/",
    "/catalogcontents/", "/shared/", "/cim/", "/courseadmin/",
    "/programadmin/", "/js/", "/images/", "/css/", "/styles/",
    "/search/", "/xsearch/", "/dataFile/", "/pdf/", "/fonts/",
    "/course-search/",
)

_last_request_at = 0.0


class DisallowedPath(Exception):
    """Raised if we ever try to fetch something robots.txt excludes."""


def _check_allowed(url: str) -> None:
    """The disallow list is the catalog's robots.txt, so only apply it there.
    enr-apps.as.cmu.edu serves no robots.txt (404), and its /SOC/ paths would
    otherwise collide with catalog rules like /search/."""
    parts = urllib.parse.urlparse(url)
    if parts.netloc != urllib.parse.urlparse(CATALOG).netloc:
        return
    for bad in DISALLOWED:
        if parts.path.startswith(bad):
            raise DisallowedPath(f"{url} matches disallowed path {bad}")


def _cache_path(url: str, body: str = "") -> Path:
    digest = hashlib.sha256((url + "|" + body).encode()).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower().replace(CATALOG, "")).strip("-")
    return CACHE_DIR / f"{slug[:80]}-{digest}.html"


def _throttle(url: str = "") -> None:
    global _last_request_at
    host = urllib.parse.urlparse(url).netloc
    delay = HOST_DELAYS.get(host, DELAY_SECONDS)
    elapsed = time.monotonic() - _last_request_at
    if elapsed < delay:
        time.sleep(delay - elapsed)


def fetch(url: str, *, refresh: bool = False) -> str:
    """Fetch a URL, using the on-disk cache unless refresh is set."""
    _check_allowed(url)

    cached = _cache_path(url)
    if cached.exists() and not refresh:
        return cached.read_text(encoding="utf-8", errors="replace")

    global _last_request_at
    _throttle(url)

    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    _last_request_at = time.monotonic()

    text = raw.decode("utf-8", errors="replace")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text


def post(url: str, params: dict[str, str], *, refresh: bool = False, timeout: int = 180) -> str:
    """POST a form and cache the response. Used for the Schedule of Classes,
    which is a plain HTML form POST with no token or session."""
    body = urllib.parse.urlencode(sorted(params.items()))

    cached = _cache_path(url, body)
    if cached.exists() and not refresh:
        return cached.read_text(encoding="utf-8", errors="replace")

    global _last_request_at
    _throttle(url)

    request = urllib.request.Request(
        url,
        data=body.encode(),
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    _last_request_at = time.monotonic()

    text = raw.decode("utf-8", errors="replace")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text


def _sitemap_urls(refresh: bool = False) -> list[str]:
    sitemap = fetch(SITEMAP, refresh=refresh)
    return sorted(set(re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sitemap)))


def course_page_urls(*, refresh: bool = False) -> list[str]:
    """Every /courses/ page listed in the catalog sitemap."""
    return [u for u in _sitemap_urls(refresh) if u.rstrip("/").endswith("/courses")]


def program_page_urls(*, refresh: bool = False) -> list[str]:
    """Every non-/courses/ page in the sitemap.

    Not all of these carry requirement tables — index and boilerplate pages
    don't. The parser returns None for those and the build skips them, which is
    more robust than trying to guess program pages from URL shape.
    """
    return [u for u in _sitemap_urls(refresh) if not u.rstrip("/").endswith("/courses")]
