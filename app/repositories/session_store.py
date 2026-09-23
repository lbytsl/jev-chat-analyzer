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
from contextlib import contextmanager
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

    @contextmanager
    def _connect(self):
        """一次操作 = 一条连接；退出时一定提交/回滚并**关闭**。

        每次调用各自开连接：写操作发生在请求线程，分类并发用的是另一批线程，
        不共享连接就没有跨线程问题；WAL 让读写不互相阻塞。

        两个容易踩的点：
        1) `with sqlite3.connect(...) as conn:` 只提交事务、**不关闭**连接（靠引用计数回收
           在 CPython 下能用，但连接与文件句柄会累积），所以这里显式 close；
        2) `isolation_level=None` —— 事务交给调用方用 `BEGIN IMMEDIATE` 显式开。默认的隐式
           事务是在第一条 DML 之前才开的，`upsert` 里「读时间戳 → 读 created_at → 写入」
           中间就会留出竞态窗口。
        """
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            yield conn
            if conn.in_transaction:
                conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

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
    def _next_stamp(self, conn) -> int:
        """严格递增的 updated_at：同一毫秒内的连续写入也要能排出先后。

        毫秒时间戳会撞车（一次请求里连写几条、测试里连着建几个会话），撞车后
        「按最后活跃倒序」就成了随机序；所以取 max(当前时间, 库里最大值 + 1)。
        必须在写事务里算（见 upsert）：换一条连接读就是「读一次再算」的竞态。
        """
        return max(now_ms(), self._latest_stamp(conn) + 1)

    @staticmethod
    def _latest_stamp(conn) -> int:
        row = conn.execute('SELECT MAX(updated_at) FROM sessions').fetchone()
        return int(row[0] or 0)

    def upsert(self, record: dict) -> dict:
        """按 id 覆盖写入（不存在则新建）；created_at 只在新建时落定。

        「取时间戳 → 读旧 created_at → 写入」三步合在**一个事务**里（BEGIN IMMEDIATE）。
        拆成三条连接时：一次保存要开 3-4 条连接，而且同一毫秒内的并发写入会算出同一个
        updated_at，「按最后活跃倒序」就变成随机序；BEGIN IMMEDIATE 立刻拿到写锁，
        避免读到别的连接还没提交的状态。
        """
        session_id = record.get('id') or new_session_id()
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            stamp = self._next_stamp(conn)
            row = conn.execute('SELECT created_at FROM sessions WHERE id = ?',
                               (session_id,)).fetchone()
            created_at = int(row['created_at']) if row is not None else stamp
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
                (
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
                ),
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
