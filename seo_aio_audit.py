#!/usr/bin/env python3
"""
SEO + AIO (AI Overview Optimization) Audit Tool
Crawls a blog and performs deep SEO and AIO analysis on every post.

Usage:
    python seo_aio_audit.py
    python seo_aio_audit.py --url https://example.com/blog --max-posts 10 --output-dir ./results/
"""

import subprocess
import sys

# ---------------------------------------------------------------------------
# Auto-install dependencies
# ---------------------------------------------------------------------------
REQUIRED = ["requests", "beautifulsoup4", "tqdm", "lxml"]

def _ensure_deps():
    import importlib
    mapping = {"beautifulsoup4": "bs4", "lxml": "lxml"}
    missing = []
    for pkg in REQUIRED:
        mod = mapping.get(pkg, pkg)
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[setup] Installing missing packages: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing, "-q"])

_ensure_deps()

# ---------------------------------------------------------------------------
# Standard + third-party imports
# ---------------------------------------------------------------------------
import argparse
import concurrent.futures
import json
import os
import re
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_URL = "https://momentivesoftware.com/blog"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30
MAX_CONCURRENT = 3
DELAY_BETWEEN = 0.5  # seconds


# ============================================================================
# PHASE 1 — CRAWL & DISCOVER
# ============================================================================

def fetch_page(url: str, session: requests.Session) -> Optional[str]:
    """Fetch a URL and return HTML text, or None on failure."""
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        print(f"  [error] Failed to fetch {url}: {exc}")
        return None


def discover_blog_post_urls(base_url: str, session: requests.Session,
                            max_posts: int = 0) -> list[str]:
    """
    Paginate through the blog index and collect all blog post URLs.
    Handles both 'next page' links and numbered pagination.
    """
    post_urls: list[str] = []
    visited_pages: set[str] = set()
    current_url = base_url.rstrip("/") + "/"
    page_num = 1

    while current_url and current_url not in visited_pages:
        visited_pages.add(current_url)
        print(f"  [crawl] Fetching index page {page_num}: {current_url}")
        html = fetch_page(current_url, session)
        if not html:
            break
        soup = BeautifulSoup(html, "lxml")

        # --- Extract post links from the listing page ---
        new_urls = _extract_post_links(soup, base_url)
        for u in new_urls:
            if u not in post_urls:
                post_urls.append(u)
                if 0 < max_posts <= len(post_urls):
                    print(f"  [crawl] Reached --max-posts limit ({max_posts})")
                    return post_urls

        # --- Find next page link ---
        next_url = _find_next_page(soup, current_url, base_url)
        if next_url and next_url not in visited_pages:
            current_url = next_url
            page_num += 1
            time.sleep(DELAY_BETWEEN)
        else:
            break

    print(f"  [crawl] Discovered {len(post_urls)} blog post URLs across {page_num} index page(s)")
    return post_urls


def _extract_post_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Pull blog post links from a listing page."""
    urls = []
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        full = urljoin(base_url, href)
        parsed = urlparse(full)

        # Must be same domain
        if parsed.netloc != base_domain:
            continue

        path = parsed.path.rstrip("/")

        # Heuristic: blog post URLs typically have /blog/slug pattern
        # Skip the index pages themselves, category pages, tag pages, etc.
        if re.match(r"^/blog/.+", path):
            # Skip pagination pages like /blog/page/2
            if "/page/" in path or "/category/" in path or "/tag/" in path or "/author/" in path:
                continue
            # Skip anchors / query-only variations
            clean = parsed._replace(fragment="", query="").geturl()
            if clean not in urls:
                urls.append(clean)

    return urls


def _find_next_page(soup: BeautifulSoup, current_url: str, base_url: str) -> Optional[str]:
    """Detect 'next page' pagination link."""
    # Strategy 1: look for a link with text like "Next", "»", ">"
    for a_tag in soup.find_all("a", href=True):
        text = a_tag.get_text(strip=True).lower()
        classes = " ".join(a_tag.get("class", []))
        if any(kw in text for kw in ["next", "»", "›", "older"]) or "next" in classes:
            return urljoin(base_url, a_tag["href"])

    # Strategy 2: look for numbered pagination and pick the next number
    # Find all /blog/page/N links
    page_links: dict[int, str] = {}
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        m = re.search(r"/page/(\d+)", href)
        if m:
            page_links[int(m.group(1))] = urljoin(base_url, href)

    if page_links:
        # Determine current page number
        m = re.search(r"/page/(\d+)", current_url)
        current_num = int(m.group(1)) if m else 1
        next_num = current_num + 1
        if next_num in page_links:
            return page_links[next_num]

    return None


# ---------------------------------------------------------------------------
# Post parsing — extract all metadata from a single blog post
# ---------------------------------------------------------------------------

def parse_blog_post(url: str, html: str) -> dict[str, Any]:
    """Parse a blog post HTML and extract all required metadata."""
    soup = BeautifulSoup(html, "lxml")
    data: dict[str, Any] = {"url": url}

    # Title tag
    title_tag = soup.find("title")
    data["title"] = title_tag.get_text(strip=True) if title_tag else ""

    # Meta description
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
    data["meta_description"] = meta_desc.get("content", "").strip() if meta_desc else ""

    # Headings
    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = [h.get_text(strip=True) for h in soup.find_all("h2")]
    h3s = [h.get_text(strip=True) for h in soup.find_all("h3")]
    data["h1s"] = h1s
    data["h2s"] = h2s
    data["h3s"] = h3s

    # Word count (visible text in <body>)
    body = soup.find("body")
    visible_text = body.get_text(separator=" ", strip=True) if body else ""
    data["word_count"] = len(visible_text.split())

    # Dates
    data["publish_date"] = _extract_date(soup, "datePublished") or _extract_meta_date(soup, "article:published_time")
    data["last_updated"] = _extract_date(soup, "dateModified") or _extract_meta_date(soup, "article:modified_time")

    # Author
    data["author"] = _extract_author(soup)

    # Category tags
    data["categories"] = _extract_categories(soup)

    # Links
    parsed_url = urlparse(url)
    base_domain = parsed_url.netloc
    internal_links = []
    external_links = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        full = urljoin(url, href)
        anchor = a_tag.get_text(strip=True)
        link_parsed = urlparse(full)
        if link_parsed.netloc == base_domain:
            internal_links.append({"href": full, "anchor_text": anchor})
        else:
            external_links.append({"href": full, "anchor_text": anchor})
    data["internal_links"] = internal_links
    data["external_links"] = external_links

    # Images
    images = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", None)
        images.append({
            "src": urljoin(url, src) if src else "",
            "alt": alt if alt is not None else "",
            "alt_missing": alt is None or alt.strip() == ""
        })
    data["images"] = images

    # Schema markup (JSON-LD)
    schemas = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            parsed = json.loads(script.string)
            schemas.append(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    data["schema_markup"] = schemas

    # Canonical URL
    canon = soup.find("link", rel="canonical")
    data["canonical_url"] = canon.get("href", "").strip() if canon else ""

    # Robots meta
    robots = soup.find("meta", attrs={"name": re.compile(r"robots", re.I)})
    data["robots_meta"] = robots.get("content", "").strip() if robots else ""

    # Open Graph tags
    og = {}
    for prop in ["og:title", "og:description", "og:image"]:
        tag = soup.find("meta", attrs={"property": prop})
        og[prop] = tag.get("content", "").strip() if tag else ""
    data["open_graph"] = og

    # FAQ detection
    data["has_faq_schema"] = _has_schema_type(schemas, "FAQPage")
    data["has_faq_content"] = _detect_faq_content(soup)

    # CTA detection
    data["has_demo_cta"] = any(
        "/demo" in link["href"].lower() for link in internal_links
    )
    data["has_solutions_cta"] = any(
        "/solutions" in link["href"].lower() for link in internal_links
    )

    return data


def _extract_date(soup: BeautifulSoup, prop: str) -> str:
    """Try to extract a date from JSON-LD schema."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string)
            if isinstance(d, dict):
                if prop in d:
                    return str(d[prop])
                # Check @graph array
                for item in d.get("@graph", []):
                    if isinstance(item, dict) and prop in item:
                        return str(item[prop])
            elif isinstance(d, list):
                for item in d:
                    if isinstance(item, dict) and prop in item:
                        return str(item[prop])
        except (json.JSONDecodeError, TypeError):
            pass
    return ""


def _extract_meta_date(soup: BeautifulSoup, prop: str) -> str:
    """Extract date from <meta property='article:published_time'>."""
    tag = soup.find("meta", attrs={"property": prop})
    return tag.get("content", "").strip() if tag else ""


def _extract_author(soup: BeautifulSoup) -> str:
    """Best-effort author extraction."""
    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string)
            items = [d] if isinstance(d, dict) else d if isinstance(d, list) else []
            # Also check @graph
            if isinstance(d, dict) and "@graph" in d:
                items.extend(d["@graph"])
            for item in items:
                if not isinstance(item, dict):
                    continue
                author = item.get("author")
                if isinstance(author, dict):
                    return author.get("name", "")
                if isinstance(author, list) and author:
                    return author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])
                if isinstance(author, str):
                    return author
        except (json.JSONDecodeError, TypeError):
            pass

    # Meta tag
    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author:
        return meta_author.get("content", "").strip()

    # Look for common author class patterns
    for selector in [".author", ".byline", "[rel='author']", ".post-author"]:
        tag = soup.select_one(selector)
        if tag:
            return tag.get_text(strip=True)

    return ""


def _extract_categories(soup: BeautifulSoup) -> list[str]:
    """Extract category tags from meta or visible elements."""
    cats = []

    # JSON-LD articleSection
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string)
            items = [d] if isinstance(d, dict) else d if isinstance(d, list) else []
            if isinstance(d, dict) and "@graph" in d:
                items.extend(d["@graph"])
            for item in items:
                if not isinstance(item, dict):
                    continue
                section = item.get("articleSection")
                if isinstance(section, str):
                    cats.append(section)
                elif isinstance(section, list):
                    cats.extend([s for s in section if isinstance(s, str)])
        except (json.JSONDecodeError, TypeError):
            pass

    # Meta keywords
    kw = soup.find("meta", attrs={"name": "keywords"})
    if kw and kw.get("content"):
        cats.extend([k.strip() for k in kw["content"].split(",") if k.strip()])

    # Category links (common pattern)
    for a_tag in soup.select("a[href*='/category/'], a[href*='/tag/'], .category a, .tags a"):
        txt = a_tag.get_text(strip=True)
        if txt and txt not in cats:
            cats.append(txt)

    return list(dict.fromkeys(cats))  # dedupe preserving order


def _has_schema_type(schemas: list, schema_type: str) -> bool:
    """Check if any JSON-LD block contains a given @type."""
    for s in schemas:
        if isinstance(s, dict):
            t = s.get("@type", "")
            if isinstance(t, list):
                if schema_type in t:
                    return True
            elif t == schema_type:
                return True
            for item in s.get("@graph", []):
                if isinstance(item, dict):
                    t2 = item.get("@type", "")
                    if isinstance(t2, list):
                        if schema_type in t2:
                            return True
                    elif t2 == schema_type:
                        return True
        elif isinstance(s, list):
            if _has_schema_type(s, schema_type):
                return True
    return False


def _detect_faq_content(soup: BeautifulSoup) -> bool:
    """Detect FAQ-like content even without schema."""
    text = soup.get_text(separator="\n", strip=True).lower()

    # Look for "frequently asked questions" or "faq" section heading
    if re.search(r"\b(faq|frequently asked questions)\b", text):
        return True

    # Look for repeated Q&A patterns (headings that start with question words)
    question_headings = 0
    for tag in soup.find_all(["h2", "h3", "h4"]):
        txt = tag.get_text(strip=True)
        if re.match(r"^(what|how|why|when|is|can|does|are|do|should|will)\b", txt, re.I):
            question_headings += 1
    if question_headings >= 3:
        return True

    return False


# ============================================================================
# PHASE 2 — SEO AUDIT
# ============================================================================

def audit_seo(post: dict[str, Any]) -> dict[str, Any]:
    """Run SEO checks on a parsed post. Returns dict of issues and scores."""
    issues: list[dict[str, str]] = []

    # --- Title Tag ---
    title = post.get("title", "")
    if not title:
        issues.append({"field": "title", "severity": "FAIL", "message": "Title tag is missing or empty"})
    else:
        if len(title) < 30:
            issues.append({"field": "title", "severity": "WARN", "message": f"Title too short ({len(title)} chars)"})
        if len(title) > 60:
            issues.append({"field": "title", "severity": "WARN", "message": f"Title too long ({len(title)} chars)"})
        # Keyword check: infer from H1
        h1 = post["h1s"][0] if post.get("h1s") else ""
        if h1 and not _title_keyword_overlap(title, h1):
            issues.append({"field": "title", "severity": "WARN", "message": "Title may not contain a target keyword (low overlap with H1)"})

    # --- Meta Description ---
    desc = post.get("meta_description", "")
    if not desc:
        issues.append({"field": "meta_description", "severity": "FAIL", "message": "Meta description is missing"})
    else:
        if len(desc) < 120:
            issues.append({"field": "meta_description", "severity": "WARN", "message": f"Meta description too short ({len(desc)} chars)"})
        if len(desc) > 160:
            issues.append({"field": "meta_description", "severity": "WARN", "message": f"Meta description too long ({len(desc)} chars)"})

    # --- H1 ---
    h1s = post.get("h1s", [])
    if not h1s:
        issues.append({"field": "h1", "severity": "FAIL", "message": "H1 tag is missing"})
    elif len(h1s) > 1:
        issues.append({"field": "h1", "severity": "WARN", "message": f"Multiple H1 tags found ({len(h1s)})"})
    if h1s and title and not _title_keyword_overlap(title, h1s[0]):
        issues.append({"field": "h1", "severity": "WARN", "message": "H1 does not closely match the title tag"})

    # --- Heading structure ---
    h2s = post.get("h2s", [])
    h3s = post.get("h3s", [])
    if not h2s:
        issues.append({"field": "headings", "severity": "WARN", "message": "No H2 headings found"})
    # We can't perfectly check order from flat lists, but note it
    if h3s and not h2s:
        issues.append({"field": "headings", "severity": "WARN", "message": "H3s found without any H2s — broken hierarchy"})

    # --- Word count ---
    wc = post.get("word_count", 0)
    if wc < 800:
        issues.append({"field": "word_count", "severity": "WARN", "message": f"Thin content ({wc} words)"})
        word_count_rating = "THIN"
    elif wc <= 1500:
        word_count_rating = "OK"
    else:
        word_count_rating = "GOOD"

    # --- Internal linking ---
    int_links = post.get("internal_links", [])
    if len(int_links) == 0:
        issues.append({"field": "internal_links", "severity": "FAIL", "message": "Zero internal links"})

    solution_links = [l for l in int_links if "/solutions" in l["href"].lower()]
    demo_links = [l for l in int_links if "/demo" in l["href"].lower()]

    if not solution_links:
        issues.append({"field": "internal_links", "severity": "WARN", "message": "No link to any /solutions/ page — missed conversion opportunity"})

    # --- Images ---
    images = post.get("images", [])
    missing_alt = [img for img in images if img.get("alt_missing")]
    for img in missing_alt:
        issues.append({"field": "images", "severity": "FAIL", "message": f"Image missing alt text: {img['src'][:80]}"})

    # --- Schema ---
    schemas = post.get("schema_markup", [])
    has_article = _has_schema_type(schemas, "Article") or _has_schema_type(schemas, "BlogPosting") or _has_schema_type(schemas, "NewsArticle")
    has_faq_schema = post.get("has_faq_schema", False)
    has_breadcrumb = _has_schema_type(schemas, "BreadcrumbList")
    has_howto = _has_schema_type(schemas, "HowTo")

    if not schemas:
        issues.append({"field": "schema", "severity": "FAIL", "message": "No schema markup at all"})
    else:
        if has_article:
            issues.append({"field": "schema", "severity": "GOOD", "message": "Has Article/BlogPosting schema"})
        if has_faq_schema:
            issues.append({"field": "schema", "severity": "EXCELLENT", "message": "Has FAQ schema"})

    schema_notes = []
    if has_breadcrumb:
        schema_notes.append("BreadcrumbList")
    if has_howto:
        schema_notes.append("HowTo")

    # FAQ content vs schema mismatch
    if post.get("has_faq_content") and not has_faq_schema:
        issues.append({"field": "faq", "severity": "WARN", "message": "FAQ-like content found but no FAQ schema — missed AIO opportunity"})

    # --- Canonical ---
    canonical = post.get("canonical_url", "")
    if not canonical:
        issues.append({"field": "canonical", "severity": "WARN", "message": "Canonical URL is missing"})
    else:
        if canonical.rstrip("/") == post["url"].rstrip("/"):
            pass  # self-referential — good
        else:
            issues.append({"field": "canonical", "severity": "FLAG", "message": f"Canonical points elsewhere: {canonical}"})

    return {
        "issues": issues,
        "word_count_rating": word_count_rating,
        "total_images": len(images),
        "images_missing_alt": len(missing_alt),
        "internal_link_count": len(int_links),
        "external_link_count": len(post.get("external_links", [])),
        "solution_links": [l["href"] for l in solution_links],
        "demo_links": [l["href"] for l in demo_links],
        "has_article_schema": has_article,
        "has_faq_schema": has_faq_schema,
        "has_breadcrumb_schema": has_breadcrumb,
        "has_howto_schema": has_howto,
        "schema_notes": schema_notes,
    }


def _title_keyword_overlap(title: str, h1: str) -> bool:
    """Check if title and H1 share significant keyword overlap."""
    stop = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at", "by", "is", "are", "was",
            "with", "your", "our", "how", "what", "why", "when", "this", "that", "|", "-", "–", "—", ""}
    t_words = {w.lower().strip(".,!?:;") for w in title.split()} - stop
    h_words = {w.lower().strip(".,!?:;") for w in h1.split()} - stop
    if not t_words or not h_words:
        return True  # can't determine — don't flag
    overlap = t_words & h_words
    return len(overlap) >= max(1, min(len(t_words), len(h_words)) * 0.3)


# ============================================================================
# PHASE 3 — AIO (AI OVERVIEW OPTIMIZATION) AUDIT
# ============================================================================

def audit_aio(post: dict[str, Any], html: str) -> dict[str, Any]:
    """Evaluate a post for AI Overview Optimization signals."""
    soup = BeautifulSoup(html, "lxml")
    results: dict[str, Any] = {}
    issues: list[dict[str, str]] = []

    # --- Direct answer score ---
    body = soup.find("body")
    body_text = body.get_text(separator=" ", strip=True) if body else ""
    first_200_words = " ".join(body_text.split()[:200])
    # Check if the opening contains a direct answer (not just a question or vague intro)
    first_para = ""
    for tag in soup.find_all(["p"]):
        txt = tag.get_text(strip=True)
        if len(txt) > 30:
            first_para = txt
            break
    has_direct_answer = bool(first_para) and not first_para.endswith("?") and len(first_para.split()) >= 10
    results["has_direct_answer"] = has_direct_answer
    if not has_direct_answer:
        issues.append({"field": "direct_answer", "severity": "WARN", "message": "No direct answer in opening paragraph"})

    # --- Q&A structure ---
    question_pattern = re.compile(r"^(what|how|why|when|is|can|does|are|do|should|will)\b", re.I)
    question_headings = []
    for tag in soup.find_all(["h2", "h3"]):
        txt = tag.get_text(strip=True)
        if question_pattern.match(txt):
            question_headings.append(txt)
    results["question_headings_count"] = len(question_headings)
    results["question_headings"] = question_headings
    if len(question_headings) == 0:
        issues.append({"field": "qa_structure", "severity": "WARN", "message": "No question-format headings found"})
    elif len(question_headings) >= 3:
        issues.append({"field": "qa_structure", "severity": "GOOD", "message": f"{len(question_headings)} question headings found"})

    # --- Definition presence ---
    definition_pattern = re.compile(
        r"[A-Z][A-Za-z\s]{2,40}\s+(is|are|refers?\s+to|means|describes|involves)\s+",
    )
    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
    definition_sentences = []
    for para in paragraphs:
        # Check first sentence of each paragraph
        first_sent = para.split(".")[0] if "." in para else para
        if definition_pattern.search(first_sent):
            definition_sentences.append(first_sent[:120])
    results["definition_count"] = len(definition_sentences)
    if len(definition_sentences) == 0:
        issues.append({"field": "definitions", "severity": "WARN", "message": "No definition-style sentences found"})

    # --- Statistical / data citations ---
    data_pattern = re.compile(r"\d+\s*(%|percent|million|billion|trillion)", re.I)
    data_citations = 0
    for para in paragraphs:
        data_citations += len(data_pattern.findall(para))
    results["data_citations"] = data_citations
    if data_citations == 0:
        issues.append({"field": "data_citations", "severity": "WARN", "message": "No statistical or data citations found"})
    elif data_citations >= 3:
        issues.append({"field": "data_citations", "severity": "GOOD", "message": f"{data_citations} data citations found"})

    # --- List / structured content ---
    ul_count = len(soup.find_all("ul"))
    ol_count = len(soup.find_all("ol"))
    li_count = len(soup.find_all("li"))
    results["list_elements"] = {"ul": ul_count, "ol": ol_count, "li": li_count}
    if ul_count + ol_count == 0:
        issues.append({"field": "lists", "severity": "WARN", "message": "No list elements found"})
    else:
        issues.append({"field": "lists", "severity": "GOOD", "message": f"{ul_count} unordered + {ol_count} ordered lists"})

    # --- Content freshness signal ---
    title = post.get("title", "")
    meta_desc = post.get("meta_description", "")
    has_year_in_title = bool(re.search(r"202[56]", title + " " + meta_desc))
    last_updated = post.get("last_updated", "") or post.get("publish_date", "")
    is_recent = False
    if last_updated:
        try:
            dt = _parse_date_string(last_updated)
            if dt and (datetime.now() - dt) < timedelta(days=180):
                is_recent = True
        except Exception:
            pass
    results["has_year_in_title"] = has_year_in_title
    results["is_recently_updated"] = is_recent
    if not has_year_in_title and not is_recent:
        issues.append({"field": "freshness", "severity": "WARN", "message": "No freshness signals (no 2025/2026 in title, not updated in 6 months)"})

    # --- Brand entity signals ---
    full_text = body_text.lower() if body_text else ""
    mentions_brand = "momentive software" in full_text
    links_about = any(
        "/about" in l["href"].lower() or "/solutions" in l["href"].lower()
        for l in post.get("internal_links", [])
    )
    results["mentions_brand"] = mentions_brand
    results["links_to_about_or_product"] = links_about
    if not mentions_brand:
        issues.append({"field": "brand_entity", "severity": "WARN", "message": "Does not mention 'Momentive Software' by full name"})

    # --- Compute AIO score ---
    aio_signals = sum([
        has_direct_answer,
        len(question_headings) >= 3,
        len(definition_sentences) >= 1,
        data_citations >= 3,
        (ul_count + ol_count) > 0,
        has_year_in_title or is_recent,
        post.get("has_faq_schema", False),
    ])
    results["aio_score"] = aio_signals  # out of 7
    if aio_signals >= 5:
        results["aio_readiness"] = "AIO Ready"
    elif aio_signals >= 3:
        results["aio_readiness"] = "AIO Partial"
    else:
        results["aio_readiness"] = "AIO Weak"

    results["issues"] = issues
    return results


def _parse_date_string(date_str: str) -> Optional[datetime]:
    """Try multiple date formats."""
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%m/%d/%Y",
    ]:
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=None)
        except ValueError:
            continue
    # Try dateutil as fallback
    try:
        from dateutil.parser import parse as dateutil_parse
        return dateutil_parse(date_str).replace(tzinfo=None)
    except Exception:
        return None


# ============================================================================
# PHASE 4 — SITE-WIDE ANALYSIS
# ============================================================================

def generate_site_analysis(all_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate all per-post results into site-wide analysis."""
    analysis: dict[str, Any] = {}

    # --- Publishing cadence ---
    month_counts: Counter = Counter()
    for r in all_results:
        date_str = r.get("publish_date") or r.get("last_updated") or ""
        dt = _parse_date_string(date_str) if date_str else None
        if dt:
            key = dt.strftime("%Y-%m")
            month_counts[key] += 1
    # Last 12 months
    now = datetime.now()
    cadence = {}
    for i in range(12):
        d = now - timedelta(days=30 * i)
        key = d.strftime("%Y-%m")
        cadence[key] = month_counts.get(key, 0)
    analysis["publishing_cadence"] = dict(sorted(cadence.items()))

    # --- Category coverage ---
    cat_counts: Counter = Counter()
    for r in all_results:
        for cat in r.get("categories", []):
            cat_counts[cat] += 1
    analysis["category_coverage"] = dict(cat_counts.most_common())

    # --- Solution page link map ---
    solution_map: Counter = Counter()
    for r in all_results:
        seo = r.get("seo_audit", {})
        for link in seo.get("solution_links", []):
            # Normalize the solution page URL
            parsed = urlparse(link)
            clean = parsed.scheme + "://" + parsed.netloc + parsed.path.rstrip("/")
            solution_map[clean] += 1
    analysis["solution_page_link_map"] = dict(solution_map.most_common())
    analysis["solution_pages_low_coverage"] = {
        url: count for url, count in solution_map.items() if count <= 1
    }

    # --- AIO readiness distribution ---
    aio_dist = Counter()
    for r in all_results:
        aio = r.get("aio_audit", {})
        aio_dist[aio.get("aio_readiness", "AIO Weak")] += 1
    analysis["aio_readiness_distribution"] = dict(aio_dist)

    # --- SEO health summary ---
    severity_by_field: dict[str, Counter] = defaultdict(Counter)
    total_sev: Counter = Counter()
    for r in all_results:
        seo = r.get("seo_audit", {})
        for issue in seo.get("issues", []):
            severity_by_field[issue["field"]][issue["severity"]] += 1
            total_sev[issue["severity"]] += 1
        aio = r.get("aio_audit", {})
        for issue in aio.get("issues", []):
            total_sev[issue["severity"]] += 1

    analysis["seo_health_summary"] = {
        "total_fails": total_sev.get("FAIL", 0),
        "total_warns": total_sev.get("WARN", 0),
        "total_good": total_sev.get("GOOD", 0) + total_sev.get("EXCELLENT", 0),
        "by_field": {k: dict(v) for k, v in severity_by_field.items()},
    }

    # --- Top content gaps ---
    # Identify solution categories that need more blog coverage
    expected_solutions = [
        "Association Management", "Fundraising", "Events", "Accounting",
        "Learning", "Volunteer Management", "Certification",
        "Career Centers", "Donor Management"
    ]
    covered_categories = set(cat_counts.keys())
    gaps = []
    for sol in expected_solutions:
        count = cat_counts.get(sol, 0)
        if count <= 2:
            gaps.append({"category": sol, "post_count": count})
    analysis["content_gaps"] = sorted(gaps, key=lambda x: x["post_count"])

    return analysis


# ============================================================================
# OUTPUT GENERATION
# ============================================================================

def write_json_output(all_results: list[dict[str, Any]], site_analysis: dict[str, Any],
                      output_dir: str):
    """Write the full audit data to audit_results.json."""
    output = {
        "audit_date": datetime.now().isoformat(),
        "total_posts": len(all_results),
        "posts": all_results,
        "site_analysis": site_analysis,
    }
    path = os.path.join(output_dir, "audit_results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"  [output] JSON written to {path}")


def write_markdown_report(all_results: list[dict[str, Any]], site_analysis: dict[str, Any],
                          output_dir: str):
    """Generate the human-readable audit_report.md."""
    lines: list[str] = []

    total = len(all_results)
    avg_wc = sum(r.get("word_count", 0) for r in all_results) / max(total, 1)
    aio_dist = site_analysis.get("aio_readiness_distribution", {})
    aio_ready_pct = (aio_dist.get("AIO Ready", 0) / max(total, 1)) * 100
    health = site_analysis.get("seo_health_summary", {})

    lines.append("# SEO + AIO Audit Report")
    lines.append(f"\n**Audit Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Base URL:** momentivesoftware.com/blog")
    lines.append("")

    # --- Executive Summary ---
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- **Total Posts Audited:** {total}")
    lines.append(f"- **Average Word Count:** {avg_wc:.0f}")
    lines.append(f"- **Total SEO FAILs:** {health.get('total_fails', 0)}")
    lines.append(f"- **Total SEO WARNs:** {health.get('total_warns', 0)}")
    lines.append(f"- **AIO Ready:** {aio_dist.get('AIO Ready', 0)} posts ({aio_ready_pct:.1f}%)")
    lines.append(f"- **AIO Partial:** {aio_dist.get('AIO Partial', 0)} posts")
    lines.append(f"- **AIO Weak:** {aio_dist.get('AIO Weak', 0)} posts")
    lines.append("")

    # --- Site-Wide Findings ---
    lines.append("## Site-Wide Findings")
    lines.append("")

    # Publishing cadence
    lines.append("### Publishing Cadence (Last 12 Months)")
    lines.append("")
    lines.append("| Month | Posts Published |")
    lines.append("|-------|---------------|")
    for month, count in sorted(site_analysis.get("publishing_cadence", {}).items()):
        lines.append(f"| {month} | {count} |")
    lines.append("")

    # Category distribution
    lines.append("### Category Distribution")
    lines.append("")
    lines.append("| Category | Post Count |")
    lines.append("|----------|-----------|")
    for cat, count in sorted(site_analysis.get("category_coverage", {}).items(), key=lambda x: -x[1]):
        lines.append(f"| {cat} | {count} |")
    if not site_analysis.get("category_coverage"):
        lines.append("| *(No categories detected)* | — |")
    lines.append("")

    # Solution page coverage
    lines.append("### Solution Page Link Coverage")
    lines.append("")
    sol_map = site_analysis.get("solution_page_link_map", {})
    if sol_map:
        lines.append("| Solution Page | Blog Posts Linking |")
        lines.append("|--------------|-------------------|")
        for url, count in sorted(sol_map.items(), key=lambda x: -x[1]):
            lines.append(f"| {url} | {count} |")
    else:
        lines.append("*No solution page links detected across blog posts.*")
    lines.append("")

    # Content gaps
    gaps = site_analysis.get("content_gaps", [])
    if gaps:
        lines.append("### Content Gaps (Low/No Blog Coverage)")
        lines.append("")
        lines.append("| Category | Current Posts |")
        lines.append("|----------|--------------|")
        for g in gaps:
            lines.append(f"| {g['category']} | {g['post_count']} |")
        lines.append("")

    # --- Top 10 by AIO Score ---
    lines.append("## Top 10 Posts by AIO Score")
    lines.append("")
    sorted_by_aio = sorted(all_results, key=lambda r: r.get("aio_audit", {}).get("aio_score", 0), reverse=True)
    lines.append("| # | Post URL | AIO Score | Readiness | Word Count |")
    lines.append("|---|---------|-----------|-----------|-----------|")
    for i, r in enumerate(sorted_by_aio[:10], 1):
        aio = r.get("aio_audit", {})
        lines.append(f"| {i} | {r['url']} | {aio.get('aio_score', 0)}/7 | {aio.get('aio_readiness', 'N/A')} | {r.get('word_count', 0)} |")
    lines.append("")

    # --- Bottom 10 (most issues) ---
    lines.append("## Bottom 10 Posts (Most Issues)")
    lines.append("")

    def _issue_count(r):
        seo_issues = len(r.get("seo_audit", {}).get("issues", []))
        aio_issues = len(r.get("aio_audit", {}).get("issues", []))
        return seo_issues + aio_issues

    sorted_by_issues = sorted(all_results, key=_issue_count, reverse=True)
    lines.append("| # | Post URL | Total Issues | FAILs | WARNs |")
    lines.append("|---|---------|-------------|-------|-------|")
    for i, r in enumerate(sorted_by_issues[:10], 1):
        seo_issues = r.get("seo_audit", {}).get("issues", [])
        aio_issues = r.get("aio_audit", {}).get("issues", [])
        all_issues = seo_issues + aio_issues
        fails = sum(1 for x in all_issues if x["severity"] == "FAIL")
        warns = sum(1 for x in all_issues if x["severity"] == "WARN")
        lines.append(f"| {i} | {r['url']} | {len(all_issues)} | {fails} | {warns} |")
    lines.append("")

    # --- AIO Readiness Breakdown ---
    lines.append("## AIO Readiness Breakdown")
    lines.append("")
    lines.append("| Level | Count | % |")
    lines.append("|-------|-------|---|")
    for level in ["AIO Ready", "AIO Partial", "AIO Weak"]:
        cnt = aio_dist.get(level, 0)
        pct = (cnt / max(total, 1)) * 100
        lines.append(f"| {level} | {cnt} | {pct:.1f}% |")
    lines.append("")

    # --- Actionable Recommendations ---
    lines.append("## Actionable Recommendations")
    lines.append("")
    recommendations = _generate_recommendations(all_results, site_analysis)
    for i, rec in enumerate(recommendations, 1):
        lines.append(f"{i}. **{rec['title']}** — {rec['detail']}")
    lines.append("")

    # Write file
    path = os.path.join(output_dir, "audit_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  [output] Markdown report written to {path}")


def _generate_recommendations(all_results: list[dict[str, Any]],
                              site_analysis: dict[str, Any]) -> list[dict[str, str]]:
    """Generate at least 10 prioritized recommendations."""
    recs = []
    health = site_analysis.get("seo_health_summary", {})
    aio_dist = site_analysis.get("aio_readiness_distribution", {})
    total = len(all_results)

    # 1. Missing meta descriptions
    meta_missing = sum(
        1 for r in all_results
        if any(i["field"] == "meta_description" and i["severity"] == "FAIL"
               for i in r.get("seo_audit", {}).get("issues", []))
    )
    if meta_missing:
        recs.append({
            "title": "Add Missing Meta Descriptions",
            "detail": f"{meta_missing} posts are missing meta descriptions. Write unique, keyword-rich descriptions (120-160 chars) for each."
        })

    # 2. Add FAQ schema
    faq_content_no_schema = sum(
        1 for r in all_results
        if r.get("has_faq_content") and not r.get("has_faq_schema")
    )
    if faq_content_no_schema:
        recs.append({
            "title": "Add FAQ Schema to Posts with FAQ Content",
            "detail": f"{faq_content_no_schema} posts have FAQ-style content but no FAQ schema markup. Adding FAQPage schema will improve AIO visibility."
        })

    # 3. Fix missing alt text
    total_missing_alt = sum(
        r.get("seo_audit", {}).get("images_missing_alt", 0) for r in all_results
    )
    if total_missing_alt:
        recs.append({
            "title": "Fix Missing Image Alt Text",
            "detail": f"{total_missing_alt} images across the blog are missing alt text. Add descriptive alt attributes for accessibility and image SEO."
        })

    # 4. AIO weak posts
    weak_count = aio_dist.get("AIO Weak", 0)
    if weak_count:
        recs.append({
            "title": "Improve AIO-Weak Posts",
            "detail": f"{weak_count} posts score poorly for AI Overview optimization. Prioritize adding direct answer openings, Q&A headings, and structured data."
        })

    # 5. Internal linking gaps
    no_links = sum(
        1 for r in all_results
        if r.get("seo_audit", {}).get("internal_link_count", 0) == 0
    )
    if no_links:
        recs.append({
            "title": "Add Internal Links to Orphan Posts",
            "detail": f"{no_links} posts have zero internal links. Add contextual links to related blog posts and solution pages."
        })

    # 6. Solution page coverage
    gaps = site_analysis.get("content_gaps", [])
    if gaps:
        gap_names = ", ".join(g["category"] for g in gaps[:5])
        recs.append({
            "title": "Create Content for Underserved Solution Categories",
            "detail": f"These categories have minimal blog coverage: {gap_names}. Create targeted posts to support each solution page."
        })

    # 7. Thin content
    thin = sum(1 for r in all_results if r.get("word_count", 0) < 800)
    if thin:
        recs.append({
            "title": "Expand Thin Content",
            "detail": f"{thin} posts have fewer than 800 words. Expand with additional sections, examples, data, and Q&A content."
        })

    # 8. Content freshness
    stale = sum(
        1 for r in all_results
        if not r.get("aio_audit", {}).get("is_recently_updated", False)
        and not r.get("aio_audit", {}).get("has_year_in_title", False)
    )
    if stale:
        recs.append({
            "title": "Update Stale Content with Current Year References",
            "detail": f"{stale} posts lack freshness signals. Update titles/descriptions with '2026' where applicable and refresh publish dates."
        })

    # 9. Direct answer openings
    no_direct = sum(
        1 for r in all_results
        if not r.get("aio_audit", {}).get("has_direct_answer", False)
    )
    if no_direct:
        recs.append({
            "title": "Add Direct Answer Openings",
            "detail": f"{no_direct} posts don't lead with a direct answer. Restructure intros to answer the title's implied question in the first 1-2 sentences."
        })

    # 10. Question-format headings
    no_q = sum(
        1 for r in all_results
        if r.get("aio_audit", {}).get("question_headings_count", 0) == 0
    )
    if no_q:
        recs.append({
            "title": "Add Question-Format Subheadings",
            "detail": f"{no_q} posts have no question-style headings (What, How, Why). Add at least 3 per post to improve AIO discoverability."
        })

    # 11. Brand mentions
    no_brand = sum(
        1 for r in all_results
        if not r.get("aio_audit", {}).get("mentions_brand", False)
    )
    if no_brand:
        recs.append({
            "title": "Include Brand Entity Mentions",
            "detail": f"{no_brand} posts don't mention 'Momentive Software' by full name. Add at least one mention to strengthen brand entity signals for AI search."
        })

    # 12. Data citations
    no_data = sum(
        1 for r in all_results
        if r.get("aio_audit", {}).get("data_citations", 0) == 0
    )
    if no_data:
        recs.append({
            "title": "Add Statistical Data and Citations",
            "detail": f"{no_data} posts contain no statistical data (percentages, figures). Include relevant industry stats to boost credibility for AI citations."
        })

    # Ensure at least 10
    while len(recs) < 10:
        recs.append({
            "title": "Establish a Regular Content Audit Cadence",
            "detail": "Run this audit quarterly to track improvements and catch new regressions before they impact AIO performance."
        })

    return recs[:12]  # cap at 12


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

def run_audit(base_url: str, max_posts: int, output_dir: str):
    """Main entry point: crawl, audit, analyze, report."""
    os.makedirs(output_dir, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    # ---- Phase 1: Discover ----
    print("\n=== PHASE 1: CRAWL & DISCOVER ===\n")
    post_urls = discover_blog_post_urls(base_url, session, max_posts)

    if not post_urls:
        print("[!] No blog post URLs discovered. Exiting.")
        return

    # ---- Fetch all posts (concurrent) ----
    print(f"\n=== Fetching {len(post_urls)} posts ===\n")
    html_map: dict[str, str] = {}
    failed_urls: list[str] = []

    # Rate-limited concurrent fetching
    def _fetch(url):
        time.sleep(DELAY_BETWEEN)
        html = fetch_page(url, session)
        return url, html

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
        futures = {executor.submit(_fetch, url): url for url in post_urls}
        for future in tqdm(concurrent.futures.as_completed(futures),
                           total=len(futures), desc="Fetching posts"):
            url, html = future.result()
            if html:
                html_map[url] = html
            else:
                failed_urls.append(url)

    if failed_urls:
        print(f"\n  [warn] {len(failed_urls)} URLs failed to fetch:")
        for u in failed_urls:
            print(f"    - {u}")

    # ---- Phase 2 + 3: Audit each post ----
    print(f"\n=== PHASE 2 & 3: AUDITING {len(html_map)} POSTS ===\n")
    all_results: list[dict[str, Any]] = []

    for url in tqdm(html_map, desc="Auditing posts"):
        html = html_map[url]
        try:
            post_data = parse_blog_post(url, html)
            seo_result = audit_seo(post_data)
            aio_result = audit_aio(post_data, html)
            post_data["seo_audit"] = seo_result
            post_data["aio_audit"] = aio_result
            all_results.append(post_data)
        except Exception as exc:
            print(f"  [error] Failed to audit {url}: {exc}")
            traceback.print_exc()

    # ---- Phase 4: Site-wide analysis ----
    print(f"\n=== PHASE 4: SITE-WIDE ANALYSIS ===\n")
    site_analysis = generate_site_analysis(all_results)

    # ---- Output ----
    print("\n=== GENERATING OUTPUT ===\n")
    write_json_output(all_results, site_analysis, output_dir)
    write_markdown_report(all_results, site_analysis, output_dir)

    # ---- Terminal summary ----
    _print_terminal_summary(all_results, site_analysis)


def _print_terminal_summary(all_results: list[dict[str, Any]], site_analysis: dict[str, Any]):
    """Print a brief summary to stdout."""
    total = len(all_results)
    health = site_analysis.get("seo_health_summary", {})
    aio_dist = site_analysis.get("aio_readiness_distribution", {})

    print("\n" + "=" * 60)
    print("  AUDIT COMPLETE — SUMMARY")
    print("=" * 60)
    print(f"  Total posts audited:   {total}")
    print(f"  Total FAILs:           {health.get('total_fails', 0)}")
    print(f"  Total WARNs:           {health.get('total_warns', 0)}")
    print(f"  Total GOOD/EXCELLENT:  {health.get('total_good', 0)}")
    print()
    print("  AIO Readiness:")
    for level in ["AIO Ready", "AIO Partial", "AIO Weak"]:
        cnt = aio_dist.get(level, 0)
        pct = (cnt / max(total, 1)) * 100
        bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        print(f"    {level:<12} {cnt:>4} ({pct:5.1f}%) {bar}")
    print("=" * 60 + "\n")


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="SEO + AIO Audit Tool for blog content",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL,
        help=f"Base blog URL to crawl (default: {DEFAULT_URL})"
    )
    parser.add_argument(
        "--max-posts", type=int, default=0,
        help="Maximum number of posts to audit (0 = unlimited, default: 0)"
    )
    parser.add_argument(
        "--output-dir", default="./audit_output/",
        help="Directory for output files (default: ./audit_output/)"
    )
    args = parser.parse_args()
    run_audit(args.url, args.max_posts, args.output_dir)


if __name__ == "__main__":
    main()
