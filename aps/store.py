import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4


class Store:
    def __init__(self, location: Path):
        self.location = location

    def connect(self):
        self.location.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.location)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self):
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, name TEXT, workbook BLOB);
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    parent_id TEXT REFERENCES plans(id), internal TEXT NOT NULL, result TEXT NOT NULL
                );
            """)

    def create_dataset(self, name, workbook):
        key = uuid4().hex
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT INTO datasets VALUES (?, ?, ?)", (key, name, workbook))
        return key

    def get_dataset(self, key):
        with closing(self.connect()) as conn:
            return conn.execute("SELECT name, workbook FROM datasets WHERE id=?", (key,)).fetchone()

    def save_plans(self, dataset_id, parent_id, plans):
        items = []
        for internal, result in plans:
            key = uuid4().hex
            result.update(id=key, dataset_id=dataset_id, parent_id=parent_id)
            items.append((key, dataset_id, parent_id, json.dumps(internal), json.dumps(result)))
        with closing(self.connect()) as conn, conn:
            conn.executemany("INSERT INTO plans VALUES (?, ?, ?, ?, ?)", items)

    def get_plan(self, key):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT internal, result FROM plans WHERE id=?", (key,)).fetchone()
            if row:
                return tuple(json.loads(value) for value in row)
        return None
