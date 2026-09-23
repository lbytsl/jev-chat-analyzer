"""会话接口测试：导入即存档、切换读取、重命名、单个/多选删除，以及单句潜台词。

这里用真流水线（假分类 + 假生成），所以落库的是真实的 envelope 结构，
能覆盖「分析 → 落库 → 补跑 → 合并 → 再读取」这条完整链路；全程离线。
"""
from __future__ import annotations

import json


import pytest

from app.repositories.session_store import SessionStore
from app.services.sessions import SessionService
from tests.test_api import make_client

JSON = {'Content-Type': 'application/json'}
TRANSCRIPT = '我：在忙吗\n她：刚开完会'


@pytest.fixture
def client(real_pipeline, tmp_path):
    service = SessionService(SessionStore(tmp_path / 'sessions.db'))
    return make_client(pipeline=real_pipeline, sessions=service), service


class TestImportCreatesSession:
    def test_analyze_chat_stores_a_session(self, client):
        http, service = client
        body = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                         headers=JSON).json()
        assert body['session_id']
        listed = http.get('/sessions').json()
        assert listed['total'] == 1
        row = listed['sessions'][0]
        assert row['title'] == '她' and row['message_count'] == 2
        assert row['preview'] == '刚开完会' and row['relationship'] == '恋爱'
        assert row['updated_at'] > 0 and row['count_other'] == 1

    def test_same_transcript_reanalyze_updates_in_place(self, client):
        http, _ = client
        first = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                          headers=JSON).json()
        again = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱',
                                                 'session_id': first['session_id']},
                          headers=JSON).json()
        assert again['session_id'] == first['session_id']
        assert http.get('/sessions').json()['total'] == 1

    def test_different_transcript_creates_second_session(self, client):
        http, _ = client
        first = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                          headers=JSON).json()
        second = http.post('/analyze-chat',
                           json={'transcript': TRANSCRIPT + '\n她：那明天聊', 'relationship': '恋爱',
                                 'session_id': first['session_id']},
                           headers=JSON).json()
        assert second['session_id'] != first['session_id']
        assert http.get('/sessions').json()['total'] == 2

    def test_switch_back_restores_everything(self, client):
        http, _ = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱',
                                                   'gen_suggestions': False}, headers=JSON).json()
        detail = http.get('/sessions/' + created['session_id']).json()
        for key in ('messages', 'analyses', 'relationship', 'me_label', 'read_labels',
                    'gen_interpretation', 'gen_suggestions', 'reply_target', 'count',
                    'other_label'):
            assert key in detail, key
        assert detail['transcript'] == TRANSCRIPT
        assert [item['index'] for item in detail['analyses']] == [2]


class TestDelete:
    def test_delete_single(self, client):
        http, _ = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        assert http.delete('/sessions/' + created['session_id']).json()['deleted'] == 1
        assert http.get('/sessions').json()['total'] == 0
        missing = http.get('/sessions/' + created['session_id'])
        assert missing.status_code == 404 and '不存在' in missing.json()['error']
        assert http.delete('/sessions/' + created['session_id']).status_code == 404

    def test_delete_many(self, client):
        http, _ = client
        ids = [http.post('/analyze-chat',
                         json={'transcript': TRANSCRIPT + '\n她：第{}条'.format(i),
                               'relationship': '恋爱'}, headers=JSON).json()['session_id']
               for i in range(3)]
        response = http.post('/sessions/delete', json={'ids': ids[:2]}, headers=JSON)
        assert response.status_code == 200 and response.json()['deleted'] == 2
        assert http.get('/sessions').json()['total'] == 1

    def test_delete_many_requires_ids(self, client):
        http, _ = client
        response = http.post('/sessions/delete', json={'ids': []}, headers=JSON)
        assert response.status_code == 400 and '勾选' in response.json()['error']

    def test_delete_without_body_is_allowed(self, client):
        """DELETE 没有请求体：不能被体积校验挡掉（Content-Length 为 0）。"""
        http, _ = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        assert http.delete('/sessions/' + created['session_id']).status_code == 200


class TestAppendUpdatesSession:
    def test_append_keeps_old_results_and_updates_session(self, client):
        http, service = client
        first = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                          headers=JSON).json()
        extended = TRANSCRIPT + '\n她：那明天聊'
        appended = http.post('/append-chat',
                             json={'session_id': first['session_id'], 'transcript': extended,
                                   'old_count': len(first['messages'])},
                             headers=JSON).json()
        assert appended['session_id'] == first['session_id']
        assert appended['count'] == 2
        assert appended['analyses'][0]['result'] == first['analyses'][0]['result']
        detail = service.load(first['session_id'])
        assert len(detail['messages']) == 3 and detail['transcript'] == extended
        # 列表元数据也跟着更新（侧栏显示条数用的就是它）
        row = service.list_sessions()['sessions'][0]
        assert row['message_count'] == 3 and row['preview'] == '那明天聊'


class TestInterpretPersists:
    """潜台词端点：只产出 intent_detail，并写回会话。"""

    def test_returns_and_stores_merged_result(self, client):
        http, service = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        response = http.post('/interpret-chat', json={'session_id': created['session_id']},
                             headers=JSON).json()
        assert response['session_id'] == created['session_id'] and response['kind'] == 'interpretation'
        # 响应只带本次增量：整份会话不再回带（会夹带另一类的存量字段）
        assert 'session' not in response
        augmentation = response['augmentations'][str(created['analyses'][0]['index'])]
        assert augmentation['intent_detail'] == '嘴上嫌弃实际在撒娇'
        stored = service.load(created['session_id'])
        assert stored['gen_interpretation'] is True
        assert stored['analyses'][0]['result']['intent_detail'] == '嘴上嫌弃实际在撒娇'
        # 只跑潜台词不该产生回复建议
        assert stored['analyses'][0]['result'].get('suggestions') is None

    def test_single_message_by_index(self, client):
        """单条潜台词：只生成指定那一条，其他条一个字都不动。"""
        http, service = client
        transcript = '我：在忙吗\n她：刚开完会\n我：那晚点说'
        created = http.post('/analyze-chat',
                            json={'transcript': transcript, 'relationship': '恋爱',
                                  'read_labels': ['我', '她']}, headers=JSON).json()
        response = http.post('/interpret-chat',
                             json={'session_id': created['session_id'], 'indexes': [3]},
                             headers=JSON).json()
        assert list(response['augmentations']) == ['3']
        stored = service.load(created['session_id'])
        results = {item['index']: item['result'] for item in stored['analyses']}
        assert results[3]['intent_detail'] == '嘴上嫌弃实际在撒娇'
        assert results[3]['gen_skipped'] is False
        assert results[1].get('intent_detail') is None and results[1]['gen_skipped'] is True

    def test_indexes_must_not_be_empty(self, client):
        http, _ = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        response = http.post('/interpret-chat',
                             json={'session_id': created['session_id'], 'indexes': []}, headers=JSON)
        assert '序号' in response.json()['error']

    def test_legacy_prev_path_still_works(self, client):
        """老路径（只带 prev、不带 session_id）保持原样：只补生成，不落库。"""
        http, service = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        response = http.post('/interpret-chat', json={'prev': created}, headers=JSON).json()
        assert 'session' not in response
        assert response['augmentations']
        # 老路径不写库：库里那条仍旧没生成过潜台词
        assert service.load(created['session_id'])['analyses'][0]['result'].get('intent_detail') is None

    def test_unknown_session_is_404(self, client):
        http, _ = client
        response = http.post('/interpret-chat', json={'session_id': 'nope'}, headers=JSON)
        assert response.status_code == 404


class TestSuggestPersists:
    """推荐回复端点：只产出 suggestions，默认锚定全局最后一条。"""

    def test_defaults_to_last_message_and_stores(self, client):
        http, service = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        response = http.post('/suggest-chat', json={'session_id': created['session_id']},
                             headers=JSON).json()
        assert response['kind'] == 'suggestions'
        assert list(response['augmentations']) == ['2']
        stored = service.load(created['session_id'])
        assert stored['gen_suggestions'] is True
        assert stored['reply_target']['result']['suggestions'][0]['label'] == '接住'
        # 只跑推荐回复不该往会话里写潜台词
        assert stored['reply_target']['result'].get('intent_detail') is None
        assert stored['gen_interpretation'] is False

    def test_indexes_can_target_any_message(self, client):
        """指定序号后不再受「推荐回复只跑最后一条」的限制。"""
        http, service = client
        transcript = '我：在忙吗\n她：刚开完会'
        created = http.post('/analyze-chat',
                            json={'transcript': transcript, 'relationship': '恋爱',
                                  'read_labels': ['我', '她']}, headers=JSON).json()
        response = http.post('/suggest-chat',
                             json={'session_id': created['session_id'], 'indexes': [1]},
                             headers=JSON).json()
        assert list(response['augmentations']) == ['1']
        stored = service.load(created['session_id'])
        results = {item['index']: item['result'] for item in stored['analyses']}
        assert results[1]['suggestions'][0]['label'] == '接住'
        assert results[2].get('suggestions') is None

    def test_two_endpoints_do_not_mix(self, client):
        """先补潜台词、再补推荐回复：两次各自合并，互不覆盖。"""
        http, service = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        http.post('/interpret-chat', json={'session_id': created['session_id']}, headers=JSON)
        http.post('/suggest-chat', json={'session_id': created['session_id']}, headers=JSON)
        detail = service.load(created['session_id'])
        assert detail['analyses'][0]['result']['intent_detail'] == '嘴上嫌弃实际在撒娇'
        assert detail['reply_target']['result']['suggestions'][0]['label'] == '接住'
        assert detail['gen_interpretation'] is True and detail['gen_suggestions'] is True


class TestResponsesStaySeparate:
    """两个端点的响应只带自己那一类产物。

    背景：早先的响应会回带整份会话，于是「推荐回复」的返回里夹着**存量**的潜台词字段，
    看起来像这个端点也生成了潜台词。这里把「互不夹带」钉成契约。
    """

    def test_suggestions_response_has_no_interpretation_fields(self, client):
        http, _ = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        # 先造出存量潜台词，确认它不会混进推荐回复的响应
        http.post('/interpret-chat', json={'session_id': created['session_id']}, headers=JSON)
        response = http.post('/suggest-chat', json={'session_id': created['session_id']},
                             headers=JSON).json()
        assert response['kind'] == 'suggestions'
        assert 'session' not in response
        assert set(response['augmentations']['2']) == {'suggestions', 'gen_failed', 'gen_error'}
        blob = json.dumps(response, ensure_ascii=False)
        for field in ('intent_detail', 'interpretation', 'emotion_detail', 'gen_interpretation'):
            assert field not in blob, field

    def test_interpretation_response_has_no_suggestion_fields(self, client):
        http, _ = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        http.post('/suggest-chat', json={'session_id': created['session_id']}, headers=JSON)
        response = http.post('/interpret-chat', json={'session_id': created['session_id']},
                             headers=JSON).json()
        assert response['kind'] == 'interpretation'
        assert 'session' not in response
        only_index = next(iter(response['augmentations']))
        assert set(response['augmentations'][only_index]) == {
            'interpretation', 'intent_detail', 'emotion_detail', 'gen_failed', 'gen_error'}
        assert 'suggestions' not in json.dumps(response, ensure_ascii=False)


class TestSessionList:
    def test_limit_and_total(self, client):
        http, _ = client
        for i in range(3):
            http.post('/analyze-chat',
                      json={'transcript': TRANSCRIPT + '\n她：第{}条'.format(i),
                            'relationship': '恋爱'}, headers=JSON)
        listed = http.get('/sessions?limit=2').json()
        assert len(listed['sessions']) == 2 and listed['total'] == 3

    def test_empty_library(self, client):
        http, _ = client
        assert http.get('/sessions').json() == {'sessions': [], 'total': 0}
