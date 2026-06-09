import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "bluebook.sqlite"


def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS missions (
            id           INTEGER PRIMARY KEY,
            country      TEXT NOT NULL UNIQUE,
            iso2         TEXT,
            mission_name TEXT,
            address      TEXT,
            phone        TEXT,
            fax          TEXT,
            email        TEXT,
            website      TEXT,
            last_scraped TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS staff (
            id                  INTEGER PRIMARY KEY,
            mission_id          INTEGER REFERENCES missions(id) ON DELETE CASCADE,
            honorific           TEXT,
            full_name           TEXT NOT NULL,
            title               TEXT,
            rank                TEXT,
            is_head_of_mission  INTEGER DEFAULT 0,
            accreditation_date  TEXT,
            last_scraped        TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scrape_log (
            id               INTEGER PRIMARY KEY,
            run_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source           TEXT,
            missions_fetched INTEGER,
            staff_fetched    INTEGER,
            status           TEXT,
            duration_seconds REAL
        );
    """)
    conn.commit()
    # Migrate databases created before the honorific column was added
    try:
        conn.execute("ALTER TABLE staff ADD COLUMN honorific TEXT")
        conn.commit()
    except Exception:
        pass  # column already exists
    return conn


def upsert_mission(conn, data: dict) -> int:
    now = datetime.utcnow().isoformat()
    conn.execute(
        """
        INSERT INTO missions (country, iso2, mission_name, address, phone, fax, email, website, last_scraped)
        VALUES (:country, :iso2, :mission_name, :address, :phone, :fax, :email, :website, :last_scraped)
        ON CONFLICT(country) DO UPDATE SET
            iso2         = excluded.iso2,
            mission_name = excluded.mission_name,
            address      = excluded.address,
            phone        = excluded.phone,
            fax          = excluded.fax,
            email        = excluded.email,
            website      = excluded.website,
            last_scraped = excluded.last_scraped
        """,
        {**data, "last_scraped": now},
    )
    conn.commit()
    return conn.execute("SELECT id FROM missions WHERE country = ?", (data["country"],)).fetchone()[0]


def upsert_staff(conn, mission_id: int, staff_list: list[dict]) -> None:
    now = datetime.utcnow().isoformat()
    conn.execute("DELETE FROM staff WHERE mission_id = ?", (mission_id,))
    conn.executemany(
        """
        INSERT INTO staff (mission_id, honorific, full_name, title, rank,
                           is_head_of_mission, accreditation_date, last_scraped)
        VALUES (:mission_id, :honorific, :full_name, :title, :rank,
                :is_head_of_mission, :accreditation_date, :last_scraped)
        """,
        [{**s, "mission_id": mission_id, "last_scraped": now} for s in staff_list],
    )
    conn.commit()


def log_run(conn, source: str, missions_fetched: int, staff_fetched: int, status: str, duration_seconds: float) -> None:
    conn.execute(
        "INSERT INTO scrape_log (source, missions_fetched, staff_fetched, status, duration_seconds) VALUES (?,?,?,?,?)",
        (source, missions_fetched, staff_fetched, status, duration_seconds),
    )
    conn.commit()


def search_staff(conn, query: str) -> list:
    return conn.execute(
        """
        SELECT s.honorific, s.full_name, s.title, s.rank, m.country, m.mission_name
        FROM staff s
        JOIN missions m ON s.mission_id = m.id
        WHERE s.full_name LIKE ? OR s.title LIKE ? OR m.country LIKE ?
        ORDER BY m.country, s.is_head_of_mission DESC, s.full_name
        """,
        (f"%{query}%", f"%{query}%", f"%{query}%"),
    ).fetchall()


def get_mission_staff(conn, country: str) -> list:
    return conn.execute(
        """
        SELECT s.honorific, s.full_name, s.title, s.rank, s.is_head_of_mission,
               m.country, m.mission_name, m.address, m.phone
        FROM staff s
        JOIN missions m ON s.mission_id = m.id
        WHERE m.country LIKE ?
        ORDER BY s.is_head_of_mission DESC, s.full_name
        """,
        (f"%{country}%",),
    ).fetchall()


def get_all_missions(conn) -> list:
    return conn.execute(
        """
        SELECT m.id, m.country, m.mission_name, m.last_scraped,
               COUNT(s.id) AS staff_count
        FROM missions m
        LEFT JOIN staff s ON s.mission_id = m.id
        GROUP BY m.id
        ORDER BY m.country
        """,
    ).fetchall()


def get_recent_logs(conn, n: int = 10) -> list:
    return conn.execute("SELECT * FROM scrape_log ORDER BY run_at DESC LIMIT ?", (n,)).fetchall()
