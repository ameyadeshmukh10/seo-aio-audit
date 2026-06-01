#!/usr/bin/env python3
"""
run_audit.py — Full Pipeline Runner
=====================================
Runs all four audit steps in sequence for a given company URL.

# Full pipeline + publish to HubSpot
python3 run_audit.py --url https://example.com/ --publish

# Full pipeline + upload as draft only
python3 run_audit.py --url https://example.com/ --publish-draft

# Just publish an existing dashboard (step 5 standalone)
python3 05_publish_hubspot.py --url https://example.com/ --publish

# Custom page slug
python3 05_publish_hubspot.py --url https://example.com/ --slug-override my-report


Usage:
    python3 run_audit.py --url https://gotransverse.com/
    python run_audit.py --url https://memgraph.com/ --max-posts 20
    python run_audit.py --url https://memgraph.com/ --skip-pages   # skip step 1
    python run_audit.py --url https://memgraph.com/ --skip-discover # skip step 2

Steps:
  1  →  01_discover_pages.py            Find product/solutions/services pages
  2  →  02_discover_blog_posts.py       Collect all blog post URLs
  3  →  03_audit_posts.py               SEO + AIO audit every post
  4  →  04_generate_reports.py          Generate dashboard + takeaways HTML
  5  →  05_publish_hubspot.py           Publish dashboard to HubSpot (optional)
  6  →  06_update_hubspot_contact.py    Write audit URL to contact property
                                        seo_aio_geo_audit (requires --email)

All output lands in:
  audits/{slug}/
    {slug}_pages.json
    {slug}_blog_urls.json
    {slug}_audit_results.json
    {slug}_dashboard.html
    {slug}_key_takeaways.html
"""

import argparse
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlparse


def url_to_slug(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return re.sub(r"[.\-]+", "_", domain)


def url_to_company_name(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    name   = domain.split(".")[0]
    return name.replace("-", " ").replace("_", " ").title()


def run_step(label, script, extra_args):
    """
    Run a pipeline script as a subprocess.
    Returns True if successful, False if it failed.
    """
    cmd = [sys.executable, script] + extra_args
    print()
    print("─" * 60)
    print(f"  {label}")
    print(f"  > {' '.join(cmd)}")
    print("─" * 60)
    t0 = time.time()
    result = subprocess.run(cmd, check=False)
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"\n[pipeline] ✗ Step failed (exit code {result.returncode}) — "
              f"elapsed {elapsed:.1f}s")
        return False
    print(f"\n[pipeline] ✓ Done in {elapsed:.1f}s")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Run the full blog SEO + AIO audit pipeline for a company website.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_audit.py --url https://memgraph.com/
  python run_audit.py --url https://hubspot.com/ --max-posts 30
  python run_audit.py --url https://stripe.com/ --skip-pages
  python run_audit.py --url https://notion.so/ --skip-pages --skip-discover
""",
    )
    parser.add_argument("--url", required=True,
                        help="Company website URL (e.g. https://memgraph.com/)")
    parser.add_argument("--max-posts", type=int, default=0,
                        help="Limit posts audited in step 3 (0 = unlimited)")
    parser.add_argument("--skip-pages", action="store_true",
                        help="Skip step 1 (page discovery) — use existing pages.json if present")
    parser.add_argument("--skip-discover", action="store_true",
                        help="Skip step 2 (blog URL discovery) — use existing blog_urls.json")
    parser.add_argument("--skip-audit", action="store_true",
                        help="Skip step 3 (post audit) — use existing audit_results.json")
    parser.add_argument("--publish", action="store_true",
                        help="Publish dashboard to HubSpot as a landing page (step 5)")
    parser.add_argument("--publish-draft", action="store_true",
                        help="Upload dashboard to HubSpot as a draft (step 5, no publish)")
    parser.add_argument("--email", action="append", default=[],
                        help="Contact email to update in step 6 (repeat for multiple). "
                             "Requires --publish.")
    args = parser.parse_args()

    url          = args.url.rstrip("/") + "/"
    slug         = url_to_slug(url)
    company_name = url_to_company_name(url)
    output_dir   = os.path.join("audits", slug)

    # Verify scripts exist alongside this runner
    base_dir  = os.path.dirname(os.path.abspath(__file__))
    required_scripts = ["01_discover_pages.py", "02_discover_blog_posts.py",
                        "03_audit_posts.py", "04_generate_reports.py"]
    if args.publish or args.publish_draft:
        required_scripts.append("05_publish_hubspot.py")
    if args.email and args.publish:
        required_scripts.append("06_update_hubspot_contact.py")
    for script in required_scripts:
        if not os.path.exists(os.path.join(base_dir, script)):
            print(f"[error] Required script not found: {script}")
            print(f"[error] Make sure all scripts are in the same directory as run_audit.py")
            sys.exit(1)

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print(f"║  Blog SEO + AIO Audit Pipeline")
    print(f"║  Company  : {company_name}")
    print(f"║  URL      : {url}")
    print(f"║  Slug     : {slug}")
    print(f"║  Output   : {output_dir}/")
    print("╚══════════════════════════════════════════════════════════╝")

    os.makedirs(output_dir, exist_ok=True)
    pipeline_start = time.time()
    steps_run      = 0
    steps_ok       = 0

    # ── Step 1: Discover pages ─────────────────────────────────────────────
    if args.skip_pages:
        pages_path = os.path.join(output_dir, f"{slug}_pages.json")
        if os.path.exists(pages_path):
            print(f"\n[pipeline] Skipping step 1 — using existing {pages_path}")
        else:
            print(f"\n[pipeline] Warning: --skip-pages set but {pages_path} not found.")
            print(f"[pipeline] Solution page link-checking will be limited in step 3.")
    else:
        steps_run += 1
        ok = run_step(
            "STEP 1 — Discover Product / Solutions Pages",
            os.path.join(base_dir, "01_discover_pages.py"),
            ["--url", url],
        )
        if ok:
            steps_ok += 1

    # ── Step 2: Discover blog posts ────────────────────────────────────────
    if args.skip_discover:
        blog_path = os.path.join(output_dir, f"{slug}_blog_urls.json")
        if os.path.exists(blog_path):
            print(f"\n[pipeline] Skipping step 2 — using existing {blog_path}")
        else:
            print(f"\n[pipeline] Error: --skip-discover set but {blog_path} not found.")
            print(f"[pipeline] Cannot proceed without blog URLs. Remove --skip-discover.")
            sys.exit(1)
    else:
        steps_run += 1
        ok = run_step(
            "STEP 2 — Discover All Blog Post URLs",
            os.path.join(base_dir, "02_discover_blog_posts.py"),
            ["--url", url],
        )
        if ok:
            steps_ok += 1
        else:
            print("\n[pipeline] Step 2 failed — cannot continue without blog URLs.")
            sys.exit(1)

    # ── Step 3: Audit posts ────────────────────────────────────────────────
    if args.skip_audit:
        results_path = os.path.join(output_dir, f"{slug}_audit_results.json")
        if os.path.exists(results_path):
            print(f"\n[pipeline] Skipping step 3 — using existing {results_path}")
        else:
            print(f"\n[pipeline] Error: --skip-audit set but {results_path} not found.")
            sys.exit(1)
    else:
        extra = []
        if args.max_posts > 0:
            extra = ["--max-posts", str(args.max_posts)]
        steps_run += 1
        ok = run_step(
            "STEP 3 — SEO + AIO Audit of All Blog Posts",
            os.path.join(base_dir, "03_audit_posts.py"),
            ["--url", url] + extra,
        )
        if ok:
            steps_ok += 1
        else:
            print("\n[pipeline] Step 3 failed — cannot generate reports without audit data.")
            sys.exit(1)

    # ── Step 4: Generate reports ───────────────────────────────────────────
    steps_run += 1
    ok = run_step(
        "STEP 4 — Generate Dashboard + Key Takeaways",
        os.path.join(base_dir, "04_generate_reports.py"),
        ["--url", url],
    )
    if ok:
        steps_ok += 1

    # ── Step 5: Publish to HubSpot (optional) ────────────────────────────
    step5_ok = False
    if args.publish or args.publish_draft:
        extra = ["--url", url]
        if args.publish:
            extra.append("--publish")
        steps_run += 1
        step5_ok = run_step(
            "STEP 5 — Publish Dashboard to HubSpot",
            os.path.join(base_dir, "05_publish_hubspot.py"),
            extra,
        )
        if step5_ok:
            steps_ok += 1

    # ── Step 6: Update HubSpot contact property (optional) ──────────────────
    # Runs only when emails were supplied AND step 5 actually published the page.
    if args.email and args.publish and step5_ok:
        import json
        hubspot_meta_path = os.path.join(output_dir, f"{slug}_hubspot.json")
        published_url = ""
        if os.path.exists(hubspot_meta_path):
            with open(hubspot_meta_path) as f:
                published_url = (json.load(f).get("page_url") or "").strip()
        if not published_url:
            print("\n[pipeline] Step 6 skipped — no published URL found in step 5 output.")
        else:
            email_args = []
            for e in args.email:
                if e and e.strip():
                    email_args.extend(["--email", e.strip()])
            steps_run += 1
            ok = run_step(
                "STEP 6 — Update HubSpot Contact Property",
                os.path.join(base_dir, "06_update_hubspot_contact.py"),
                email_args + ["--audit-url", published_url],
            )
            if ok:
                steps_ok += 1

    # ── Summary ────────────────────────────────────────────────────────────
    total_elapsed = time.time() - pipeline_start
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print(f"║  PIPELINE COMPLETE")
    print(f"║  Steps: {steps_ok}/{steps_run} succeeded · {total_elapsed:.1f}s total")
    print(f"║")
    print(f"║  Output directory: {os.path.abspath(output_dir)}/")
    print(f"║")
    output_files = [
        f"{slug}_pages.json",
        f"{slug}_blog_urls.json",
        f"{slug}_audit_results.json",
        f"{slug}_dashboard.html",
        f"{slug}_key_takeaways.html",
    ]
    if args.publish or args.publish_draft:
        output_files.append(f"{slug}_hubspot.json")
    for fname in output_files:
        path = os.path.join(output_dir, fname)
        exists = "✓" if os.path.exists(path) else "✗"
        size   = f"({os.path.getsize(path):,} bytes)" if os.path.exists(path) else ""
        print(f"║  {exists}  {fname:<35} {size}")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    # Print published URL for batch runner parsing
    if args.publish or args.publish_draft:
        import json
        hubspot_meta_path = os.path.join(output_dir, f"{slug}_hubspot.json")
        if os.path.exists(hubspot_meta_path):
            with open(hubspot_meta_path) as f:
                meta = json.load(f)
            page_url = meta.get("page_url", "")
            if page_url:
                print(f"PUBLISHED_URL: {page_url}")


if __name__ == "__main__":
    main()
