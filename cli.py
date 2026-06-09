"""
Query the UN diplomatic missions database.

Commands
--------
    python cli.py missions              list all missions with staff counts
    python cli.py search <term>         search staff by name, title, or country
    python cli.py country <name>        full detail for one country's mission
    python cli.py lookup France "First Secretary"
                                        look up staff by country + position
    python cli.py lookup --from-file attendees.csv
                                        batch lookup from a CSV with columns:
                                        country, position
    python cli.py export <file.csv>     export all data to CSV
    python cli.py log                   show recent scrape run history
    python cli.py probe [url ...]       save screenshots + HTML for debugging
"""

import argparse
import asyncio
import csv
import sys

from rich.console import Console
from rich.table import Table

from db import (
    get_all_missions,
    get_mission_staff,
    get_recent_logs,
    init_db,
    search_staff,
)

console = Console()
DB_PATH = "bluebook.sqlite"


# ── Command handlers ──────────────────────────────────────────────────────────

def cmd_missions(_args) -> None:
    conn = init_db(DB_PATH)
    rows = get_all_missions(conn)
    conn.close()

    t = Table(title=f"UN Permanent Missions — New York ({len(rows)} total)")
    t.add_column("Country", style="bold")
    t.add_column("Mission Name")
    t.add_column("Staff", justify="right")
    t.add_column("Last Updated")
    for r in rows:
        t.add_row(r["country"], r["mission_name"] or "", str(r["staff_count"]), r["last_scraped"] or "—")
    console.print(t)


def _display_name(row) -> str:
    """Combine honorific + full name for display."""
    h = (row["honorific"] or "").strip()
    return f"{h} {row['full_name']}".strip() if h else row["full_name"]


def cmd_search(args) -> None:
    conn = init_db(DB_PATH)
    rows = search_staff(conn, args.query)
    conn.close()

    t = Table(title=f'Search: "{args.query}" — {len(rows)} result(s)')
    t.add_column("Name", style="bold")
    t.add_column("Title")
    t.add_column("Rank")
    t.add_column("Country")
    for r in rows:
        t.add_row(_display_name(r), r["title"] or "", r["rank"] or "", r["country"])
    console.print(t)


def cmd_country(args) -> None:
    conn = init_db(DB_PATH)
    rows = get_mission_staff(conn, args.country)
    conn.close()

    if not rows:
        console.print(f"[red]No mission found matching '{args.country}'")
        sys.exit(1)

    m = rows[0]
    console.print(f"\n[bold]{m['mission_name']}[/bold]")
    if m["address"]:
        console.print(f"  Address : {m['address']}")
    if m["phone"]:
        console.print(f"  Phone   : {m['phone']}")

    t = Table()
    t.add_column("Name", style="bold")
    t.add_column("Title")
    t.add_column("Rank")
    t.add_column("HoM", justify="center")
    for r in rows:
        t.add_row(
            _display_name(r),
            r["title"] or "",
            r["rank"] or "",
            "[green]✓[/green]" if r["is_head_of_mission"] else "",
        )
    console.print(t)


def cmd_export(args) -> None:
    conn = init_db(DB_PATH)
    rows = conn.execute(
        """
        SELECT m.country, m.mission_name, m.address, m.phone, m.fax, m.email, m.website,
               s.full_name, s.title, s.rank, s.is_head_of_mission, s.accreditation_date
        FROM staff s
        JOIN missions m ON s.mission_id = m.id
        ORDER BY m.country, s.is_head_of_mission DESC, s.full_name
        """
    ).fetchall()
    conn.close()

    with open(args.file, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Country", "Mission", "Address", "Phone", "Fax", "Email", "Website",
            "Staff Name", "Title", "Rank", "Head of Mission", "Accreditation Date",
        ])
        w.writerows(rows)

    console.print(f"[green]Exported {len(rows)} rows → {args.file}")


def cmd_log(_args) -> None:
    conn = init_db(DB_PATH)
    rows = get_recent_logs(conn)
    conn.close()

    t = Table(title="Recent Scrape Runs")
    t.add_column("Time")
    t.add_column("Source")
    t.add_column("Missions", justify="right")
    t.add_column("Staff", justify="right")
    t.add_column("Duration", justify="right")
    t.add_column("Status")
    for r in rows:
        t.add_row(
            r["run_at"],
            r["source"] or "",
            str(r["missions_fetched"] or 0),
            str(r["staff_fetched"] or 0),
            f"{r['duration_seconds']:.1f}s",
            r["status"] or "",
        )
    console.print(t)


# ── Abbreviation expansion ────────────────────────────────────────────────────
# Maps common UN diplomatic shorthand → SQL LIKE pattern applied to title/rank.
# Patterns without a leading % are prefix-anchored (e.g. PR won't match DPR).
# Add entries here as new shorthands come up.
ABBREVS: dict[str, str] = {
    # ── Positions (match against title / BB_Function) ──────────────────────
    "pr":              "Permanent Representative%",   # prefix: avoids matching DPR
    "perm rep":        "Permanent Representative%",
    "p.r.":            "Permanent Representative%",
    "dpr":             "%Deputy Permanent Representative%",
    "dep pr":          "%Deputy Permanent Representative%",
    "dep. pr.":        "%Deputy Permanent Representative%",
    "2dpr":            "%Second Deputy Permanent Representative%",
    "2nd dpr":         "%Second Deputy Permanent Representative%",
    "alt rep":         "%Alternate Representative%",
    "alt. rep.":       "%Alternate Representative%",
    "alternate rep":   "%Alternate Representative%",
    "po":              "Permanent Observer%",         # prefix: avoids matching DPO
    "perm obs":        "Permanent Observer%",
    "dpo":             "%Deputy Permanent Observer%",
    "dep po":          "%Deputy Permanent Observer%",
    "cda":             "%Charg%",                     # Chargé d'Affaires, any spelling
    "chargé":          "%Charg%",
    "charge":          "%Charg%",
    "chargé d'affaires": "%Charg%",
    "hoc":             "%Head of Chancery%",
    "head of chancery": "%Head of Chancery%",
    "mil adv":         "%Military Adviser%",
    "military adviser": "%Military Adviser%",
    "legal adv":       "%Legal Adviser%",
    # ── Ranks (match against rank / BB_Dipl_Rank_Display) ─────────────────
    "amb":             "%Ambassador%",
    "he":              "%Ambassador%",   # H.E. conventionally means Ambassador rank
    "h.e.":            "%Ambassador%",
    "aep":             "%Ambassador Extraordinary%",
    "mc":              "%Minister Counsellor%",
    "min couns":       "%Minister Counsellor%",
    "min. couns.":     "%Minister Counsellor%",
    "mp":              "%Minister Plenipotentiary%",
    "couns":           "%Counsellor%",
    "counsellor":      "%Counsellor%",
    "1st sec":         "%First Secretary%",
    "fs":              "%First Secretary%",
    "2nd sec":         "%Second Secretary%",
    "3rd sec":         "%Third Secretary%",
    "att":             "%Attaché%",
    "attache":         "%Attaché%",
}


def _expand(position: str) -> str:
    """Expand a position abbreviation to a SQL LIKE pattern, or wrap as-is."""
    key = position.strip().lower()
    if key in ABBREVS:
        return ABBREVS[key]
    # Not a known abbreviation — treat as a free-text partial match
    return f"%{position}%"


def cmd_lookup(args) -> None:
    """
    Resolve who currently holds a given position at a given mission.
    Understands standard UN shorthand: PR, DPR, Alt Rep, CDA, MC, etc.

    Single query:
        python cli.py lookup Latvia DPR
        python cli.py lookup France PR

    Batch CSV (columns: country, position — header optional):
        python cli.py lookup --from-file attendees.csv
        python cli.py lookup --from-file attendees.csv --out results.csv
    """
    conn = init_db(DB_PATH)

    pairs: list[tuple[str, str]] = []
    if args.from_file:
        with open(args.from_file, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                country, position = row[0].strip(), row[1].strip()
                if country.lower() in ("country", ""):
                    continue
                pairs.append((country, position))
    else:
        pairs = [(args.country, args.position or "")]

    all_rows: list = []
    not_found: list[tuple[str, str]] = []

    for country, position in pairs:
        pattern = _expand(position) if position else "%"
        rows = conn.execute(
            """
            SELECT s.honorific, s.full_name, s.title, s.rank, s.is_head_of_mission,
                   s.accreditation_date,
                   m.country, m.mission_name, m.address, m.phone, m.email
            FROM staff s
            JOIN missions m ON s.mission_id = m.id
            WHERE m.country LIKE ?
              AND (s.title LIKE ? OR s.rank LIKE ?)
            ORDER BY s.is_head_of_mission DESC, s.full_name
            """,
            (f"%{country}%", pattern, pattern),
        ).fetchall()

        if rows:
            all_rows.extend(rows)
        else:
            not_found.append((country, position))

    if all_rows:
        t = Table(title=f"Lookup — {len(all_rows)} result(s)")
        t.add_column("Country", style="bold")
        t.add_column("Name")
        t.add_column("Position / Title")
        t.add_column("Rank")
        t.add_column("HoM", justify="center")
        t.add_column("Phone")
        t.add_column("Email")
        for r in all_rows:
            t.add_row(
                r["country"],
                _display_name(r),
                r["title"] or "",
                r["rank"] or "",
                "[green]✓[/green]" if r["is_head_of_mission"] else "",
                r["phone"] or "",
                r["email"] or "",
            )
        console.print(t)

    if not_found:
        console.print(
            f"\n[yellow]No match for "
            f"{len(not_found)} entr{'y' if len(not_found) == 1 else 'ies'}[/yellow]"
        )
        for country, position in not_found:
            console.print(f"  [bold]{country}[/bold] / [italic]{position}[/italic]")
            # Show the senior staff that DO exist so the user can pick the right term
            senior = conn.execute(
                """
                SELECT s.full_name, s.title, s.rank
                FROM staff s JOIN missions m ON s.mission_id = m.id
                WHERE m.country LIKE ?
                  AND (
                    s.title LIKE '%Representative%'
                    OR s.title LIKE '%Observer%'
                    OR s.title LIKE '%Charg%'
                    OR s.title LIKE '%Head of Chancery%'
                    OR s.rank  LIKE '%Ambassador%'
                    OR s.rank  LIKE '%Minister%'
                  )
                ORDER BY s.is_head_of_mission DESC, s.full_name
                LIMIT 6
                """,
                (f"%{country}%",),
            ).fetchall()
            if senior:
                for s in senior:
                    console.print(
                        f"    [dim]→ {s['full_name']} | "
                        f"{s['title'] or '—'} | {s['rank'] or '—'}[/dim]"
                    )
            else:
                console.print(f"    [dim](no mission found for {country!r})[/dim]")

    conn.close()

    if args.out and all_rows:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "Country", "Mission", "Honorific", "Name", "Position/Title", "Rank",
                "Head of Mission", "Accreditation Date", "Phone", "Email", "Address",
            ])
            for r in all_rows:
                w.writerow([
                    r["country"], r["mission_name"],
                    r["honorific"] or "", r["full_name"],
                    r["title"] or "", r["rank"] or "",
                    "Yes" if r["is_head_of_mission"] else "No",
                    r["accreditation_date"] or "",
                    r["phone"] or "", r["email"] or "", r["address"] or "",
                ])
        console.print(f"[green]Saved → {args.out}")


def cmd_probe(args) -> None:
    from scraper import probe
    urls = args.urls or None
    asyncio.run(probe(urls))


# ── Argument parser ───────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="UN Diplomatic Missions DB — query and maintenance tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("missions", help="List all missions with staff counts")

    s = sub.add_parser("search", help="Search staff by name, title, or country")
    s.add_argument("query", help="Search term")

    c = sub.add_parser("country", help="Show all staff for a country's mission")
    c.add_argument("country", help="Country name (partial match OK)")

    e = sub.add_parser("export", help="Export all data to CSV")
    e.add_argument("file", help="Output CSV file path")

    lk = sub.add_parser("lookup", help="Look up staff by country + position")
    lk_src = lk.add_mutually_exclusive_group(required=True)
    lk_src.add_argument("--from-file", metavar="CSV", help="CSV file with columns: country, position")
    lk_src.add_argument("country", nargs="?", help="Country name (partial match OK)")
    lk.add_argument("position", nargs="?", default="",
                    help="Position/title/rank to filter by (partial match, optional)")
    lk.add_argument("--out", metavar="FILE", help="Save results to CSV")

    sub.add_parser("log", help="Show recent scrape run history")

    pr = sub.add_parser("probe", help="Save screenshots + HTML of source URLs for debugging")
    pr.add_argument("urls", nargs="*", help="URLs to probe (defaults to Blue Book + un.int)")

    args = p.parse_args()
    {
        "missions": cmd_missions,
        "search": cmd_search,
        "country": cmd_country,
        "export": cmd_export,
        "lookup": cmd_lookup,
        "log": cmd_log,
        "probe": cmd_probe,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
