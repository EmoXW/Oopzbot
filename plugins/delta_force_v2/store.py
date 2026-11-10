"""Isolated persistent game sessions; never modifies the legacy plugin database."""
from pathlib import Path

import aiosqlite

from core.paths import DATA_DIR


class SessionStore:
    def __init__(self, path=None):
        self.path = Path(path or Path(DATA_DIR) / "delta_force_v2.sqlite3")

    async def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path)
        await db.execute("CREATE TABLE IF NOT EXISTS sessions (client TEXT, user TEXT, token TEXT, PRIMARY KEY(client,user))")
        return db

    async def get(self, client, user):
        db = await self._open()
        try:
            async with db.execute("SELECT token FROM sessions WHERE client=? AND user=?", (client, user)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else ""
        finally:
            await db.close()

    async def put(self, client, user, token):
        db = await self._open()
        try:
            await db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?)", (client, user, token))
            await db.commit()
        finally:
            await db.close()

    async def delete(self, client, user):
        db = await self._open()
        try:
            await db.execute("DELETE FROM sessions WHERE client=? AND user=?", (client, user))
            await db.commit()
        finally:
            await db.close()
