#!/usr/bin/env python3
"""
batch_audit_runner.py — Process a contacts CSV one row at a time
=================================================================
Loads contacts.csv, finds rows where auditurl is blank, and runs
run_audit.py --url <company_url> --publish for each one sequentially.

Saves progress after every single row. Restarts skip already-processed rows.

Usage:
    python3 batch_audit_runner.py
    python3 batch_audit_runner.py --csv my_contacts.csv --delay 60
    python3 batch_audit_runner.py --dry-run
"""

import csv
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

# ── Configuration ─────────────────────────────────────────────────────────

CSV_FILE = "contacts.csv"
COMPANY_URL_COLUMN = "company_url"
AUDIT_URL_COLUMN = "auditurl"
DELAY_SECONDS = 0          # stagger between launching workers (0 = none)
MAX_WORKERS = 5            # parallel audit subprocesses
LOG_FILE = "audit_run.log"
AUDIT_TIMEOUT = 300  # 5 minutes max per audit

# ── Globals ───────────────────────────────────────────────────────────────

_shutdown = False


def handle_sigint(signum, frame):
    global _shutdown
    _shutdown = True
    print("\n\n[batch] Ctrl+C received — finishing current row before exiting …")


signal.signal(signal.SIGINT, handle_sigint)


# ── Helpers ───────────────────────────────────────────────────────────────

def setup_logging(log_file: str) -> logging.Logger:
    logger = logging.getLogger("batch_audit")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s",
                                           datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


def is_valid_url(url: str) -> bool:
    """Check if a string is a plausible company URL."""
    if not url or not url.strip():
        return False
    url = url.strip()
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            parsed = urlparse("https://" + url)
        return bool(parsed.netloc) and "." in parsed.netloc
    except Exception:
        return False


def normalize_url(url: str) -> str:
    """Ensure URL has a scheme and trailing slash."""
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/") + "/"


def extract_company_name_from_row(row: dict) -> str:
    """Get a display name for the row."""
    return (row.get("company_name") or "").strip() or "Unknown"


def extract_domain(url: str) -> str:
    """Extract bare domain from a URL."""
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        return parsed.netloc.lower().replace("www.", "")
    except Exception:
        return url


def parse_published_url(stdout: str) -> str:
    """
    Parse the published HubSpot URL from run_audit.py stdout.
    Looks for lines containing https:// — takes the last one found,
    which is typically the final published page URL.
    Also checks for the specific format from 05_publish_hubspot.py:
      URL      : https://...
    """
    if not stdout:
        return ""

    # First try the PUBLISHED_URL line from run_audit.py
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("PUBLISHED_URL:"):
            match = re.search(r'(https?://\S+)', line)
            if match:
                return match.group(1)

    # Try the "URL" line from 05_publish_hubspot.py
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("URL") and ":" in line:
            match = re.search(r'(https?://\S+)', line)
            if match:
                return match.group(1)

    # Fallback: find the last https:// URL in output
    urls = re.findall(r'(https?://\S+)', stdout)
    if urls:
        # Filter to likely HubSpot page URLs (not CDN/API URLs)
        page_urls = [u for u in urls if "hubspot" in u.lower()
                     or "everworker" in u.lower()
                     or "/seo-audit-" in u.lower()]
        if page_urls:
            return page_urls[-1].rstrip(")")
        return urls[-1].rstrip(")")

    return ""


def load_csv(csv_file: str) -> tuple[list[str], list[dict]]:
    """Load CSV and return (fieldnames, rows)."""
    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    return fieldnames, rows


def save_csv(csv_file: str, fieldnames: list[str], rows: list[dict]):
    """Write rows back to CSV, preserving field order."""
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Batch-process SEO audits for a contacts CSV.",
    )
    parser.add_argument("--csv", default=CSV_FILE,
                        help=f"Path to contacts CSV (default: {CSV_FILE})")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"Parallel audit subprocesses (default: {MAX_WORKERS})")
    parser.add_argument("--delay", type=int, default=DELAY_SECONDS,
                        help=f"Seconds between launching workers (default: {DELAY_SECONDS})")
    parser.add_argument("--timeout", type=int, default=AUDIT_TIMEOUT,
                        help=f"Max seconds per audit subprocess (default: {AUDIT_TIMEOUT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be processed without running audits")
    parser.add_argument("--max-posts", type=int, default=0,
                        help="Limit posts audited per company (0 = unlimited)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max number of rows to process in this run (0 = all)")
    args = parser.parse_args()

    csv_file = args.csv
    delay = args.delay
    timeout = args.timeout
    workers = max(1, args.workers)

    if not os.path.exists(csv_file):
        print(f"[error] CSV not found: {csv_file}")
        print(f"[error] Run fetch_hubspot_contacts.py first, or specify --csv path")
        sys.exit(1)

    # Verify run_audit.py exists
    base_dir = os.path.dirname(os.path.abspath(__file__))
    audit_script = os.path.join(base_dir, "run_audit.py")
    if not os.path.exists(audit_script):
        print(f"[error] run_audit.py not found at {audit_script}")
        sys.exit(1)

    logger = setup_logging(LOG_FILE)

    # Load CSV
    fieldnames, rows = load_csv(csv_file)

    # Ensure required columns exist
    if COMPANY_URL_COLUMN not in fieldnames:
        print(f"[error] Column '{COMPANY_URL_COLUMN}' not found in CSV.")
        print(f"[error] Available columns: {', '.join(fieldnames)}")
        sys.exit(1)

    if AUDIT_URL_COLUMN not in fieldnames:
        fieldnames.append(AUDIT_URL_COLUMN)
        for row in rows:
            row.setdefault(AUDIT_URL_COLUMN, "")

    total = len(rows)
    to_process = [i for i, r in enumerate(rows)
                  if not (r.get(AUDIT_URL_COLUMN) or "").strip()]
    already_done = total - len(to_process)

    print()
    print("━" * 60)
    print(f"  Batch Audit Runner")
    print(f"  CSV         : {csv_file}")
    print(f"  Total rows  : {total}")
    print(f"  Already done: {already_done}")
    print(f"  To process  : {len(to_process)}")
    print(f"  Workers     : {workers} parallel")
    print(f"  Stagger     : {delay}s between worker launches")
    print(f"  Timeout     : {timeout}s per audit")
    if args.dry_run:
        print(f"  Mode        : DRY RUN (no audits will be executed)")
    print("━" * 60)
    print()

    # Apply --limit
    if args.limit > 0 and len(to_process) > args.limit:
        to_process = to_process[:args.limit]
        print(f"  Limited to : {args.limit} rows")

    if not to_process:
        print("[batch] Nothing to process — all rows already have audit URLs.")
        return

    if args.dry_run:
        for idx in to_process:
            row = rows[idx]
            company = extract_company_name_from_row(row)
            url = (row.get(COMPANY_URL_COLUMN) or "").strip()
            valid = is_valid_url(url)
            status = "VALID" if valid else "SKIP (invalid URL)"
            print(f"  [{idx + 1:>4} / {total}] {company:<30} {url or '(empty)':<40} {status}")
        print(f"\n[dry-run] {len(to_process)} rows would be processed.")
        return

    # ── Phase 1: synchronously mark invalid URLs, group valid rows by domain ──
    skip_count = 0
    domain_to_rows: dict[str, list[int]] = {}  # domain -> list of row indices

    for idx in to_process:
        row = rows[idx]
        raw_url = (row.get(COMPANY_URL_COLUMN) or "").strip()
        company = extract_company_name_from_row(row)
        if not is_valid_url(raw_url):
            row[AUDIT_URL_COLUMN] = "SKIP"
            logger.info(f"ROW {idx + 1} | {company} | {raw_url or '(empty)'} | SKIP | invalid or empty URL")
            skip_count += 1
            continue
        domain = extract_domain(raw_url)
        domain_to_rows.setdefault(domain, []).append(idx)

    save_csv(csv_file, fieldnames, rows)

    if not domain_to_rows:
        print(f"[batch] {skip_count} rows marked SKIP. No valid URLs to audit.")
        return

    domain_jobs = list(domain_to_rows.items())
    total_jobs = len(domain_jobs)
    fanout_total = sum(len(v) for v in domain_to_rows.values())
    print(f"[batch] {total_jobs} unique domains across {fanout_total} rows "
          f"({skip_count} skipped). Launching {workers} parallel workers …\n")

    # ── Phase 2: parallel audits ─────────────────────────────────────────────
    csv_lock = threading.Lock()
    print_lock = threading.Lock()
    success_count = 0
    error_count = 0

    def run_audit_job(job_seq: int, domain: str, row_indices: list) -> tuple:
        """Run one audit subprocess for one unique domain; fan result out to all rows."""
        first_row = rows[row_indices[0]]
        company = extract_company_name_from_row(first_row)
        raw_url = (first_row.get(COMPANY_URL_COLUMN) or "").strip()
        url = normalize_url(raw_url)
        label = f"[{job_seq:>4}/{total_jobs}] {company} ({domain})"

        with print_lock:
            print(f"{label} — starting")

        if _shutdown:
            return (domain, "SHUTDOWN", "skip")

        cmd = [sys.executable, audit_script, "--url", url, "--publish"]
        if args.max_posts > 0:
            cmd.extend(["--max-posts", str(args.max_posts)])
        # Forward each contact's email so step 6 can update their property
        for ridx in row_indices:
            email = (rows[ridx].get("email") or "").strip()
            if email:
                cmd.extend(["--email", email])

        audit_url = "ERROR"
        status = "error"
        log_detail = ""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=base_dir,
            )
            if result.returncode != 0:
                stderr_snippet = (result.stderr or "")[:200].replace("\n", " ")
                audit_url = "ERROR"
                log_detail = f"exit code {result.returncode}: {stderr_snippet}"
            else:
                parsed = parse_published_url(result.stdout)
                if parsed:
                    audit_url = parsed
                    status = "success"
                    log_detail = parsed
                else:
                    audit_url = "ERROR:NO_URL"
                    log_detail = "no URL found in stdout"
        except subprocess.TimeoutExpired:
            audit_url = "ERROR:TIMEOUT"
            log_detail = f"exceeded {timeout}s"
        except Exception as e:
            audit_url = "ERROR"
            log_detail = str(e)

        # Fan result out to every row sharing this domain, then save CSV
        with csv_lock:
            for ridx in row_indices:
                rows[ridx][AUDIT_URL_COLUMN] = audit_url
            save_csv(csv_file, fieldnames, rows)

        marker = "✓" if status == "success" else "✗"
        fanout = f" (+{len(row_indices) - 1} rows)" if len(row_indices) > 1 else ""
        with print_lock:
            print(f"{label} {marker} {audit_url}{fanout}")
        logger.info(f"DOMAIN {domain} | {company} | {audit_url.upper() if status != 'success' else 'SUCCESS'} | "
                    f"{log_detail} | rows={row_indices}")
        return (domain, audit_url, status)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = []
        for i, (domain, row_indices) in enumerate(domain_jobs):
            if _shutdown:
                break
            if delay > 0 and i > 0:
                time.sleep(delay)
            futures.append(executor.submit(run_audit_job, i + 1, domain, row_indices))

        for fut in as_completed(futures):
            try:
                _, _, status = fut.result()
                if status == "success":
                    success_count += 1
                elif status == "error":
                    error_count += 1
            except Exception as e:
                error_count += 1
                with print_lock:
                    print(f"[batch] worker raised: {e}")

        if _shutdown:
            print("\n[batch] Shutting down — in-flight audits will finish, no new work dispatched.")

    # Final summary
    print()
    print("━" * 60)
    print(f"  Batch complete.")
    print(f"  ✓ Success:  {success_count:>5}")
    print(f"  ✗ Error:    {error_count:>5}")
    print(f"  — Skipped:  {skip_count:>5}")
    print(f"  Total:      {success_count + error_count + skip_count:>5}")
    print("━" * 60)
    print()
    print(f"  Results saved to: {csv_file}")
    print(f"  Log file:         {LOG_FILE}")
    print()


if __name__ == "__main__":
    main()
