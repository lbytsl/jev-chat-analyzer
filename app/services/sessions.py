"""会话用例：把「一次导入」存成一条可切换的会话记录。

规则（与产品确认过的口径一致）：
- 导入即存档；**同正文重跑 = 原地更新当前会话，不同正文 = 新建会话**；
- 追加新消息、补跑生成层、单句潜台词都更新当前会话（生成内容不丢）；
- 删除由用户显式触发，不做自动清理。

职责边界：本层只做「envelope ↔ 会话记录」的转换与那几条规则，真正的读写交给
`SessionStore`，因此换成别的存储实现时这里不用动。
"""
from __future__ import annotations

import threading
import zlib

from app.core.config import SESSIONS_LIST_LIMIT, VERSION
from app.core.exceptions import InvalidRequest, NotFoundError
from app.repositories.session_store import SessionStore, new_session_id

DEFAULT_TITLE = '未命名会话'
PREVIEW_CHARS = 24
# 会话元数据（由表列负责，payload 里不再存副本）
SESSION_META_KEYS = ('session_id', 'title', 'transcript', 'created_at', 'updated_at')
# 会话写锁的分片数：固定常量，与「访问过多少个会话」无关。
LOCK_SHARDS = 64


class SessionService:
    # 每个会话一把写锁：整段分析落库、追加消息、补跑生成都是「读会话 → 改 → 写回」，
    # 两类生成并发时（右键「一键生成」会同时调两个端点），不加锁就会后写覆盖先写，
    # 先合并的那类结果凭空消失。
    #
    # 实现是**固定分片**而不是「一个会话一把锁」的字典：字典会随访问过的会话一直长、
    # 从不清理，长跑就是慢性内存泄漏；分片数是常量，两三个会话撞到同一片只是多串行一下。
    # 用 RLock：apply_augmentations 持锁后会再调 update_analysis（同一线程需重入）。
    _LOCKS = tuple(threading.RLock() for _ in range(LOCK_SHARDS))

    def __init__(self, store: SessionStore | None = None):
        self._store = store or SessionStore()

    @classmethod
    def _lock_for(cls, session_id: str) -> threading.RLock:
        # 用 crc32 而不是内置 hash()：str 的 hash 每个进程都带随机盐，跨进程不稳定
        # （虽然只在进程内用，但没必要留这种惊喜）。
        digest = zlib.crc32(str(session_id).encode('utf-8'))
        return cls._LOCKS[digest % LOCK_SHARDS]

    # ---------- 读 ----------
    def list_sessions(self, limit: int = SESSIONS_LIST_LIMIT) -> dict:
        limit = max(1, min(int(limit or SESSIONS_LIST_LIMIT), SESSIONS_LIST_LIMIT))
        return {'sessions': self._store.list_meta(limit), 'total': self._store.count()}

    def load(self, session_id: str) -> dict:
        record = self._store.get(session_id)
        if record is None:
            raise NotFoundError('这个会话不存在，可能已经被删除。')
        return self._to_detail(record)

    # ---------- 写 ----------
    def save_analysis(self, envelope: dict, transcript: str, session_id: str | None = None,
                      title: str | None = None) -> dict:
        """整段分析后落库；返回会话元数据（含最终 id）。

        只有「带了 session_id 且库里那条的正文与新正文一模一样」才算原地重跑；
        正文不同（用户换了记录）静默另起一条，绝不覆盖旧会话。
        """
        # 锁挂在「本次要读写的那个 id」上；没带 session_id 时是全新 id，无人竞争。
        target_id = session_id or new_session_id()
        with self._lock_for(target_id):
            keep_title = (title or '').strip() or None
            existing = self._store.get(target_id) if session_id else None
            if existing is not None and existing.get('transcript') == transcript:
                keep_title = keep_title or (existing.get('title') or None)
            elif session_id:
                # 带了 session_id 但正文不同（或那条已被删）：另起一条，绝不覆盖旧会话。
                target_id = new_session_id()
            saved = self._store.upsert(self._record(envelope, transcript, target_id, keep_title))
        return {'session_id': saved['id'], 'updated_at': saved['updated_at'],
                'created_at': saved['created_at'], 'title': keep_title or self._default_title(envelope)}

    def update_analysis(self, session_id: str, envelope: dict, transcript: str | None = None) -> dict:
        """追加消息 / 补跑生成层 / 单句潜台词后更新已有会话（保留原标题与创建时间）。

        同样加会话锁：与「另一类生成正在合并」并发时，读到的必须是对方写完的版本。
        RLock 允许 apply_augmentations 持锁后重入到这里。
        """
        with self._lock_for(session_id):
            existing = self._store.get(session_id)
            if existing is None:
                raise NotFoundError('这个会话不存在，可能已经被删除。')
            text = transcript if transcript is not None else existing.get('transcript', '')
            saved = self._store.upsert(self._record(envelope, text, session_id, existing.get('title')))
        return {'session_id': saved['id'], 'updated_at': saved['updated_at'],
                'created_at': saved['created_at'], 'title': existing.get('title') or ''}

    def _record(self, envelope: dict, transcript: str, session_id: str | None,
                title: str | None) -> dict:
        meta = self._derive(envelope, transcript, title=title)
        meta['id'] = session_id or new_session_id()
        meta['payload'] = self.strip_meta(envelope)
        return meta

    @staticmethod
    def strip_meta(envelope: dict) -> dict:
        """存 payload 前把会话元数据摘掉：这些字段由列负责，别在 JSON 里留旧副本。"""
        return {key: value for key, value in envelope.items() if key not in SESSION_META_KEYS}

    @staticmethod
    def merge_augmentations(payload: dict, augmentations: dict, kind: str) -> dict:
        """把补跑结果合并进会话（与前端同一套规则，服务端这份是权威副本）。

        `kind` = 'interpretation' | 'suggestions'，一次只合并一类：
        - 只覆盖本次真的生成过的字段（按字段是否存在判断，不会误清另一类的结果）；
        - 失败按种类标记：推荐回复失败 → gen_failed（底部回复面板据此提示），
          潜台词失败 → interpretation_failed；
        - 只有真的合并到内容才翻会话开关，避免「什么都没生成却显示已生成潜台词」。
        """
        succeeded = False

        def merge(target: dict):
            nonlocal succeeded
            aug = augmentations.get(str(target.get('index')))
            if not isinstance(aug, dict):
                return
            result = target.setdefault('result', {})
            if 'intent_detail' in aug:
                result['interpretation'] = aug.get('interpretation')
                result['intent_detail'] = aug.get('intent_detail')
                result['emotion_detail'] = aug.get('emotion_detail')
                result['interpretation_failed'] = False
                result['gen_skipped'] = False
                succeeded = True
            elif kind == 'interpretation' and aug.get('gen_failed'):
                result['interpretation_failed'] = True
                result['gen_error'] = aug.get('gen_error')
            if 'suggestions' in aug:
                result['suggestions'] = aug.get('suggestions')
                result['gen_failed'] = False
                result['gen_skipped'] = False
                succeeded = True
            elif kind == 'suggestions' and aug.get('gen_failed'):
                result['gen_failed'] = True
                result['gen_error'] = aug.get('gen_error')

        for item in payload.get('analyses') or []:
            merge(item)
        # reply_target 也要合并（与 analyses 里同序号的那条是两份独立副本，只改一份会造成
        # 「同一个 index 两处数据不一致」）；它可能压根不在 analyses 里（比如只解读了对方）。
        if isinstance(payload.get('reply_target'), dict):
            merge(payload['reply_target'])
        if succeeded and kind == 'interpretation':
            payload['gen_interpretation'] = True
        if succeeded and kind == 'suggestions':
            payload['gen_suggestions'] = True
        return payload

    def apply_augmentations(self, session_id: str, augmentations: dict, kind: str) -> dict:
        """读会话 → 合并这一类生成结果 → 写回，整段加锁，返回合并后的完整结果。

        读-改-写必须原子：两类生成并发时，各自都以「最新」的会话为基准合并。
        """
        with self._lock_for(session_id):
            stored = self.load(session_id)
            merged = self.merge_augmentations(stored, augmentations, kind)
            meta = self.update_analysis(session_id, merged)
            return {**merged, **meta}

    def delete(self, ids: list[str]) -> int:
        if not isinstance(ids, list):
            raise InvalidRequest('要删除的会话列表格式不正确。')
        return self._store.delete(ids)

    # ---------- 转换 ----------
    def _derive(self, envelope: dict, transcript: str, title: str | None = None) -> dict:
        # 关系类型不在这里把关：分析接口已经校验过（analyze 的用户入参、append/augment 的 prev
        # 都会先被流水线拒绝）。落库是收尾动作，不该因为一个字段把已经算好的结果整单打回。
        relationship = envelope.get('relationship') or ''
        messages = envelope.get('messages') or []
        return {
            'title': (title or '').strip() or self._default_title(envelope),
            'relationship': relationship,
            'preview': self._preview(messages),
            'message_count': len(messages),
            'count_other': int(envelope.get('count_other') or 0),
            'count_me': int(envelope.get('count_me') or 0),
            'failed_count': int(envelope.get('failed_count') or 0),
            'gen_interpretation': bool(envelope.get('gen_interpretation')),
            'gen_suggestions': bool(envelope.get('gen_suggestions')),
            'transcript': transcript,
        }

    @staticmethod
    def _default_title(envelope: dict) -> str:
        other = (envelope.get('other_label') or '').strip()
        return other or DEFAULT_TITLE

    @staticmethod
    def _preview(messages: list[dict]) -> str:
        for item in reversed(messages):
            if item.get('speaker') == 'other' and item.get('text'):
                return str(item['text']).replace('\n', ' ')[:PREVIEW_CHARS]
        for item in reversed(messages):
            if item.get('text'):
                return str(item['text']).replace('\n', ' ')[:PREVIEW_CHARS]
        return ''

    def _to_detail(self, record: dict) -> dict:
        """存库结构 → 前端直接可用的会话详情（envelope + 会话元数据）。"""
        detail = dict(record.get('payload') or {})
        detail.update({
            'version': detail.get('version') or VERSION,
            'session_id': record['id'],
            'title': record.get('title') or '',
            'transcript': record.get('transcript') or '',
            'relationship': record.get('relationship') or detail.get('relationship') or '',
            'created_at': record.get('created_at') or 0,
            'updated_at': record.get('updated_at') or 0,
        })
        return detail
