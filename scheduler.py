"""
Fetch UN diplomatic mission data and keep the local SQLite database current.

Usage
-----
    python scheduler.py           # fetch now, then refresh every 24 hours
    python scheduler.py --once    # fetch once and exit
"""

import sys
import time

import schedule as sched
from rich.console import Console

from db import init_db, log_run, upsert_mission, upsert_staff
from scraper import fetch_bluebook, merge_sources, scrape_un_int

console = Console()
DB_PATH = "bluebook.sqlite"


def run_scrape() -> None:
    start = time.time()
    console.rule("[bold blue]UN Diplomatic Missions — refresh")

    # Primary source: direct JSON API (fast, no browser)
    console.print("[cyan]Fetching Blue Book JSON...")
    try:
        bb_data = fetch_bluebook()
        console.print(f"  [green]{len(bb_data)} missions from Blue Book")
    except Exception as e:
        console.print(f"  [red]Blue Book error: {e}")
        bb_data = []

    # Supplemental source: un.int (optional, Playwright-based)
    # Disabled by default since the Blue Book already has full coverage.
    # Un-comment the block below to enable it.
    unint_data: list = []
    # import asyncio
    # console.print("[cyan]Scraping un.int (supplemental)...")
    # try:
    #     unint_data = asyncio.run(scrape_un_int())
    #     console.print(f"  [green]{len(unint_data)} missions from un.int")
    # except Exception as e:
    #     console.print(f"  [yellow]un.int skipped: {e}")

    if not bb_data and not unint_data:
        console.print("[red]No data from either source — database not modified.")
        conn = init_db(DB_PATH)
        log_run(conn, "all", 0, 0, "no data", time.time() - start)
        conn.close()
        return

    data = merge_sources(bb_data, unint_data)
    console.print(f"[cyan]Writing {len(data)} missions to {DB_PATH}...")

    conn = init_db(DB_PATH)
    total_staff = 0
    for mission in data:
        mid = upsert_mission(conn, {k: v for k, v in mission.items() if k != "staff"})
        staff = mission.get("staff") or []
        upsert_staff(conn, mid, staff)
        total_staff += len(staff)

    duration = time.time() - start
    log_run(conn, "bluebook", len(data), total_staff, "ok", duration)
    conn.close()

    console.print(
        f"[bold green]Done — {len(data)} missions, {total_staff} staff "
        f"in {duration:.1f}s"
    )


def main() -> None:
    once = "--once" in sys.argv

    run_scrape()

    if once:
        return

    sched.every(24).hours.do(run_scrape)
    console.print("[dim]Scheduled to refresh every 24 hours. Ctrl+C to stop.")

    try:
        while True:
            sched.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.")


if __name__ == "__main__":
    main()
