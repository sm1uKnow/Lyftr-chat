import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class ChatStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    system_prompt TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages (session_id, id);
            """)

    def check(self) -> None:
        with self.connect() as conn:
            conn.execute("SELECT 1 FROM messages LIMIT 1").fetchone()

    def load(self, session_id: str) -> tuple[str | None, list[dict]]:
        with self.connect() as conn:
            conn.execute("BEGIN")
            session = conn.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()

        messages = []
        for row in rows:
            message = {"role": row["role"], "content": row["content"]}
            if row["metadata"] is not None:
                message["metadata"] = json.loads(row["metadata"])
            messages.append(message)
        return (session["system_prompt"] if session else None), messages

    def save_turn(
        self, session_id: str, system_prompt: str, message: str, reply: str, metadata: dict
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sessions (session_id, system_prompt) VALUES (?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET system_prompt = excluded.system_prompt""",
                (session_id, system_prompt),
            )
            conn.executemany(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (session_id, "user", message, None, now),
                    (session_id, "assistant", reply, json.dumps(metadata), now),
                ],
            )
