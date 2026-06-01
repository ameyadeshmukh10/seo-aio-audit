#!/usr/bin/env python3
"""
STEP 2 — Discover All Blog Post URLs
======================================
Tries four strategies in order of reliability to collect every published
blog post URL for any company website:

  1. WordPress REST API  — fastest, most complete for WP sites
  2. XML Sitemaps        — works for most CMS platforms
  3. RSS / Atom Feed     — good fallback for any blog
  4. Blog Index Crawl   — universal fallback with pagination

Results are merged with any existing URL list and saved to disk.

Usage:
    python 02_discover_blog_posts.py --url https://memgraph.com/

Output:
    audits/{slug}/{slug}_blog_urls.json
"""

import subprocess, sys

_DEPS = {"requests": "requests", "beautifulsoup4": "bs4", "lxml": "lxml"}
def _ensure_deps():
    import importlib, importlib.util
    missing = [pkg for pkg, mod in _DEPS.items() if not importlib.util.find_spec(mod)]
    if missing:
        print(f"[setup] Installing: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing, "-q"])
_ensure_deps()

import argparse, json, os, re, time
from typing import Optional
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

# ── Constants ──────────────────────────────────────────────────────────────
REQUEST_TIMEOUT = 25
REQUEST_DELAY   = 0.5
MAX_CRAWL_PAGES = 100        # max blog index pages to paginate through
MAX_SITEMAP_URLS = 5000      # cap sitemap processing

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Common blog path slugs to probe
BLOG_PATHS = [
    "/blog", "/news", "/articles", "/article", "/insights", "/resources",
    "/learn", "/learning", "/journal", "/posts", "/blog/posts",
    "/content", "/hub", "/stories",
]

# Patterns that strongly suggest a URL is a blog post
BLOG_POST_PATTERNS = re.compile(
    r"/(blog|news|article|articles|post|posts|insights|resources|learn|"
    r"stories|journal|content|hub)/[^/]+/?$",
    re.IGNORECASE,
)

# URL fragments to exclude from blog post lists
NOT_A_POST = re.compile(
    r"/(tag|tags|category|categories|author|authors|page|archive|feed|"
    r"search|wp-|cdn-|sitemap|login|signup|#)",
    re.IGNORECASE,
)

SKIP_EXTENSIONS = re.compile(
    r"\.(pdf|png|jpg|jpeg|gif|svg|webp|ico|zip|mp4|mp3|css|js)$",
    re.IGNORECASE,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def url_to_slug(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return re.sub(r"[.\-]+", "_", domain)


def url_to_company_name(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    name = domain.split(".")[0]
    return name.replace("-", " ").replace("_", " ").title()


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def get(session: requests.Session, url: str, **kwargs) -> Optional[requests.Response]:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True, **kwargs)
        return r if r.ok else None
    except Exception:
        return None


def is_likely_post(url: str, base_domain: str) -> bool:
    """Heuristic: does this URL look like a blog post (not a category/tag/etc)?"""
    parsed = urlparse(url)
    if parsed.netloc.lower().replace("www.", "") != base_domain:
        return False
    if SKIP_EXTENSIONS.search(parsed.path):
        return False
    if NOT_A_POST.search(parsed.path):
        return False
    if not BLOG_POST_PATTERNS.search(parsed.path):
        return False
    # Path should be at least 2 segments deep  (e.g. /blog/my-post/)
    segments = [s for s in parsed.path.strip("/").split("/") if s]
    return len(segments) >= 2


def load_existing(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return [u for u in d if isinstance(u, str)]
    except Exception:
        return []


def merge_urls(existing: list[str], new: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for u in existing + new:
        norm = u.rstrip("/")
        if norm not in seen:
            seen.add(norm)
            merged.append(u)
    return merged


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 1 — WordPress REST API
# ══════════════════════════════════════════════════════════════════════════════

def try_wordpress_api(base_url: str, session: requests.Session) -> list[str]:
    """
    Try the WP REST API at /wp-json/wp/v2/posts.
    Returns a list of post URLs if the site uses WordPress, else [].
    """
    test_url = base_url.rstrip("/") + "/wp-json/wp/v2/posts?per_page=1&_fields=link"
    r = get(session, test_url)
    if not r:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    if not isinstance(data, list) or not data:
        return []

    print("[2/blog] ✓ WordPress REST API detected — paginating …")
    urls: list[str] = []
    page = 1
    api_base = base_url.rstrip("/") + "/wp-json/wp/v2/posts"

    while True:
        api_url = f"{api_base}?per_page=100&page={page}&_fields=link"
        r = get(session, api_url)
        if not r:
            break
        try:
            batch = r.json()
        except Exception:
            break
        if not isinstance(batch, list) or not batch:
            break
        found = [item["link"] for item in batch if isinstance(item, dict) and "link" in item]
        urls.extend(found)
        print(f"  [WP API] Page {page}: {len(found)} posts (total: {len(urls)})")
        if len(batch) < 100:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    return urls


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 2 — XML Sitemaps
# ══════════════════════════════════════════════════════════════════════════════

def _parse_sitemap_xml(content: str, base_domain: str) -> tuple[list[str], list[str]]:
    """
    Parse a sitemap XML string.
    Returns (page_urls, child_sitemap_urls).
    """
    page_urls: list[str] = []
    child_sitemaps: list[str] = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return [], []
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    # Sitemap index → contains <sitemap> elements
    for sm in root.findall(".//sm:sitemap/sm:loc", ns):
        if sm.text:
            child_sitemaps.append(sm.text.strip())
    # Regular sitemap → contains <url> elements
    for url in root.findall(".//sm:url/sm:loc", ns):
        if url.text:
            page_urls.append(url.text.strip())
    return page_urls, child_sitemaps


def _find_sitemap_urls(base_url: str, session: requests.Session) -> list[str]:
    """Locate sitemap URLs by checking robots.txt and common locations."""
    candidates: list[str] = []

    # Check robots.txt
    r = get(session, base_url.rstrip("/") + "/robots.txt")
    if r and r.text:
        for line in r.text.splitlines():
            if line.lower().startswith("sitemap:"):
                sm_url = line.split(":", 1)[1].strip()
                candidates.append(sm_url)

    # Common locations
    for path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
                 "/post-sitemap.xml", "/page-sitemap.xml", "/blog-sitemap.xml",
                 "/news-sitemap.xml"]:
        candidates.append(base_url.rstrip("/") + path)

    return list(dict.fromkeys(candidates))  # dedupe preserving order


def try_sitemaps(base_url: str, session: requests.Session, base_domain: str) -> list[str]:
    """
    Locate and parse XML sitemaps, collecting all blog-post-like URLs.
    Returns a list of post URLs.
    """
    sitemap_urls = _find_sitemap_urls(base_url, session)
    if not sitemap_urls:
        return []

    post_urls: list[str] = []
    visited_sitemaps: set[str] = set()
    queue = sitemap_urls[:8]  # don't chase too many

    for sm_url in queue:
        if sm_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sm_url)
        r = get(session, sm_url)
        if not r:
            continue
        ct = r.headers.get("content-type", "")
        if "xml" not in ct and not sm_url.endswith(".xml"):
            continue
        pages, children = _parse_sitemap_xml(r.text, base_domain)

        # Add child sitemaps to the queue if they look blog-related
        for child in children:
            if any(kw in child.lower() for kw in
                   ["post", "blog", "news", "article", "content"]):
                if child not in visited_sitemaps:
                    queue.append(child)

        # Filter page URLs to only blog-post-like ones
        for u in pages[:MAX_SITEMAP_URLS]:
            if is_likely_post(u, base_domain):
                post_urls.append(u)

        time.sleep(REQUEST_DELAY)

    if post_urls:
        print(f"[2/blog] ✓ Sitemaps: found {len(post_urls)} blog post URLs")
    return post_urls


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 3 — RSS / Atom Feed
# ══════════════════════════════════════════════════════════════════════════════

def _probe_rss(session: requests.Session, url: str) -> list[str]:
    r = get(session, url)
    if not r:
        return []
    ct = r.headers.get("content-type", "")
    if not any(x in ct for x in ["xml", "rss", "atom"]):
        # Try parsing anyway if URL looks feed-like
        if not any(x in url for x in ["feed", "rss", "atom"]):
            return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    urls: list[str] = []
    # RSS 2.0
    for item in root.findall(".//item"):
        link = item.findtext("link")
        if link:
            urls.append(link.strip())
    # Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        link_el = entry.find("atom:link[@rel='alternate']", ns) or entry.find("atom:link", ns)
        if link_el is not None:
            href = link_el.get("href", "")
            if href:
                urls.append(href.strip())
    return urls


def try_rss_feeds(base_url: str, session: requests.Session) -> list[str]:
    """
    Check common RSS/Atom feed locations + <link rel="alternate"> in the homepage.
    Returns post URLs found in the feed.
    """
    probe_paths = [
        "/feed", "/feed/", "/rss", "/rss/", "/feed.xml", "/rss.xml",
        "/atom.xml", "/blog/feed", "/blog/feed/", "/blog/rss",
        "/blog/rss/", "/news/feed", "/articles/feed",
    ]

    # Also check homepage for <link rel="alternate" type="application/rss+xml">
    r = get(session, base_url)
    if r and r.text:
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup.find_all("link", rel="alternate"):
            t = tag.get("type", "")
            href = tag.get("href", "")
            if ("rss" in t or "atom" in t) and href:
                full = urljoin(base_url, href)
                probe_paths.insert(0, full)

    urls: list[str] = []
    for path in probe_paths[:12]:
        full = path if path.startswith("http") else base_url.rstrip("/") + path
        found = _probe_rss(session, full)
        if found:
            print(f"[2/blog] ✓ RSS feed at {full}: {len(found)} entries")
            urls.extend(found)
            break   # one good feed is enough
        time.sleep(0.2)

    return urls


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 4 — Blog Index Crawl
# ══════════════════════════════════════════════════════════════════════════════

def _find_blog_url(base_url: str, session: requests.Session) -> Optional[str]:
    """
    Find the blog index URL by:
    1. Checking the homepage navigation for blog/news/etc links
    2. Probing common paths
    """
    r = get(session, base_url)
    if r and r.text:
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            href = a["href"].lower()
            if any(kw in text or kw in href for kw in
                   ["blog", "news", "articles", "insights", "resources", "learn"]):
                full = urljoin(base_url, a["href"]).rstrip("/")
                parsed = urlparse(full)
                if parsed.netloc == urlparse(base_url).netloc:
                    return full

    for path in BLOG_PATHS:
        full = base_url.rstrip("/") + path
        r = get(session, full)
        if r and r.ok:
            ct = r.headers.get("content-type", "")
            if "html" in ct:
                return full
        time.sleep(0.2)

    return None


def _extract_post_links(html: str, blog_url: str, base_domain: str) -> list[str]:
    """Extract all post-like links from a blog index page."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        full = urljoin(blog_url, a["href"]).rstrip("/")
        if is_likely_post(full, base_domain):
            found.append(full)
    return list(dict.fromkeys(found))


def _find_next_page(soup: BeautifulSoup, current_url: str) -> Optional[str]:
    """
    Look for a 'next page' link in common pagination patterns.
    """
    # Explicit rel="next"
    next_link = soup.find("a", rel="next")
    if next_link and next_link.get("href"):
        return urljoin(current_url, next_link["href"])

    # Text-based next buttons
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        if text in ("next", "next page", "newer posts", "→", "»", "load more"):
            return urljoin(current_url, a["href"])

    # Numbered pagination: find current page number and look for +1
    # Try to detect /page/N or ?page=N patterns
    parsed = urlparse(current_url)
    page_match = re.search(r"/page/(\d+)", parsed.path)
    if page_match:
        n = int(page_match.group(1))
        return current_url.replace(f"/page/{n}", f"/page/{n+1}")
    if "page=" in parsed.query:
        n = int(re.search(r"page=(\d+)", parsed.query).group(1))
        return re.sub(r"page=\d+", f"page={n+1}", current_url)

    return None


def try_blog_crawl(base_url: str, session: requests.Session,
                   base_domain: str) -> list[str]:
    """
    Find the blog index, then crawl it paginating through all pages.
    Returns all discovered post URLs.
    """
    blog_url = _find_blog_url(base_url, session)
    if not blog_url:
        print("[2/blog] Could not locate blog index URL.")
        return []

    print(f"[2/blog] ✓ Blog index found: {blog_url}")
    post_urls: list[str] = []
    visited_pages: set[str] = {blog_url}
    current = blog_url
    page_num = 1

    while current and page_num <= MAX_CRAWL_PAGES:
        r = get(session, current)
        if not r or "html" not in r.headers.get("content-type", ""):
            break
        soup = BeautifulSoup(r.text, "lxml")
        found = _extract_post_links(r.text, current, base_domain)
        new = [u for u in found if u not in post_urls]
        post_urls.extend(new)
        print(f"  [crawl] Page {page_num}: +{len(new)} posts (total: {len(post_urls)})")

        next_pg = _find_next_page(soup, current)
        if not next_pg or next_pg in visited_pages:
            break
        visited_pages.add(next_pg)
        current = next_pg
        page_num += 1
        time.sleep(REQUEST_DELAY)

    return post_urls


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

def discover_blog_posts(base_url: str) -> list[str]:
    """
    Run all four discovery strategies in order.
    Return a deduplicated, merged list of blog post URLs.
    """
    session     = make_session()
    base_domain = urlparse(base_url).netloc.lower().replace("www.", "")
    all_urls: list[str] = []

    # 1. WordPress REST API
    print("\n[2/blog] Strategy 1: WordPress REST API …")
    wp_urls = try_wordpress_api(base_url, session)
    if wp_urls:
        all_urls = merge_urls(all_urls, wp_urls)
        print(f"[2/blog] WP API total: {len(wp_urls)}")
    else:
        print("[2/blog] Not a WordPress site (or API disabled).")

    # 2. XML Sitemaps
    print("\n[2/blog] Strategy 2: XML Sitemaps …")
    sm_urls = try_sitemaps(base_url, session, base_domain)
    before = len(all_urls)
    all_urls = merge_urls(all_urls, sm_urls)
    print(f"[2/blog] Sitemaps added {len(all_urls)-before} new URLs.")

    # 3. RSS / Atom Feed
    print("\n[2/blog] Strategy 3: RSS / Atom Feeds …")
    rss_urls = try_rss_feeds(base_url, session)
    before = len(all_urls)
    all_urls = merge_urls(all_urls, rss_urls)
    print(f"[2/blog] RSS added {len(all_urls)-before} new URLs.")

    # 4. Blog index crawl (only run if the above strategies found nothing)
    if len(all_urls) < 10:
        print("\n[2/blog] Strategy 4: Blog Index Crawl (fallback) …")
        crawl_urls = try_blog_crawl(base_url, session, base_domain)
        before = len(all_urls)
        all_urls = merge_urls(all_urls, crawl_urls)
        print(f"[2/blog] Crawl added {len(all_urls)-before} new URLs.")
    else:
        print(f"\n[2/blog] Skipping crawl — already have {len(all_urls)} URLs.")

    return all_urls


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Discover all blog post URLs for a company website."
    )
    parser.add_argument("--url", required=True,
                        help="Base URL (e.g. https://memgraph.com/)")
    args = parser.parse_args()

    base_url = args.url.rstrip("/") + "/"
    slug     = url_to_slug(base_url)

    output_dir = os.path.join("audits", slug)
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{slug}_blog_urls.json")

    # Merge with any existing list
    existing = load_existing(out_path)
    if existing:
        print(f"[2/blog] Loaded {len(existing)} existing URLs from {out_path}")

    discovered = discover_blog_posts(base_url)
    merged     = merge_urls(existing, discovered)
    new_count  = len(merged) - len(existing)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    print(f"\n[2/blog] ─────────────────────────────────────────")
    print(f"[2/blog] Existing : {len(existing)}")
    print(f"[2/blog] Discovered: {len(discovered)}")
    print(f"[2/blog] New added : {new_count}")
    print(f"[2/blog] Total     : {len(merged)}")
    print(f"[2/blog] ✓ Saved to: {out_path}")
    print(f"[2/blog] Next step : python 03_audit_posts.py --url {args.url}")


if __name__ == "__main__":
    main()
