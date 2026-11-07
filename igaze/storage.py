"""Local preferences and session summaries. Never stores camera frames."""
import csv
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from igaze.paths import data_path


class Store:
    def __init__(self, path=None):
        self.path = path or data_path('igaze.sqlite3')
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY, started TEXT, profile TEXT, seconds REAL, actions INTEGER, error TEXT)')
            db.execute('''CREATE TABLE IF NOT EXISTS action_events (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                elapsed_seconds REAL NOT NULL, action TEXT NOT NULL, mode TEXT NOT NULL,
                frame_age_ms REAL
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS action_events_session ON action_events(session_id)')

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute('PRAGMA foreign_keys = ON')
            yield db

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM preferences WHERE key=?', (key,)).fetchone()
        try:
            return json.loads(row[0]) if row else default
        except (ValueError, TypeError):
            return default

    def set(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO preferences VALUES (?,?)', (key, json.dumps(value)))

    def record(self, started, profile, seconds, actions, error, *, events=()):
        with self.connect() as db:
            row = db.execute('INSERT INTO sessions (started,profile,seconds,actions,error) VALUES (?,?,?,?,?)',
                             (started, profile, seconds, actions, error))
            db.executemany(
                'INSERT INTO action_events (session_id,elapsed_seconds,action,mode,frame_age_ms) VALUES (?,?,?,?,?)',
                ((row.lastrowid, *event) for event in events),
            )

    def export_latest(self, destination):
        with self.connect() as db:
            rows = db.execute('''
                SELECT s.id AS session_id, s.started AS started_utc, s.seconds AS duration_seconds,
                       s.actions AS actions_sent, s.error, e.elapsed_seconds, e.action, e.mode, e.frame_age_ms
                FROM sessions AS s LEFT JOIN action_events AS e ON e.session_id = s.id
                WHERE s.id = (SELECT MAX(id) FROM sessions)
                ORDER BY e.id
            ''')
            first = rows.fetchone()
            if first is None:
                return False
            with Path(destination).open('w', encoding='utf-8-sig', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(column[0] for column in rows.description)
                writer.writerow(first)
                writer.writerows(rows)
            return True

    def history(self):
        with self.connect() as db:
            return db.execute('SELECT started,profile,seconds,actions,error FROM sessions ORDER BY id DESC LIMIT 50').fetchall()

    def clear(self):
        with self.connect() as db:
            db.execute('DELETE FROM sessions')


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
