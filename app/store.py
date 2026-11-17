"""SQLite JSON snapshots; one transaction serializes each edit and its revision."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.errors import AppError


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL
            )""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=35)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def load(self, db, campaign_id: int) -> dict:
        row = db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if row is None:
            raise AppError("Сначала загрузите потоки кампании из Keitaro.", 404)
        document = json.loads(row["document"])
        document["revision"] = row["revision"]
        return document

    def get(self, campaign_id: int) -> dict:
        with self.connection() as db:
            return self.load(db, campaign_id)

    def save(self, db, document: dict) -> dict:
        document["revision"] = document.get("revision", 0) + 1
        db.execute(
            """INSERT INTO campaigns VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, document=excluded.document""",
            (document["id"], document["revision"], json.dumps(document, ensure_ascii=False)),
        )
        return document

    @staticmethod
    def check_revision(document: dict, revision: int):
        if document["revision"] != revision:
            raise AppError("Кампания изменена в другой вкладке. Откройте её заново.", 409)
