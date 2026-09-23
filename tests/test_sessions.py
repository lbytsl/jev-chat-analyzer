"""会话存储与服务层测试：落库规则、原地更新 vs 新建、合并补跑结果、删除/重命名。"""
from __future__ import annotations

import pytest

from app.core.exceptions import InvalidRequest, NotFoundError
from app.repositories.session_store import SessionStore
from app.services.sessions import SessionService


def envelope(relationship='恋爱', texts=('她：在吗',), other_label='她', transcript=None):
    messages = [{'index': i + 1, 'speaker': 'other' if t.startswith('她') else 'me',
                 'label': t.split('：')[0], 'text': t.split('：', 1)[1]} for i, t in enumerate(texts)]
    return {
        'version': 'v008', 'relationship': relationship, 'messages': messages,
        'analyses': [{'index': m['index'], 'speaker': '对方', 'label': m['label'], 'message': m['text'],
                      'context': '', 'result': {'primary_intent': {'key': '接住话了', 'label': '接住话了',
                                                                   'score': 0.5, 'ranked': []},
                                                'emotion': {'key': '无情绪', 'label': '无情绪',
                                                            'score': 0.5, 'ranked': []},
                                                'gen_skipped': True}} for m in messages],
        'count': len(messages), 'count_other': sum(1 for m in messages if m['speaker'] == 'other'),
        'count_me': sum(1 for m in messages if m['speaker'] == 'me'), 'failed_count': 0,
        'failed_indexes': [], 'me_label': '我', 'other_label': other_label,
        'other_labels': [other_label], 'read_labels': [other_label],
        'gen_interpretation': False, 'gen_suggestions': False, 'reply_target': None,
    }


class TestSessionStore:
    def test_upsert_and_get_roundtrip(self, tmp_path):
        store = SessionStore(tmp_path / 'db.sqlite')
        record = {'title': '她', 'relationship': '恋爱', 'transcript': '她：在吗',
                  'preview': '在吗', 'message_count': 1, 'payload': {'a': 1}}
        saved = store.upsert(record)
        loaded = store.get(saved['id'])
        assert loaded['title'] == '她' and loaded['payload'] == {'a': 1}
        assert loaded['created_at'] and loaded['created_at'] == loaded['updated_at']

    def test_update_keeps_created_at_and_payload_is_replaced(self, tmp_path):
        store = SessionStore(tmp_path / 'db.sqlite')
        saved = store.upsert({'title': 'A', 'relationship': '恋爱', 'payload': {'v': 1}})
        again = store.upsert({'id': saved['id'], 'title': 'B', 'relationship': '恋爱', 'payload': {'v': 2}})
        loaded = store.get(saved['id'])
        assert again['created_at'] == saved['created_at']
        assert loaded['title'] == 'B' and loaded['payload'] == {'v': 2}

    def test_list_is_ordered_by_updated_at_desc(self, tmp_path):
        store = SessionStore(tmp_path / 'db.sqlite')
        first = store.upsert({'title': '旧', 'relationship': '恋爱'})
        second = store.upsert({'title': '新', 'relationship': '恋爱'})
        # 把第一条再写一次 → 它变成最近活跃
        store.upsert({'id': first['id'], 'title': '旧（刚更新）', 'relationship': '恋爱'})
        ids = [row['id'] for row in store.list_meta(10)]
        assert ids[0] == first['id'] and second['id'] in ids

    def test_updated_at_is_strictly_increasing(self, tmp_path):
        """同一毫秒内的连续写入也必须能排出先后，否则列表顺序会随机跳。"""
        store = SessionStore(tmp_path / 'db.sqlite')
        created = [store.upsert({'title': '会话{}'.format(i), 'relationship': '恋爱'})
                   for i in range(5)]
        stamps = [item['updated_at'] for item in created]
        assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
        assert store.list_meta(10)[0]['id'] == created[-1]['id'], '最后写入的排第一'

    def test_delete_many(self, tmp_path):
        store = SessionStore(tmp_path / 'db.sqlite')
        a = store.upsert({'title': 'A', 'relationship': '恋爱'})
        b = store.upsert({'title': 'B', 'relationship': '恋爱'})
        c = store.upsert({'title': 'C', 'relationship': '恋爱'})
        assert store.delete([a['id'], b['id']]) == 2
        assert store.count() == 1
        assert store.get(a['id']) is None and store.get(c['id']) is not None
        assert store.delete([]) == 0


class TestSessionService:
    def test_save_analysis_creates_session_with_derived_meta(self, session_service):
        meta = session_service.save_analysis(envelope(texts=('她：在吗', '我：在')), '她：在吗\n我：在')
        assert meta['session_id'] and meta['title'] == '她'
        listed = session_service.list_sessions()
        assert listed['total'] == 1
        row = listed['sessions'][0]
        assert row['message_count'] == 2 and row['count_other'] == 1 and row['count_me'] == 1
        assert row['preview'] == '在吗' and row['relationship'] == '恋爱'

    def test_same_transcript_updates_in_place(self, session_service):
        first = session_service.save_analysis(envelope(), '她：在吗')
        again = session_service.save_analysis(envelope(), '她：在吗', session_id=first['session_id'])
        assert again['session_id'] == first['session_id']
        assert session_service.list_sessions()['total'] == 1

    def test_different_transcript_creates_new_session(self, session_service):
        first = session_service.save_analysis(envelope(), '她：在吗')
        second = session_service.save_analysis(envelope(texts=('她：换一段',)), '她：换一段',
                                              session_id=first['session_id'])
        assert second['session_id'] != first['session_id']
        assert session_service.list_sessions()['total'] == 2
        # 旧会话原样还在
        assert session_service.load(first['session_id'])['transcript'] == '她：在吗'

    def test_update_analysis_keeps_transcript_when_not_given(self, session_service):
        """补跑生成层不会改正文：transcript 缺省时沿用库里那份，只替换分析结果。"""
        first = session_service.save_analysis(envelope(), '她：在吗')
        session_service.update_analysis(first['session_id'], envelope(texts=('她：追加',)))
        detail = session_service.load(first['session_id'])
        assert detail['transcript'] == '她：在吗'
        assert detail['analyses'][0]['message'] == '追加'

    def test_update_analysis_rejects_missing_session(self, session_service):
        with pytest.raises(NotFoundError):
            session_service.update_analysis('nope', envelope())

    def test_load_unknown_raises_not_found(self, session_service):
        with pytest.raises(NotFoundError):
            session_service.load('nope')

    def test_detail_contains_envelope_and_meta(self, session_service):
        first = session_service.save_analysis(envelope(), '她：在吗')
        detail = session_service.load(first['session_id'])
        assert detail['messages'] and detail['analyses']
        assert detail['session_id'] == first['session_id'] and detail['transcript'] == '她：在吗'
        assert detail['version'] == 'v008'

    def test_meta_is_not_duplicated_inside_payload(self, tmp_path):
        """会话元数据由表列负责；payload 里再存一份迟早会前后不一致。"""
        store = SessionStore(tmp_path / 'db.sqlite')
        service = SessionService(store)
        saved = service.save_analysis(envelope(), '她：在吗')
        payload = store.get(saved['session_id'])['payload']
        assert not ({'session_id', 'title', 'transcript', 'created_at', 'updated_at'} & set(payload))

    def test_list_limit_is_clamped(self, session_service):
        for i in range(3):
            session_service.save_analysis(envelope(texts=(f'她：第{i}条',)), f'她：第{i}条')
        assert len(session_service.list_sessions(limit=2)['sessions']) == 2
        assert session_service.list_sessions(limit=999)['total'] == 3


class TestMergeAugmentations:
    """一次只合并一类（潜台词 / 推荐回复各自一个端点），按字段是否存在判断。"""

    def test_interpretation_merges_into_analyses(self, session_service):
        payload = envelope(texts=('她：在吗',))
        augmentations = {'1': {'intent_detail': '其实是在等你先开口', 'interpretation': '',
                               'emotion_detail': '', 'gen_failed': False}}
        merged = SessionService.merge_augmentations(payload, augmentations, 'interpretation')
        item = merged['analyses'][0]['result']
        assert item['intent_detail'] == '其实是在等你先开口'
        assert item['gen_skipped'] is False
        assert merged['gen_interpretation'] is True
        # 只跑潜台词不该动推荐回复的开关，也不该凭空造出 suggestions
        assert merged['gen_suggestions'] is False and item.get('suggestions') is None

    def test_suggestions_merges_into_reply_target(self, session_service):
        payload = envelope(texts=('她：在吗',))
        payload['reply_target'] = {'index': 1, 'speaker': '对方', 'result': {}}
        augmentations = {'1': {'suggestions': [{'label': '接住', 'text': '在的'}], 'gen_failed': False}}
        merged = SessionService.merge_augmentations(payload, augmentations, 'suggestions')
        assert merged['reply_target']['result']['suggestions'][0]['text'] == '在的'
        assert merged['gen_suggestions'] is True
        assert merged['analyses'][0]['result'].get('intent_detail') is None

    def test_interpretation_merges_the_reply_target_copy_too(self, session_service):
        """reply_target 与 analyses 里同序号的那条是两份副本，潜台词必须两处一起更新。

        实际踩到的坑：只更新 analyses，同一 index 的两份数据分叉，卡片与回复面板各显示一套。
        这里顺带覆盖「模型返回空潜台词」的情形（空串要如实写进去，而不是留着旧值）。
        """
        payload = envelope(texts=('她：在吗',))
        payload['reply_target'] = {'index': 1, 'speaker': '对方',
                                   'result': {'intent_detail': '上一版潜台词'}}
        merged = SessionService.merge_augmentations(
            payload, {'1': {'intent_detail': '', 'gen_failed': False}}, 'interpretation')
        assert merged['analyses'][0]['result']['intent_detail'] == ''
        assert merged['reply_target']['result']['intent_detail'] == ''

    def test_failure_marks_only_that_message(self, session_service):
        payload = envelope(texts=('她：在吗', '她：怎么不回',))
        augmentations = {'2': {'gen_failed': True, 'gen_error': '上游 402'}}
        merged = SessionService.merge_augmentations(payload, augmentations, 'suggestions')
        assert merged['analyses'][0]['result'].get('gen_failed') is None
        assert merged['analyses'][1]['result']['gen_failed'] is True
        assert merged['analyses'][1]['result']['gen_error'] == '上游 402'

    def test_interpretation_failure_does_not_touch_suggestions_flag(self, session_service):
        """潜台词失败只标 interpretation_failed，不能让底部回复面板跟着报「回复建议失败」。"""
        payload = envelope(texts=('她：在吗',))
        merged = SessionService.merge_augmentations(payload, {'1': {'gen_failed': True}},
                                                   'interpretation')
        result = merged['analyses'][0]['result']
        assert result['interpretation_failed'] is True
        assert result.get('gen_failed') in (None, False)

    def test_empty_augmentations_do_not_flip_flags(self, session_service):
        """一次什么都没生成的补跑（例如指定的序号不在解读范围内）不该改会话开关。"""
        payload = envelope(texts=('她：在吗',))
        payload['gen_interpretation'] = False
        payload['gen_suggestions'] = False
        merged = SessionService.merge_augmentations(payload, {}, 'interpretation')
        assert merged['gen_interpretation'] is False and merged['gen_suggestions'] is False
        assert merged['analyses'][0]['result']['gen_skipped'] is True

    def test_concurrent_kinds_do_not_overwrite_each_other(self, session_service):
        """两类生成并发写同一会话：读-改-写加锁，两个结果都必须留下来。"""
        import threading

        saved = session_service.save_analysis(envelope(texts=('她：在吗',)), '她：在吗',
                                              None)
        session_id = saved['session_id']
        barrier = threading.Barrier(2)

        def worker(kind, augmentation):
            barrier.wait()
            session_service.apply_augmentations(session_id, {'1': augmentation}, kind)

        threads = [
            threading.Thread(target=worker, args=('interpretation',
                                                  {'intent_detail': '潜台词', 'gen_failed': False})),
            threading.Thread(target=worker, args=('suggestions',
                                                  {'suggestions': [{'label': '接住', 'text': '在的'}],
                                                   'gen_failed': False})),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        result = session_service.load(session_id)['analyses'][0]['result']
        assert result['intent_detail'] == '潜台词'
        assert result['suggestions'][0]['text'] == '在的'

    def test_merge_never_clears_the_other_kind(self, session_service):
        """已有潜台词的会话补跑推荐回复后，潜台词必须还在。"""
        payload = envelope(texts=('她：在吗',))
        payload['analyses'][0]['result']['intent_detail'] = '原来的潜台词'
        payload['gen_interpretation'] = True
        merged = SessionService.merge_augmentations(
            payload, {'1': {'suggestions': [{'label': 'a', 'text': 'b'}], 'gen_failed': False}},
            'suggestions')
        result = merged['analyses'][0]['result']
        assert result['intent_detail'] == '原来的潜台词'
        assert result['suggestions'][0]['text'] == 'b'
