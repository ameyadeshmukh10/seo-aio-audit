#!/usr/bin/env python3
"""
STEP 1 — Discover Key Pages
============================
Crawls a company website to find product, platform, solutions, services,
pricing, and other high-value pages — building the list of target URLs to
evaluate for internal-linking coverage from blog posts.

Usage:
    python 01_discover_pages.py --url https://memgraph.com/

Output:
    audits/{slug}/{slug}_pages.json
"""

import subprocess, sys

# ── Auto-install deps ──────────────────────────────────────────────────────
_DEPS = {"requests": "requests", "beautifulsoup4": "bs4", "lxml": "lxml"}
def _ensure_deps():
    import importlib, importlib.util
    missing = [pkg for pkg, mod in _DEPS.items() if not importlib.util.find_spec(mod)]
    if missing:
        print(f"[setup] Installing: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing, "-q"])
_ensure_deps()

import argparse, json, os, re, time
from collections import defaultdict
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ── Constants ──────────────────────────────────────────────────────────────
REQUEST_TIMEOUT = 20
REQUEST_DELAY   = 0.4
MAX_CRAWL_DEPTH = 2          # how many levels deep to follow links
MAX_PAGES_PER_CATEGORY = 100

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# URL path keywords → category
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "product":   ["product", "platform", "feature", "capability", "module",
                  "function", "tool", "app", "application", "suite", "engine"],
    "solutions": ["solution", "use-case", "use_case", "industry", "vertical",
                  "segment", "/for-", "by-industry", "by-role", "persona"],
    "services":  ["service", "professional", "consult", "implement",
                  "support", "managed", "agency", "partner"],
    "pricing":   ["pricing", "/price", "/plans", "/package", "/tier",
                  "/cost", "/buy", "/quote", "/trial"],
    "resources": ["resource", "/docs", "/documentation", "/guide",
                  "/tutorial", "/learn", "/academy", "/knowledge",
                  "/help", "/getting-started"],
    "company":   ["about", "/company", "/team", "/career", "/job",
                  "/press", "/investor", "/mission", "/vision", "/story"],
    "customers": ["customer", "case-stud", "success-stor", "testimonial",
                  "partner", "client", "/roi", "/results"],
    "integrations": ["integrat", "connector", "plugin", "extension",
                     "marketplace", "/apps", "ecosystem"],
}

# Paths to always skip
SKIP_PATTERNS = re.compile(
    r"/(blog|news|article|post|feed|tag|categor|author|search|"
    r"privacy|cookie|legal|terms|gdpr|sitemap|wp-|cdn-cgi|"
    r"login|signup|register|checkout|cart|404|\#)",
    re.IGNORECASE,
)

# File extensions that aren't pages
SKIP_EXTENSIONS = re.compile(
    r"\.(pdf|png|jpg|jpeg|gif|svg|webp|ico|zip|mp4|mp3|css|js|xml|json)$",
    re.IGNORECASE,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def url_to_slug(url: str) -> str:
    """https://memgraph.com/ → memgraph_com"""
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return re.sub(r"[.\-]+", "_", domain)


def url_to_company_name(url: str) -> str:
    """https://memgraph.com/ → Memgraph"""
    domain = urlparse(url).netloc.lower().replace("www.", "")
    name = domain.split(".")[0]
    return name.replace("-", " ").replace("_", " ").title()


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def fetch_html(url: str, session: requests.Session) -> Optional[str]:
    """Fetch a URL and return HTML text, or None on failure."""
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "html" not in ct:
            return None
        return r.text
    except Exception as exc:
        print(f"  [warn] fetch failed: {url} — {exc}")
        return None


def normalise_url(href: str, base: str, base_domain: str) -> Optional[str]:
    """
    Resolve a possibly-relative href against base.
    Returns None if the URL is off-domain, a skip pattern, or a file extension.
    """
    if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    url = urljoin(base, href).split("#")[0].split("?")[0].rstrip("/")
    parsed = urlparse(url)
    # Must be same domain (allow www. variant)
    domain = parsed.netloc.lower().replace("www.", "")
    if domain != base_domain:
        return None
    if SKIP_EXTENSIONS.search(parsed.path):
        return None
    if SKIP_PATTERNS.search(parsed.path):
        return None
    # Must have a path (not just the homepage)
    if parsed.path in ("", "/"):
        return None
    return url


def categorise_url(url: str) -> Optional[str]:
    """
    Return the category string for a URL based on path keywords,
    or None if it doesn't match any important category.
    """
    path = urlparse(url).path.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in path:
                return cat
    return None


def extract_links(html: str, page_url: str, base_domain: str,
                  is_nav: bool = False) -> list[dict]:
    """
    Parse HTML and return a list of link dicts:
        { url, text, in_nav, category }
    `is_nav` marks whether we're inside a nav element.
    """
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    seen: set[str] = set()

    # Find nav containers (header nav, sidebar nav, footer nav)
    nav_containers = (
        soup.find_all("nav") +
        soup.find_all(attrs={"role": "navigation"}) +
        soup.find_all(class_=re.compile(r"(nav|menu|header)", re.I)) +
        soup.find_all("header")
    )
    nav_urls: set[str] = set()
    for container in nav_containers:
        for a in container.find_all("a", href=True):
            n = normalise_url(a["href"], page_url, base_domain)
            if n:
                nav_urls.add(n)

    # All links on the page
    for a in soup.find_all("a", href=True):
        url = normalise_url(a["href"], page_url, base_domain)
        if not url or url in seen:
            continue
        seen.add(url)
        cat = categorise_url(url)
        results.append({
            "url":      url,
            "text":     a.get_text(strip=True)[:120],
            "in_nav":   url in nav_urls,
            "category": cat,
        })
    return results


# ── Main discovery logic ───────────────────────────────────────────────────

def discover_pages(base_url: str) -> dict:
    """
    Crawl the website up to MAX_CRAWL_DEPTH levels and return:
    {
      "company":  str,
      "base_url": str,
      "slug":     str,
      "categories": {
          "product":   [{ url, text, in_nav }],
          "solutions": [...],
          ...
      },
      "uncategorised_nav": [{ url, text }],
      "all_found": int,
    }
    """
    session      = make_session()
    base_domain  = urlparse(base_url).netloc.lower().replace("www.", "")
    slug         = url_to_slug(base_url)
    company_name = url_to_company_name(base_url)

    print(f"[1/discover] Company : {company_name}")
    print(f"[1/discover] Domain  : {base_domain}")
    print(f"[1/discover] Slug    : {slug}")
    print()

    # -- Level 0: homepage ------------------------------------------------
    print(f"[1/discover] Fetching homepage …")
    html = fetch_html(base_url, session)
    if not html:
        print("[error] Could not fetch homepage. Aborting.")
        return {}

    level0_links = extract_links(html, base_url, base_domain)
    print(f"[1/discover] Homepage links found: {len(level0_links)}")

    # Accumulate everything keyed by URL
    all_links: dict[str, dict] = {}
    queue: list[str] = []   # URLs to crawl at depth 1

    for lk in level0_links:
        all_links[lk["url"]] = lk
        if lk["in_nav"] or lk["category"]:
            queue.append(lk["url"])

    # Deduplicate queue
    queue = list(dict.fromkeys(queue))
    print(f"[1/discover] Nav / categorised links to follow: {len(queue)}")

    # -- Level 1: follow nav + categorised links --------------------------
    crawled: set[str] = {base_url}
    for i, url in enumerate(queue[:60]):   # cap to avoid runaway crawl
        if url in crawled:
            continue
        crawled.add(url)
        time.sleep(REQUEST_DELAY)
        print(f"  [{i+1}/{min(len(queue),60)}] {url}")
        sub_html = fetch_html(url, session)
        if not sub_html:
            continue
        sub_links = extract_links(sub_html, url, base_domain)
        for lk in sub_links:
            if lk["url"] not in all_links:
                all_links[lk["url"]] = lk
            elif lk["in_nav"] and not all_links[lk["url"]]["in_nav"]:
                all_links[lk["url"]]["in_nav"] = True

    print(f"\n[1/discover] Total unique pages found: {len(all_links)}")

    # -- Organise into categories ----------------------------------------
    categories: dict[str, list[dict]] = defaultdict(list)
    uncategorised_nav: list[dict] = []

    for url, lk in sorted(all_links.items()):
        cat = lk.get("category")
        entry = {"url": url, "text": lk["text"], "in_nav": lk["in_nav"]}
        if cat:
            categories[cat].append(entry)
        elif lk["in_nav"]:
            uncategorised_nav.append(entry)

    # Sort each category: nav links first, then alphabetically
    for cat in categories:
        categories[cat].sort(key=lambda x: (not x["in_nav"], x["url"]))

    # Summary
    print()
    print("[1/discover] Category breakdown:")
    for cat, pages in sorted(categories.items()):
        print(f"  {cat:<15} {len(pages):>4} pages")
    print(f"  {'uncategorised_nav':<15} {len(uncategorised_nav):>4} pages")

    return {
        "company":           company_name,
        "base_url":          base_url,
        "slug":              slug,
        "categories":        dict(categories),
        "uncategorised_nav": uncategorised_nav,
        "all_found":         len(all_links),
    }


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Discover product/solutions/services pages on a company website."
    )
    parser.add_argument("--url", required=True,
                        help="Base URL of the company website (e.g. https://memgraph.com/)")
    args = parser.parse_args()

    base_url = args.url.rstrip("/") + "/"
    slug     = url_to_slug(base_url)

    output_dir = os.path.join("audits", slug)
    os.makedirs(output_dir, exist_ok=True)

    result = discover_pages(base_url)
    if not result:
        sys.exit(1)

    out_path = os.path.join(output_dir, f"{slug}_pages.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n[1/discover] ✓ Pages saved to: {out_path}")
    print(f"[1/discover] Next step: python 02_discover_blog_posts.py --url {args.url}")


if __name__ == "__main__":
    main()
