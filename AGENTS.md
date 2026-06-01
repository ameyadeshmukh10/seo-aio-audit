# SEO/AIO Audit Pipeline — Agent Instructions

This repo audits prospects' blogs for SEO + AIO readiness, publishes a HubSpot landing-page dashboard per company, and writes the page URL back into each contact's `seo_aio_geo_audit` property.

The user invokes the agent by pasting a HubSpot list/segment URL (or a bare list ID) into chat. **Run end-to-end autonomously. Never ask follow-up questions.**

---

## Trigger

The agent activates when the user message contains any of:

- `https://app.hubspot.com/contacts/{portalId}/objectLists/{listId}`
- `https://app.hubspot.com/contacts/{portalId}/lists/{listId}`
- A bare numeric ID like `154293172`

### Extracting the list ID
1. Try regex `(?:objectLists|lists)/(\d+)` against the URL → group 1 is the list ID.
2. Otherwise, if the message is/contains a standalone integer ≥ 6 digits, use that.
3. If neither matches, post a single message asking for the list URL or ID, and stop. **No other clarifying questions.**

---

## Run sequence (autonomous — do not pause for confirmation)

### 0. Pre-flight (silent unless something is wrong)
- Confirm `.env` exists in the project root and contains `HUBSPOT_ACCESS_TOKEN`. If missing, post a single failure message naming exactly what's missing and stop.
- Confirm `fetch_hubspot_contacts.py`, `batch_audit_runner.py`, `run_audit.py`, and `06_update_hubspot_contact.py` exist. If any are missing, stop and tell the user.

### 1. Fetch contacts (foreground)
Run once and wait:
```
python3 fetch_hubspot_contacts.py --list-id <LIST_ID>
```
Post a one-line update with the row count fetched: e.g. *"Fetched 47 contacts from list 154293172."*

### 2. Run batch audits (background, long-running)
Launch with `run_in_background=true`:
```
python3 batch_audit_runner.py
```
Defaults are correct: 5 parallel workers, 0 s stagger, 300 s per-audit timeout, full 6-step pipeline (steps 1–5 audit + publish, step 6 updates `seo_aio_geo_audit` on each contact).

Do **not** pass `--max-posts`, `--limit`, or any other flag unless the user explicitly asks.

### 3. Stream progress to chat
Use the `Monitor` tool on the background process to receive each line of stdout as a notification. Surface progress to the user as it arrives.

The runner emits one line per audit start and one per completion, formatted:
```
[  3/47] Memgraph (memgraph.com) — starting
[  3/47] Memgraph (memgraph.com) ✓ https://...hubspot.com/seo-audit-memgraph-com  (+2 rows)
[  7/47] BadDomain (baddomain.io) ✗ ERROR:TIMEOUT
```

Reporting cadence:
- Post an immediate "Starting batch — N domains, 5 parallel workers" message after launch.
- Forward every `✓` / `✗` completion line as it arrives (one short message per completion is fine — that is the entire point of running this from chat).
- If nothing has happened for ~3 minutes, post a heartbeat (e.g., *"Still running — 12/47 done."*) by reading the latest CSV state.

### 4. Final summary
When the background process exits, post:
- Success / error / skipped counts (parse the runner's `Batch complete.` block)
- The HubSpot URLs of 3–5 successful audits (read from `contacts.csv`, `auditurl` column)
- One line confirming contact properties were updated

---

## Hard rules

- **Always run the full pipeline**, all six steps. Do not skip any.
- **Never** edit `contacts.csv` by hand. The runner owns the file and is resumable — rows with a populated `auditurl` are skipped automatically on the next run.
- **Never** ask the user to confirm before running. Pasting the URL *is* the authorization.
- **Never** propose a plan or summary before executing. Execute, then summarize.
- The HubSpot publish step is configured for immediate publish via `draft/push-live`. No scheduling, no manual review — pages go live as they're created.
- If a single audit fails, keep going. Per-row failures are non-fatal; only stop on auth/setup errors that affect every row.

---

## File map (for reference)

| File | Role |
|------|------|
| `fetch_hubspot_contacts.py` | List → `contacts.csv` |
| `batch_audit_runner.py` | Parallel orchestrator over the CSV |
| `run_audit.py` | Per-company orchestrator (steps 1–6) |
| `01_…05_…py` | SEO/AIO audit + dashboard publish |
| `06_update_hubspot_contact.py` | Writes audit URL to `seo_aio_geo_audit` property |
| `audits/{slug}/` | Per-company artifacts |
| `audit_run.log` | Append-only run history |
| `.env` | Holds `HUBSPOT_ACCESS_TOKEN` |
