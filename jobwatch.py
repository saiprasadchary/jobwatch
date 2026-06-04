#!/usr/bin/env python3
"""JobWatch — monitor company career pages for new roles."""

import os
import sys
import argparse
import multiprocessing as mp
import queue
import time
import yaml
from pathlib import Path

from adapters import ADAPTERS
from filters import filter_jobs
from ranking import rank_jobs
from store import (
    VALID_STATUSES,
    cleanup_old_jobs,
    detect_source_anomalies,
    get_stats,
    get_status_summary,
    get_recent_source_health,
    mark_jobs_notified,
    mark_jobs_pending_notification,
    mark_status,
    record_source_results,
    reset_pending_notifications,
    search_jobs,
    sync_jobs,
)
from notifier import build_subject, send_email, send_ntfy, print_report
from workflow_inbox import print_summary, record_batch, render_inbox

BROWSER_ATS = {"playwright"}
RUNNER_DEFAULTS = {
    "fast_workers": 8,
    "browser_workers": 1,
    "fast_timeout_seconds": 150,
    "browser_timeout_seconds": 210,
}
ALERTING_DEFAULTS = {
    "email_bands": ("Top", "Strong"),
}


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _as_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _runner_settings(config: dict) -> dict:
    configured = config.get("runner", {}) if config else {}
    return {
        key: _as_positive_int(configured.get(key), default)
        for key, default in RUNNER_DEFAULTS.items()
    }


def _alerting_settings(config: dict) -> dict:
    configured = config.get("alerting", {}) if config else {}
    email_bands = configured.get("email_bands", ALERTING_DEFAULTS["email_bands"])
    if not isinstance(email_bands, (list, tuple)):
        email_bands = ALERTING_DEFAULTS["email_bands"]
    return {"email_bands": {str(band).strip() for band in email_bands if str(band).strip()}}


def _company_lane(company: dict) -> str:
    return "browser" if company.get("ats") in BROWSER_ATS else "fast"


def _selected_companies(companies: list[dict], lane: str) -> list[dict]:
    if lane == "all":
        return companies
    return [company for company in companies if _company_lane(company) == lane]


def _fetch_company_result(
    index: int,
    company: dict,
    keywords: list[str],
    locations: list[str],
) -> dict:
    name = company.get("name", "Unknown")
    ats = company.get("ats", "")
    lane = _company_lane(company)
    started = time.perf_counter()
    result = {
        "index": index,
        "company": name,
        "ats": ats,
        "lane": lane,
        "raw_count": 0,
        "matched_count": 0,
        "jobs": [],
        "duration": 0.0,
        "status": "ok",
        "error": None,
    }

    adapter = ADAPTERS.get(ats)
    if not adapter:
        result.update({"status": "skipped", "error": f"no adapter for '{ats}'"})
        return result

    try:
        raw_jobs = adapter(company)
        matched = filter_jobs(raw_jobs, keywords, locations)
        for job in matched:
            job.setdefault("source", ats)
        result.update(
            {
                "raw_count": len(raw_jobs),
                "matched_count": len(matched),
                "jobs": matched,
            }
        )
    except Exception as exc:
        result.update({"status": "error", "error": str(exc)})
    finally:
        result["duration"] = time.perf_counter() - started

    return result


def _fetch_company_worker(index: int, company: dict, keywords: list[str], locations: list[str], outbox) -> None:
    outbox.put(_fetch_company_result(index, company, keywords, locations))


def _timeout_result(index: int, company: dict, timeout_seconds: int) -> dict:
    return {
        "index": index,
        "company": company.get("name", "Unknown"),
        "ats": company.get("ats", ""),
        "lane": _company_lane(company),
        "raw_count": 0,
        "matched_count": 0,
        "jobs": [],
        "duration": float(timeout_seconds),
        "status": "timeout",
        "error": f"exceeded {timeout_seconds}s source budget",
    }


def _run_source_pool(
    companies: list[dict],
    keywords: list[str],
    locations: list[str],
    *,
    max_workers: int,
    timeout_seconds: int,
) -> list[dict]:
    pending = list(enumerate(companies))
    active = []
    results = []

    def start_next() -> None:
        if not pending:
            return
        index, company = pending.pop(0)
        outbox = mp.Queue(maxsize=1)
        process = mp.Process(
            target=_fetch_company_worker,
            args=(index, company, keywords, locations, outbox),
        )
        process.start()
        active.append(
            {
                "index": index,
                "company": company,
                "process": process,
                "outbox": outbox,
                "started": time.perf_counter(),
            }
        )

    for _ in range(min(max_workers, len(pending))):
        start_next()

    while active:
        for item in list(active):
            process = item["process"]
            outbox = item["outbox"]
            elapsed = time.perf_counter() - item["started"]

            try:
                result = outbox.get_nowait()
            except queue.Empty:
                result = None

            if result is not None:
                process.join(timeout=1)
                outbox.close()
                active.remove(item)
                _print_source_result(result)
                results.append(result)
                start_next()
                continue

            if not process.is_alive():
                process.join(timeout=1)
                outbox.close()
                active.remove(item)
                result = {
                    **_timeout_result(item["index"], item["company"], timeout_seconds),
                    "duration": elapsed,
                    "status": "error",
                    "error": "worker exited without returning a result",
                }
                _print_source_result(result)
                results.append(result)
                start_next()
                continue

            if elapsed > timeout_seconds:
                process.terminate()
                process.join(timeout=5)
                outbox.close()
                active.remove(item)
                result = _timeout_result(item["index"], item["company"], timeout_seconds)
                _print_source_result(result)
                results.append(result)
                start_next()

        if active:
            time.sleep(0.1)

    return sorted(results, key=lambda item: item["index"])


def _print_source_result(result: dict) -> None:
    name = result["company"]
    ats = result["ats"] or "unknown"
    duration = result["duration"]
    if result["status"] == "ok":
        print(
            f"  [{name}] {ats}/{result['lane']}: "
            f"{result['raw_count']} jobs, {result['matched_count']} matches in {duration:.1f}s"
        )
        return
    print(f"  [{name}] {ats}/{result['lane']}: {result['status'].upper()} in {duration:.1f}s - {result['error']}")


def _run_fetch_plan(
    companies: list[dict],
    keywords: list[str],
    locations: list[str],
    config: dict,
    lane: str,
) -> list[dict]:
    selected = _selected_companies(companies, lane)
    settings = _runner_settings(config)
    all_results = []

    if not selected:
        print(f"No companies selected for lane '{lane}'.")
        return []

    for lane_name, workers_key, timeout_key in (
        ("fast", "fast_workers", "fast_timeout_seconds"),
        ("browser", "browser_workers", "browser_timeout_seconds"),
    ):
        lane_companies = [company for company in selected if _company_lane(company) == lane_name]
        if not lane_companies:
            continue

        max_workers = min(settings[workers_key], len(lane_companies))
        timeout_seconds = settings[timeout_key]
        print(
            f"\n--- Fetching {len(lane_companies)} {lane_name} source(s) "
            f"with {max_workers} worker(s), {timeout_seconds}s/source ---"
        )
        all_results.extend(
            _run_source_pool(
                lane_companies,
                keywords,
                locations,
                max_workers=max_workers,
                timeout_seconds=timeout_seconds,
            )
        )

    return sorted(all_results, key=lambda item: item["index"])


def _print_health_summary(results: list[dict]) -> None:
    if not results:
        return

    print("\n--- Source health ---")
    print(f"{'Company':<24} {'Lane':<8} {'ATS':<16} {'Sec':>6} {'Raw':>5} {'Match':>5} Status")
    print("-" * 80)
    for result in sorted(results, key=lambda item: (-item["duration"], item["company"].lower())):
        status = result["status"]
        if result["error"]:
            status = f"{status}: {result['error']}"
        print(
            f"{result['company'][:24]:<24} {result['lane']:<8} {result['ats'][:16]:<16} "
            f"{result['duration']:>6.1f} {result['raw_count']:>5} {result['matched_count']:>5} {status}"
        )


def _print_health_anomalies(anomalies: list[dict]) -> None:
    if not anomalies:
        return

    print("\n--- Source health alerts ---")
    for anomaly in anomalies:
        print(f"  [{anomaly['company']}] {anomaly['anomaly']}: {anomaly['detail']}")


def _select_email_jobs(jobs: list[dict], config: dict) -> list[dict]:
    email_bands = _alerting_settings(config)["email_bands"]
    return [job for job in jobs if str(job.get("rank_band", "")).strip() in email_bands]


def cmd_run(args):
    config = load_config()
    keywords = config.get("keywords", [])
    locations = config.get("locations", [])
    companies = config.get("companies", [])
    notification = config.get("notification", {})
    recipient = (notification.get("email")
                 or os.environ.get("JOBWATCH_NOTIFY_EMAIL", "")
                 or os.environ.get("JOBWATCH_EMAIL_USER", ""))
    lane = getattr(args, "lane", "all")
    dry_run = getattr(args, "dry_run", False)

    if not companies:
        print("No companies configured in config.yaml")
        sys.exit(1)

    delivery_error = None

    results = _run_fetch_plan(companies, keywords, locations, config, lane)
    all_matched = [job for result in results for job in result["jobs"]]
    errors = [result for result in results if result["status"] in {"error", "timeout"}]
    successful_sources = [result for result in results if result["status"] == "ok"]
    health_anomalies = [] if dry_run else detect_source_anomalies(results)

    jobs_to_report = all_matched if dry_run else sync_jobs(all_matched)
    pending_jobs = rank_jobs(jobs_to_report, config=config)
    print_report(pending_jobs, config)

    if dry_run:
        print("\nDry run: database, email delivery, and workflow inbox were not updated.")
        _print_health_summary(results)
        if errors and not successful_sources:
            print("\nWARNING: All selected adapters failed - no jobs fetched this run.")
            sys.exit(1)
        return

    recorded_sources = record_source_results(results, lane)
    if recorded_sources:
        print(f"\nRecorded source health for {recorded_sources} source(s).")

    if pending_jobs:
        pending_ids = [job["job_id"] for job in pending_jobs]
        mark_jobs_pending_notification(pending_ids)

        email_jobs = _select_email_jobs(pending_jobs, config)
        subject = build_subject(email_jobs or pending_jobs, config)
        try:
            if email_jobs:
                sent, delivery_message = send_email(email_jobs, config)
            else:
                sent = True
                delivery_message = "No instant-alert tier roles this run; pending roles were archived in the workflow inbox."

            if sent:
                mark_jobs_notified(pending_ids)
                record_batch(
                    status="sent" if email_jobs else "inbox_only",
                    jobs=pending_jobs,
                    recipient=recipient,
                    subject=subject,
                )
                print(f"\n{delivery_message}")
            else:
                delivery_error = delivery_message
                reset_pending_notifications()
                record_batch(
                    status="pending_delivery",
                    jobs=pending_jobs,
                    recipient=recipient,
                    subject=subject,
                    error=delivery_error,
                )
                print(f"\n{delivery_error}")
        except Exception as e:
            delivery_error = f"Email failed: {e}"
            reset_pending_notifications()
            record_batch(
                status="delivery_failed",
                jobs=pending_jobs,
                recipient=recipient,
                subject=subject,
                error=delivery_error,
            )
            print(f"\n{delivery_error}")

        if email_jobs:
            send_ntfy(email_jobs, config)

    inbox_path = render_inbox(config=config)
    print(f"\nWorkflow inbox updated: {inbox_path}")

    # Run retention cleanup only on the browser lane (every 4h) to avoid
    # unnecessary DELETE queries on every 30-minute fast-lane run.
    if lane in ("browser", "all"):
        cleaned = cleanup_old_jobs()
        if cleaned:
            print(f"Cleaned up {cleaned} job(s) older than 30 days.")

    _print_health_summary(results)
    _print_health_anomalies(health_anomalies)

    if errors:
        print(f"\n--- Errors ({len(errors)}) ---")
        for err in errors:
            print(f"  {err['company']}: {err['error']}")

    if errors and not successful_sources:
        print("\nWARNING: All selected adapters failed — no jobs fetched this run.")
        sys.exit(1)

    if delivery_error:
        print("\nWARNING: New roles were saved but not marked notified so the next run can retry delivery.")
        sys.exit(1)

    stats = get_stats()
    print(f"\nTotal jobs tracked: {stats['total']}")


def cmd_mark(args):
    job_id = args.job_id
    status = args.status

    if mark_status(job_id, status):
        print(f"Marked {job_id} as '{status}'")
    else:
        print(f"Job not found: {job_id}")
        print("Use 'jobwatch search <query>' to find job IDs")


def cmd_search(args):
    results = search_jobs(args.query, limit=args.limit)
    if not results:
        print(f"No jobs matching '{args.query}'")
        return

    print(f"\n{'ID':<45} {'Status':<12} {'Company':<20} Title")
    print("─" * 110)
    for r in results:
        jid = r["job_id"][:44]
        print(f"{jid:<45} {r['status']:<12} {r['company']:<20} {r['title']}")
        if r.get("url"):
            print(f"{'':>45} → {r['url']}")


def cmd_status(args):
    summary = get_status_summary()

    print("\n--- Application Pipeline ---")
    for status, count in summary["by_status"]:
        print(f"  {status:<15} {count:>5}")

    active = summary["active_applications"]
    if active:
        print(f"\n--- Active Applications ({len(active)}) ---")
        for app in active:
            print(f"  [{app['status']}] {app['company']} — {app['title']}")
            if app.get("url"):
                print(f"    → {app['url']}")
    else:
        print("\nNo active applications yet. Use 'jobwatch mark <job_id> applied' to track.")


def cmd_health(args):
    rows = get_recent_source_health(limit=args.limit)
    if not rows:
        print("No source health has been recorded yet.")
        return

    print("\n--- Recent Source Health ---")
    print(f"{'Run At':<20} {'Company':<24} {'Lane':<8} {'ATS':<16} {'Sec':>6} {'Raw':>5} {'Match':>5} Status")
    print("-" * 102)
    for row in rows:
        status = row["status"]
        if row.get("error"):
            status = f"{status}: {row['error']}"
        print(
            f"{row['run_at'][:19]:<20} {row['company'][:24]:<24} {row['source_lane']:<8} "
            f"{(row['ats'] or '')[:16]:<16} {float(row['duration_seconds']):>6.1f} "
            f"{int(row['raw_count']):>5} {int(row['matched_count']):>5} {status}"
        )


def cmd_export(args):
    import csv
    from store import _connect

    conn = _connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.execute(
        "SELECT job_id, company, title, location, url, status, first_seen, last_seen, posted_at, source, salary, notified_at "
        "FROM seen_jobs ORDER BY first_seen DESC"
    )
    rows = cur.fetchall()
    conn.close()

    out = args.output or "jobwatch_export.csv"
    with open(out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "job_id",
                "company",
                "title",
                "location",
                "url",
                "status",
                "first_seen",
                "last_seen",
                "posted_at",
                "source",
                "salary",
                "notified_at",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r["job_id"],
                    r["company"],
                    r["title"],
                    r["location"],
                    r["url"],
                    r["status"],
                    r["first_seen"],
                    r["last_seen"],
                    r["posted_at"],
                    r["source"],
                    r["salary"],
                    r["notified_at"],
                ]
            )

    print(f"Exported {len(rows)} jobs to {out}")


def cmd_inbox(args):
    config = load_config()
    inbox_path = render_inbox(limit=args.limit, config=config)
    print(print_summary(limit=args.limit, config=config))
    print(f"\nWorkflow inbox file: {inbox_path}")


def main():
    parser = argparse.ArgumentParser(description="JobWatch — career page monitor")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Fetch new jobs and send alerts")
    p_run.add_argument(
        "--lane",
        choices=("all", "fast", "browser"),
        default="all",
        help="Source lane to run: all, fast ATS/API sources, or browser-backed Playwright sources",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and rank jobs without updating the database, sending email, or writing workflow inbox files",
    )

    p_mark = sub.add_parser("mark", help="Update a job's application status")
    p_mark.add_argument("job_id", help="Job ID (from search results)")
    p_mark.add_argument("status", choices=VALID_STATUSES, help="New status")

    p_search = sub.add_parser("search", help="Search tracked jobs")
    p_search.add_argument("query", help="Search term (matches title or company)")
    p_search.add_argument("--limit", type=int, default=20, help="Max results")

    sub.add_parser("status", help="Show application pipeline summary")

    p_health = sub.add_parser("health", help="Show recent source health runs")
    p_health.add_argument("--limit", type=int, default=50, help="Max source health rows to show")

    p_export = sub.add_parser("export", help="Export all jobs to CSV")
    p_export.add_argument("--output", "-o", help="Output file path (default: jobwatch_export.csv)")

    p_inbox = sub.add_parser("inbox", help="Show the local workflow inbox summary")
    p_inbox.add_argument("--limit", type=int, default=10, help="Max batches/jobs to show")

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "mark": cmd_mark,
        "search": cmd_search,
        "status": cmd_status,
        "health": cmd_health,
        "export": cmd_export,
        "inbox": cmd_inbox,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        # Default: run
        cmd_run(args)


if __name__ == "__main__":
    main()
