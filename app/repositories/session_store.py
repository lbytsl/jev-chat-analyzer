"""会话库（SQLite）：一次导入 = 一条会话，可持久记忆、可切换。

为什么用 SQLite 而不是一堆 JSON 文件：
- 写入是原子的，不会出现「写了半个 JSON」的坏档；
- 列表只要一条带索引的 SQL（按最后活跃时间倒序），不用读盘解析所有文件；
- 依然零依赖（标准库 sqlite3），文件就一个 `var/sessions.db`。

存法：分析结果整体存成 `payload` JSON 列（envelope 原样），另外把列表要用的元数据
（标题、预览、条数、时间、两个生成开关）单独存列。这样列结构只服务于「列表页」，
分析结果结构再演进（v008 就动过）也不用写迁移。

注意：这个库里有真实聊天原文，落在 `var/`（已 gitignore），不会进仓库。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from app.core.config import SESSIONS_DB, SESSIONS_LIST_LIMIT

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                 TEXT PRIMARY KEY,
    title              TEXT NOT NULL DEFAULT '',
    relationship       TEXT NOT NULL DEFAULT '',
    transcript         TEXT NOT NULL DEFAULT '',
    preview            TEXT NOT NULL DEFAULT '',
    message_count      INTEGER NOT NULL DEFAULT 0,
    count_other        INTEGER NOT NULL DEFAULT 0,
    count_me           INTEGER NOT NULL DEFAULT 0,
    failed_count       INTEGER NOT NULL DEFAULT 0,
    gen_interpretation INTEGER NOT NULL DEFAULT 0,
    gen_suggestions    INTEGER NOT NULL DEFAULT 0,
    payload            TEXT NOT NULL DEFAULT '{}',
    created_at         INTEGER NOT NULL DEFAULT 0,
    updated_at         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
"""

META_FIELDS = ('id', 'title', 'relationship', 'preview', 'message_count', 'count_other',
               'count_me', 'failed_count', 'gen_interpretation', 'gen_suggestions',
               'created_at', 'updated_at')


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]


def now_ms() -> int:
    return int(time.time() * 1000)


class SessionStore:
    def __init__(self, path: Path | str = SESSIONS_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # 每次调用各自开连接：写操作发生在请求线程，分类并发用的是另一批线程，
        # 不共享连接就没有跨线程问题；WAL 让读写不互相阻塞。
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        return conn

    # ---------- 读 ----------
    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])

    def list_meta(self, limit: int = SESSIONS_LIST_LIMIT) -> list[dict]:
        columns = ', '.join(META_FIELDS)
        with self._connect() as conn:
            rows = conn.execute(
                f'SELECT {columns} FROM sessions ORDER BY updated_at DESC, id DESC LIMIT ?',
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, session_id: str) -> dict | None:
        columns = ', '.join(META_FIELDS)
        with self._connect() as conn:
            row = conn.execute(
                f'SELECT {columns}, transcript, payload FROM sessions WHERE id = ?',
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record['payload'] = json.loads(record.get('payload') or '{}')
        return record

    # ---------- 写 ----------
    def _next_stamp(self) -> int:
        """严格递增的 updated_at：同一毫秒内的连续写入也要能排出先后。

        毫秒时间戳会撞车（一次请求里连写几条、测试里连着建几个会话），撞车后
        「按最后活跃倒序」就成了随机序；所以取 max(当前时间, 库里最大值 + 1)。
        """
        return max(now_ms(), self._latest_stamp() + 1)

    def _latest_stamp(self) -> int:
        with self._connect() as conn:
            row = conn.execute('SELECT MAX(updated_at) FROM sessions').fetchone()
        return int(row[0] or 0)

    def upsert(self, record: dict) -> dict:
        """按 id 覆盖写入（不存在则新建）；created_at 只在新建时落定。"""
        session_id = record.get('id') or new_session_id()
        stamp = self._next_stamp()
        existing = self.get(session_id)
        created_at = existing['created_at'] if existing else stamp
        values = (
            session_id,
            (record.get('title') or '').strip(),
            record.get('relationship') or '',
            record.get('transcript') or '',
            record.get('preview') or '',
            int(record.get('message_count') or 0),
            int(record.get('count_other') or 0),
            int(record.get('count_me') or 0),
            int(record.get('failed_count') or 0),
            1 if record.get('gen_interpretation') else 0,
            1 if record.get('gen_suggestions') else 0,
            json.dumps(record.get('payload') or {}, ensure_ascii=False),
            created_at,
            stamp,
        )
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO sessions (id, title, relationship, transcript, preview, message_count,'
                ' count_other, count_me, failed_count, gen_interpretation, gen_suggestions,'
                ' payload, created_at, updated_at)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
                ' ON CONFLICT(id) DO UPDATE SET'
                ' title=excluded.title, relationship=excluded.relationship,'
                ' transcript=excluded.transcript, preview=excluded.preview,'
                ' message_count=excluded.message_count, count_other=excluded.count_other,'
                ' count_me=excluded.count_me, failed_count=excluded.failed_count,'
                ' gen_interpretation=excluded.gen_interpretation,'
                ' gen_suggestions=excluded.gen_suggestions, payload=excluded.payload,'
                ' updated_at=excluded.updated_at',
                values,
            )
        return {'id': session_id, 'created_at': created_at, 'updated_at': stamp}

    def delete(self, ids: list[str]) -> int:
        clean = [str(item) for item in ids if str(item).strip()]
        if not clean:
            return 0
        placeholders = ','.join('?' for _ in clean)
        with self._connect() as conn:
            cursor = conn.execute(f'DELETE FROM sessions WHERE id IN ({placeholders})', clean)
        return int(cursor.rowcount)
