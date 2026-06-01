#!/usr/bin/env python3
"""
STEP 3 — SEO + AIO Audit of All Blog Posts
============================================
Reads:
  - audits/{slug}/{slug}_blog_urls.json   (from step 2)
  - audits/{slug}/{slug}_pages.json       (from step 1, for solution page links)

Audits every blog post concurrently (5 workers, 0.5 s delay per thread)
and writes full results to:
  - audits/{slug}/{slug}_audit_results.json

Each post record includes:
  Title, meta description, H1/H2/H3 counts, word count, schema signals,
  FAQ content/schema, structured snippets, internal blog links, solution page
  links, target keywords — plus an AIO score (0–5) and tier label.

Usage:
    python 03_audit_posts.py --url https://memgraph.com/
    python 03_audit_posts.py --url https://memgraph.com/ --max-posts 20
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

import argparse, concurrent.futures, json, os, re, time
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ── Constants ──────────────────────────────────────────────────────────────
MAX_WORKERS     = 5
REQUEST_DELAY   = 0.5
REQUEST_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_QUESTION_RE = re.compile(
    r"^(what|how|why|when|is|can|are|do|does|which|where|who|will|should)\b",
    re.IGNORECASE,
)

_BLOG_SEGMENT_RE = re.compile(
    r"/(blog|news|article|articles|post|posts|insights|resources|learn|"
    r"stories|journal|content|hub)/[^/]+/?$",
    re.IGNORECASE,
)


# ── Shared helpers ─────────────────────────────────────────────────────────

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


# ── JSON-LD helpers ────────────────────────────────────────────────────────

def get_json_ld_nodes(soup: BeautifulSoup) -> list[dict]:
    """Return every JSON-LD object on the page (flattening @graph arrays)."""
    nodes: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            nodes.extend(data.get("@graph", [data]))
        elif isinstance(data, list):
            nodes.extend(data)
    return nodes


def node_has_type(node: Any, *types: str) -> bool:
    if not isinstance(node, dict):
        return False
    t = node.get("@type", "")
    t_set = set(t if isinstance(t, list) else [t])
    return bool(t_set & set(types))


def extract_keywords(nodes: list[dict]) -> list[str]:
    """
    Pull target keywords from schema JSON-LD (BlogPosting / Article node).
    Also checks <meta name="keywords"> as fallback.
    """
    for node in nodes:
        if node_has_type(node, "BlogPosting", "Article", "NewsArticle"):
            kw = node.get("keywords", "")
            if kw and isinstance(kw, str):
                return [k.strip() for k in kw.split(",") if k.strip()]
            if isinstance(kw, list):
                return [str(k).strip() for k in kw if str(k).strip()]
    return []


# ── Content selectors ─────────────────────────────────────────────────────

def find_main_content(soup: BeautifulSoup):
    """Return the main article body element."""
    for sel in ("article", ".entry-content", ".post-content",
                ".article-content", ".blog-content", ".content-body",
                "[itemprop='articleBody']", "main"):
        el = soup.select_one(sel)
        if el:
            return el
    return soup.find("body") or soup


# ── Field extractors ───────────────────────────────────────────────────────

def extract_title(soup: BeautifulSoup) -> str:
    tag = soup.find("title")
    return tag.get_text(strip=True) if tag else ""


def extract_meta_description(soup: BeautifulSoup) -> str:
    tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    return (tag.get("content") or "").strip() if tag else ""


def extract_headings(soup: BeautifulSoup) -> dict:
    h1s = [t.get_text(strip=True) for t in soup.find_all("h1")]
    h2s = [t.get_text(strip=True) for t in soup.find_all("h2")]
    h3s = [t.get_text(strip=True) for t in soup.find_all("h3")]
    return {"h1": h1s, "h2s": h2s, "h3s": h3s}


def count_words(content_el) -> int:
    text = content_el.get_text(separator=" ", strip=True)
    return len(text.split())


def has_snippets(content_el) -> bool:
    """
    True if the content has structured snippet patterns:
    - <ol> with 3+ items (numbered step lists)
    - <table> (comparison / data tables)
    - <strong> as first child of <p> (bolded lead sentences)
    - <ul> with 4+ items (proper list, not just nav)
    """
    for ol in content_el.find_all("ol"):
        if len(ol.find_all("li", recursive=False)) >= 3:
            return True
    if content_el.find("table"):
        return True
    for ul in content_el.find_all("ul"):
        if len(ul.find_all("li", recursive=False)) >= 4:
            return True
    for p in content_el.find_all("p"):
        children = [c for c in p.children if str(c).strip()]
        if children and getattr(children[0], "name", None) == "strong":
            return True
    return False


def has_faq_content(soup: BeautifulSoup) -> bool:
    """
    True if:
    - 2+ question-style H2/H3 headings, OR
    - Any element with class/id containing 'faq'
    """
    q_count = 0
    for tag in soup.find_all(["h2", "h3"]):
        if _QUESTION_RE.match(tag.get_text(strip=True)):
            q_count += 1
            if q_count >= 2:
                return True
    for el in soup.find_all(True):
        for attr in ("class", "id"):
            val = el.get(attr, "")
            val_str = " ".join(val) if isinstance(val, list) else str(val)
            if "faq" in val_str.lower():
                return True
    return False


def count_question_headings(soup: BeautifulSoup) -> int:
    return sum(1 for t in soup.find_all(["h2", "h3"])
               if _QUESTION_RE.match(t.get_text(strip=True)))


def has_definition_sentences(content_el) -> int:
    """Count definition-style sentences: '[Term] is …' or '[Term] refers to …'"""
    text = content_el.get_text(separator="\n", strip=True)
    pattern = re.compile(r"\b\w[\w\s]+?\b (is|are|refers to|means|defined as)\b",
                         re.IGNORECASE)
    return len(pattern.findall(text))


def count_data_citations(content_el) -> int:
    """Count sentences with %, numbers + million/billion/percent."""
    text = content_el.get_text(separator=" ", strip=True)
    count = len(re.findall(r"\d[\d,.]*\s*%", text))
    count += len(re.findall(r"\d[\d,.]*\s*(million|billion|trillion|percent)\b",
                             text, re.IGNORECASE))
    return count


def has_faq_schema(nodes: list[dict]) -> bool:
    return any(node_has_type(n, "FAQPage", "Question") for n in nodes)


def has_article_schema(nodes: list[dict]) -> bool:
    return any(node_has_type(n, "BlogPosting", "Article", "NewsArticle")
               for n in nodes)


def has_breadcrumb_schema(nodes: list[dict]) -> bool:
    return any(node_has_type(n, "BreadcrumbList") for n in nodes)


def has_howto_schema(nodes: list[dict]) -> bool:
    return any(node_has_type(n, "HowTo") for n in nodes)


def extract_canonical(soup: BeautifulSoup) -> str:
    tag = soup.find("link", rel="canonical")
    return tag.get("href", "").strip() if tag else ""


def extract_og_tags(soup: BeautifulSoup) -> dict:
    og: dict[str, str] = {}
    for meta in soup.find_all("meta", property=re.compile(r"^og:", re.I)):
        prop = meta.get("property", "").lower()
        content = meta.get("content", "")
        og[prop] = content
    return og


def find_solution_page_links(soup: BeautifulSoup,
                              solution_urls: list[str]) -> list[str]:
    """
    Return which solution pages (from the pages.json) appear as links on this post.
    Normalises trailing slashes for comparison.
    """
    sol_norm = {u.rstrip("/").lower(): u for u in solution_urls}
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].rstrip("/").lower()
        if href in sol_norm and sol_norm[href] not in found:
            found.append(sol_norm[href])
    return found


def find_internal_blog_links(soup: BeautifulSoup, current_url: str,
                              base_domain: str) -> list[dict]:
    """
    Return all links within the main content area that point to other
    blog posts on the same domain.
    """
    content = find_main_content(soup)
    current_norm = current_url.rstrip("/")
    seen: set[str] = set()
    results: list[dict] = []

    for a in content.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("/"):
            href = f"https://{base_domain}{href}"
        elif not href.startswith("http"):
            href = urljoin(current_url, href)
        norm = href.rstrip("/")
        if norm == current_norm or norm in seen:
            continue
        parsed = urlparse(href)
        if parsed.netloc.lower().replace("www.", "") != base_domain:
            continue
        if _BLOG_SEGMENT_RE.search(parsed.path):
            seen.add(norm)
            results.append({
                "url": href if href.endswith("/") else href + "/",
                "anchor_text": a.get_text(strip=True)[:120],
            })
    return results


def has_direct_answer_opening(content_el) -> bool:
    """
    Heuristic: does the post open with a direct answer?
    Check first 200 words. Flag as missing if the first
    non-empty paragraph is a question or is very short.
    """
    text = content_el.get_text(separator="\n", strip=True)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    first_para = lines[0]
    # Missing if first line ends with "?" (question, not answer)
    if first_para.endswith("?"):
        return False
    # Missing if very short (< 15 words) — likely a vague hook
    if len(first_para.split()) < 15:
        return False
    return True


# ── AIO Score ──────────────────────────────────────────────────────────────

def compute_aio_score(record: dict) -> int:
    """
    0–5 score based on five AIO readiness signals:
    +1  FAQ schema present
    +1  FAQ content / Q&A headings present
    +1  Structured snippets (lists, tables, bold leads)
    +1  Target keywords defined in schema
    +1  Word count >= 1200 (substantive content)
    """
    score = 0
    if record.get("has_faq_schema"):         score += 1
    if record.get("has_faq_content"):        score += 1
    if record.get("has_snippets"):           score += 1
    if record.get("target_keywords"):        score += 1
    if (record.get("word_count") or 0) >= 1200: score += 1
    return score


# ── Per-post audit ─────────────────────────────────────────────────────────

def audit_url(url: str, session: requests.Session,
              solution_urls: list[str], base_domain: str) -> dict:
    """
    Fetch and fully audit a single blog post URL.
    Returns the result dict (with error key set on failure).
    """
    time.sleep(REQUEST_DELAY)

    blank: dict = {
        "url": url, "title": None, "meta_description": None,
        "canonical": None, "og_tags": None, "target_keywords": None,
        "h1": None, "h1_count": None, "h2_count": None, "h3_count": None,
        "question_heading_count": None, "word_count": None,
        "has_snippets": None, "has_faq_content": None, "has_faq_schema": None,
        "has_article_schema": None, "has_breadcrumb_schema": None,
        "has_howto_schema": None, "has_direct_answer": None,
        "definition_sentence_count": None, "data_citation_count": None,
        "solution_page_links": None, "internal_blog_links": None,
        "internal_blog_links_count": None,
        "aio_score": None, "aio_tier": None,
        "wc_tier": None, "issues": None, "error": None,
    }

    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as exc:
        blank["error"] = f"HTTP error: {exc}"
        return blank

    try:
        soup    = BeautifulSoup(html, "lxml")
        nodes   = get_json_ld_nodes(soup)
        content = find_main_content(soup)
        headings = extract_headings(soup)
        wc       = count_words(content)

        result: dict = {
            "url":                   url,
            "title":                 extract_title(soup),
            "meta_description":      extract_meta_description(soup),
            "canonical":             extract_canonical(soup),
            "og_tags":               extract_og_tags(soup),
            "target_keywords":       extract_keywords(nodes),
            "h1":                    headings["h1"][0] if headings["h1"] else "",
            "h1_count":              len(headings["h1"]),
            "h2_count":              len(headings["h2s"]),
            "h3_count":              len(headings["h3s"]),
            "h2s":                   headings["h2s"][:10],  # first 10 H2s
            "question_heading_count":count_question_headings(soup),
            "word_count":            wc,
            "wc_tier":               ("Thin" if wc < 800 else
                                      "OK"   if wc < 1500 else "Strong"),
            "has_snippets":          has_snippets(content),
            "has_faq_content":       has_faq_content(soup),
            "has_faq_schema":        has_faq_schema(nodes),
            "has_article_schema":    has_article_schema(nodes),
            "has_breadcrumb_schema": has_breadcrumb_schema(nodes),
            "has_howto_schema":      has_howto_schema(nodes),
            "has_direct_answer":     has_direct_answer_opening(content),
            "definition_sentence_count": has_definition_sentences(content),
            "data_citation_count":   count_data_citations(content),
            "solution_page_links":   find_solution_page_links(soup, solution_urls),
            "internal_blog_links":   find_internal_blog_links(soup, url, base_domain),
            "error":                 None,
        }
        result["internal_blog_links_count"] = len(result["internal_blog_links"])

        # AIO scoring
        aio = compute_aio_score(result)
        result["aio_score"] = aio
        result["aio_tier"]  = ("AIO Ready"   if aio >= 4 else
                                "AIO Partial" if aio >= 2 else "AIO Weak")

        # Issue flags (human-readable list of gaps)
        issues: list[str] = []
        if result["has_faq_content"] and not result["has_faq_schema"]:
            issues.append("FAQ schema missing")
        if not result["has_snippets"]:
            issues.append("No structured snippets")
        if not result["target_keywords"]:
            issues.append("No target keywords")
        if wc < 800:
            issues.append(f"Thin content ({wc}w)")
        if not result["internal_blog_links"]:
            issues.append("No internal blog links")
        if not result["has_direct_answer"]:
            issues.append("No direct answer opening")
        if result["question_heading_count"] == 0:
            issues.append("No question-format headings")
        result["issues"] = issues

        # Clean up title (strip common site-name suffixes)
        for suffix in [" | ", " - ", " – ", " — "]:
            if suffix in result["title"]:
                # Keep everything before the last separator if it's long enough
                parts = result["title"].rsplit(suffix, 1)
                if len(parts[0]) >= 20:
                    result["title"] = parts[0]
                break

        return result

    except Exception as exc:
        blank["error"] = f"Parse error: {exc}"
        return blank


# ── Concurrent batch audit ─────────────────────────────────────────────────

def audit_all(urls: list[str], solution_urls: list[str],
              base_domain: str) -> list[dict]:
    """Audit all URLs concurrently, printing progress as each completes."""
    total    = len(urls)
    results  = [None] * total
    done     = 0
    session  = make_session()

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fut_map = {
            ex.submit(audit_url, url, session, solution_urls, base_domain): i
            for i, url in enumerate(urls)
        }
        for fut in concurrent.futures.as_completed(fut_map):
            i    = fut_map[fut]
            done += 1
            try:
                res = fut.result()
            except Exception as exc:
                res = {"url": urls[i], "error": str(exc),
                       **{k: None for k in (
                           "title","meta_description","target_keywords",
                           "word_count","wc_tier","has_snippets","has_faq_content",
                           "has_faq_schema","has_article_schema","has_breadcrumb_schema",
                           "has_howto_schema","solution_page_links","internal_blog_links",
                           "internal_blog_links_count","aio_score","aio_tier","issues",
                       )}}
            results[i] = res
            status = "✓" if not res.get("error") else "✗"
            print(f"[{done:>4}/{total}] {status} {urls[i]}")

    return results


# ── Summary ────────────────────────────────────────────────────────────────

def print_summary(results: list[dict]):
    valid   = [r for r in results if not r.get("error")]
    errors  = len(results) - len(valid)
    total   = len(valid)
    if total == 0:
        print("[3/audit] No valid results to summarise.")
        return

    faq_gap = sum(1 for r in valid if r.get("has_faq_content") and not r.get("has_faq_schema"))
    no_int  = sum(1 for r in valid if not (r.get("internal_blog_links") or []))
    no_kw   = sum(1 for r in valid if not r.get("target_keywords"))
    thin    = sum(1 for r in valid if r.get("wc_tier") == "Thin")
    ready   = sum(1 for r in valid if r.get("aio_tier") == "AIO Ready")
    partial = sum(1 for r in valid if r.get("aio_tier") == "AIO Partial")
    weak    = sum(1 for r in valid if r.get("aio_tier") == "AIO Weak")
    wcs     = [r["word_count"] for r in valid if isinstance(r.get("word_count"), int)]
    avg_wc  = round(sum(wcs) / len(wcs)) if wcs else 0

    print()
    print("═" * 52)
    print("  AUDIT SUMMARY")
    print("═" * 52)
    print(f"  Posts audited          : {total}")
    print(f"  Fetch/parse errors     : {errors}")
    print(f"  Average word count     : {avg_wc:,}")
    print(f"  Thin content (<800w)   : {thin}  ({thin/total*100:.1f}%)")
    print(f"  No target keywords     : {no_kw}  ({no_kw/total*100:.1f}%)")
    print(f"  FAQ content, no schema : {faq_gap}  ({faq_gap/total*100:.1f}%)")
    print(f"  No internal blog links : {no_int}  ({no_int/total*100:.1f}%)")
    print(f"  AIO Ready  (4–5/5)     : {ready}  ({ready/total*100:.1f}%)")
    print(f"  AIO Partial (2–3/5)    : {partial}  ({partial/total*100:.1f}%)")
    print(f"  AIO Weak   (0–1/5)     : {weak}  ({weak/total*100:.1f}%)")
    print("═" * 52)
    print()


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SEO + AIO audit of all blog posts for a company website."
    )
    parser.add_argument("--url", required=True,
                        help="Base URL (e.g. https://memgraph.com/)")
    parser.add_argument("--max-posts", type=int, default=0,
                        help="Cap number of posts to audit (0 = unlimited, good for testing)")
    args = parser.parse_args()

    base_url    = args.url.rstrip("/") + "/"
    slug        = url_to_slug(base_url)
    base_domain = urlparse(base_url).netloc.lower().replace("www.", "")
    output_dir  = os.path.join("audits", slug)
    os.makedirs(output_dir, exist_ok=True)

    # Load blog URLs (required)
    blog_url_path = os.path.join(output_dir, f"{slug}_blog_urls.json")
    if not os.path.exists(blog_url_path):
        print(f"[error] Blog URL file not found: {blog_url_path}")
        print(f"[error] Run step 2 first: python 02_discover_blog_posts.py --url {args.url}")
        sys.exit(1)
    with open(blog_url_path, encoding="utf-8") as f:
        blog_urls: list[str] = json.load(f)

    # Load solution pages (optional — use empty list if step 1 not run)
    solution_urls: list[str] = []
    pages_path = os.path.join(output_dir, f"{slug}_pages.json")
    if os.path.exists(pages_path):
        with open(pages_path, encoding="utf-8") as f:
            pages_data = json.load(f)
        for cat_pages in pages_data.get("categories", {}).values():
            solution_urls.extend(p["url"] for p in cat_pages)
        print(f"[3/audit] Loaded {len(solution_urls)} solution/product pages for link checking.")
    else:
        print("[3/audit] No pages.json found — solution link checking will be skipped.")
        print(f"[3/audit] Run step 1 first for full results: python 01_discover_pages.py --url {args.url}")

    if args.max_posts > 0:
        blog_urls = blog_urls[:args.max_posts]
        print(f"[3/audit] Capped to {args.max_posts} posts (--max-posts flag).")

    print(f"[3/audit] Auditing {len(blog_urls)} posts "
          f"({MAX_WORKERS} workers, {REQUEST_DELAY}s delay) …\n")

    results = audit_all(blog_urls, solution_urls, base_domain)

    out_path = os.path.join(output_dir, f"{slug}_audit_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n[3/audit] ✓ Results written to: {out_path}")
    print_summary(results)
    print(f"[3/audit] Next step: python 04_generate_reports.py --url {args.url}")


if __name__ == "__main__":
    main()
