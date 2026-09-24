"""流式（SSE）测试：半截 JSON 解析、流式客户端、事件序列、会话落库。

全部离线：客户端用假 httpx，上游用假分类/假生成，会话库落临时目录。
"""
from __future__ import annotations

import json

import pytest

from app.clients import general_llm
from app.core.exceptions import GeneralLLMError
from app.repositories.session_store import SessionStore
from app.services.generation import partial_items, partial_string
from app.services.sessions import SessionService
from tests.test_api import make_client

JSON = {'Content-Type': 'application/json'}
TRANSCRIPT = '我：在忙吗\n她：刚开完会\n我：那晚点说'


# ---------- 半截 JSON 解析 ----------
class TestPartialParsing:
    def test_string_grows_with_the_stream(self):
        pieces = ['{"', 'int', 'ent', '_detail', '":', ' "', '只', '确认', '身份', '，', '没', '打算', '多聊', '"}']
        seen, buffer = [], ''
        for piece in pieces:
            buffer += piece
            text = partial_string(buffer, 'intent_detail')
            if text != (seen[-1] if seen else ''):
                seen.append(text)
        assert seen[0] == '只'
        assert seen[-1] == '只确认身份，没打算多聊'
        assert len(seen) > 3, '应该随分片逐步变长，而不是最后才出现'

    def test_string_handles_escapes_and_half_escapes(self):
        assert partial_string('{"intent_detail": "带\\"引号\\"", "x": 1}', 'intent_detail') == '带"引号"'
        # 末尾停在半个 \u 转义上时，只还原已经完整的部分，不能崩
        assert partial_string('{"intent_detail": "\\u786e\\u8ba4\\u8eab', 'intent_detail') == '确认身'

    def test_string_missing_key_is_empty(self):
        assert partial_string('{"other": "x"}', 'intent_detail') == ''
        assert partial_string('', 'intent_detail') == ''

    def test_items_appear_one_by_one(self):
        payload = ('{"suggestions": [{"label": "甲", "text": "第一句"}, '
                   '{"label": "乙", "text": "第二句"}, {"label": "丙", "text": "第三句"}]}')
        counts = []
        for size in range(1, len(payload) + 1):
            found = partial_items(payload[:size], 'suggestions', 5)
            if not counts or counts[-1] != len(found):
                counts.append(len(found))
        assert counts == [0, 1, 2, 3]
        assert partial_items(payload, 'suggestions', 5)[0]['text'] == '第一句'
        assert len(partial_items(payload, 'suggestions', 2)) == 2, 'limit 生效'


# ---------- 流式客户端 ----------
class TestDeltasAreIncremental:
    """`delta.text` 必须是**增量片段**而不是累计全文（否则每片都在重发整句）。"""

    @staticmethod
    def _collect(buffers):
        """喂给 watcher 一串累计原文，收集它推出来的事件。"""
        from app.services.generation import GenerationService

        events = []
        watch, _reset = GenerationService._interpretation_watcher(events.append)
        for buffer in buffers:
            watch(buffer)
        return events

    def test_each_delta_carries_only_the_new_fragment(self):
        buffers = ['{"intent_detail": "撒',
                   '{"intent_detail": "撒娇',
                   '{"intent_detail": "撒娇式应',
                   '{"intent_detail": "撒娇式应下，安心',
                   '{"intent_detail": "撒娇式应下，安心等我接送"}']
        events = self._collect(buffers)
        assert [e['text'] for e in events] == ['撒', '娇', '式应', '下，安心', '等我接送']
        assert all(e['type'] == 'delta' for e in events)
        assert ''.join(e['text'] for e in events) == '撒娇式应下，安心等我接送'

    def test_unchanged_buffer_does_not_repeat(self):
        events = self._collect(['{"intent_detail": "在撒娇"}'] * 3)
        assert [e['text'] for e in events] == ['在撒娇'], '同一个累计值不该重复推'

    def test_rewritten_text_triggers_reset_then_resend(self):
        events = self._collect(['{"intent_detail": "在撒', '{"intent_detail": "其实是在撒娇"}'])
        assert [e['type'] for e in events] == ['delta', 'reset', 'delta']
        assert events[-1]['text'] == '其实是在撒娇'

    def test_reset_callback_clears_the_sent_prefix(self):
        from app.services.generation import GenerationService

        events = []
        watch, reset = GenerationService._interpretation_watcher(events.append)
        watch('{"intent_detail": "在撒娇"}')
        reset()
        watch('{"intent_detail": "在撒娇"}')
        assert [e['type'] for e in events] == ['delta', 'reset', 'delta']
        assert events[-1]['text'] == '在撒娇'


class TestSuggestionPreviews:
    """建议预览：逐条推，而且**重试之后要能重来**。

    这条曾经踩过坑：`reset` 只通知前端清屏，没把 watcher 里的「已推送条数」归零，
    于是重试那一轮的第一条被当成「发过了」（`state['sent']` 还停在上轮的值）而漏掉——
    预览在重试后彻底静默，用户只能等 `done`。
    """

    @staticmethod
    def _watcher(events):
        from app.services.generation import GenerationService

        return GenerationService._suggestions_watcher(events.append, 5)

    def test_items_are_pushed_one_by_one(self):
        events = []
        watch, _ = self._watcher(events)
        watch('{"suggestions": [{"label": "甲", "text": "第一句"}]}')
        watch('{"suggestions": [{"label": "甲", "text": "第一句"}, {"label": "乙", "text": "第二句"}]}')
        assert [e['suggestion']['text'] for e in events] == ['第一句', '第二句']
        assert all(e['type'] == 'item' and e['kind'] == 'suggestions' for e in events)

    def test_reset_lets_the_retry_push_again(self):
        events = []
        watch, reset = self._watcher(events)
        watch('{"suggestions": [{"label": "甲", "text": "半句"}]}')
        reset()
        watch('{"suggestions": [{"label": "甲", "text": "完整的第一句"}]}')
        assert [e['type'] for e in events] == ['item', 'reset', 'item']
        assert events[-1]['suggestion']['text'] == '完整的第一句'

    def test_generate_suggestions_wires_the_watcher_reset(self, monkeypatch):
        """端到端：真实客户端的重试路径要接到 watcher 的 reset 上，否则预览第二轮就没了。"""
        from app.services.generation import GenerationService

        truncated = FakeStreamResponse(lines=[
            'data: ' + json.dumps({'choices': [{'delta': {'content': '{"suggestions": [{"label": "甲", "text": "半句"}'},
                                                 'finish_reason': 'length'}]})])
        good = FakeStreamResponse(lines=sse_lines(
            '{"suggestions": [{"label": "甲", "text": "完整的第一句"}]}'))
        fake = FakeClient([truncated, good])
        use_fake_client(monkeypatch, fake)

        service = GenerationService(
            client=general_llm.GeneralLLMClient(settings=settings(gen_suggestions_count=5)))
        events = []
        service.generate_suggestions('恋爱', '对方：在忙吗', '在忙吗', 'other',
                                     {'label': '试探', 'score': 0.9},
                                     {'label': '无情绪', 'score': 0.9},
                                     on_event=events.append)
        assert [e['type'] for e in events] == ['item', 'reset', 'item']
        assert [e['suggestion']['text'] for e in events if e['type'] == 'item'] \
            == ['半句', '完整的第一句']


class FakeStreamResponse:
    def __init__(self, status_code=200, lines=(), text=''):
        self.status_code = status_code
        self._lines = list(lines)
        self.text = text

    def iter_lines(self):
        yield from self._lines

    def read(self):
        return self.text.encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeClient:
    """替掉共享客户端（clients/http.py）：按轮次依次返回预设响应，并记录每轮请求体。"""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.bodies = []

    def stream(self, method, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.bodies.append(json)
        return self._rounds.pop(0)

    def post(self, url, json=None, headers=None, timeout=None):
        self.bodies.append(json)
        return self._rounds.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def use_fake_client(monkeypatch, fake):
    """把生成层的 HTTP 客户端换成桩。

    客户端现在是 `clients/http.py` 里的进程级共享单例，桩要打在 `shared_client` 上：
    原来打 `httpx.Client` 那个缝已经不通了（共享客户端只在自己模块里建一次实例）。
    """
    monkeypatch.setattr(general_llm, 'shared_client', lambda name, **_: fake)


def sse_lines(*chunks: str) -> list[str]:
    return ['data: ' + json.dumps({'choices': [{'delta': {'content': chunk}}]}) for chunk in chunks]


def settings(**overrides):
    base = {'deepseek_api_key': 'sk-test', 'deepseek_model': 'fake-model',
            'deepseek_base_url': 'http://fake'}
    base.update(overrides)
    return type('S', (), base)()


class TestStreamJson:
    def test_deltas_are_forwarded_and_result_parsed(self, monkeypatch):
        fake = FakeClient([FakeStreamResponse(lines=sse_lines('{"intent_detail": "', '在撒娇', '"}'))])
        use_fake_client(monkeypatch, fake)
        seen = []
        client = general_llm.GeneralLLMClient(settings=settings())
        parsed = client.stream_json('sys', 'user', accept=lambda p: 'intent_detail' in p,
                                    on_delta=seen.append)
        assert parsed == {'intent_detail': '在撒娇'}
        assert seen[0].endswith('{"intent_detail": "')
        assert seen[-1] == '{"intent_detail": "在撒娇"}'
        assert fake.bodies[0]['stream'] is True, '流式请求必须带 stream: true'

    def test_truncated_answer_resets_then_retries_with_bigger_budget(self, monkeypatch):
        truncated = FakeStreamResponse(lines=[
            'data: ' + json.dumps({'choices': [{'delta': {'content': '{"intent_detail": "半'},
                                                 'finish_reason': 'length'}]})])
        good = FakeStreamResponse(lines=sse_lines('{"intent_detail": "完整的"}'))
        fake = FakeClient([truncated, good])
        use_fake_client(monkeypatch, fake)
        resets = []
        client = general_llm.GeneralLLMClient(settings=settings())
        parsed = client.stream_json('sys', 'user', accept=lambda p: 'intent_detail' in p,
                                    on_reset=lambda: resets.append(True))
        assert parsed == {'intent_detail': '完整的'}
        assert resets, '重试前必须让前端清掉上一轮的半截预览'
        assert fake.bodies[0]['max_tokens'] < fake.bodies[1]['max_tokens'], '重试要加大预算'

    def test_auth_failure_is_not_retried(self, monkeypatch):
        fake = FakeClient([FakeStreamResponse(status_code=401, text='bad key')])
        use_fake_client(monkeypatch, fake)
        client = general_llm.GeneralLLMClient(settings=settings())
        with pytest.raises(GeneralLLMError, match='鉴权失败'):
            client.stream_json('sys', 'user')
        assert len(fake.bodies) == 1


# ---------- 端点写法与报错文案（生成层不是「DeepSeek 专用」） ----------
class TestClientEndpointAndErrors:
    def test_base_url_with_or_without_the_chat_path(self):
        """两种写法都认：填到 /v1 自动补 /chat/completions；粘完整端点则原样用。"""
        from app.core.config import Settings

        def client(base_url):
            return general_llm.GeneralLLMClient(
                settings=Settings(deepseek_base_url=base_url, llm_profiles=''))

        assert client('https://api.stepfun.com/v1').endpoint == \
            'https://api.stepfun.com/v1/chat/completions'
        assert client('https://api.stepfun.com/step_plan/v1/chat/completions').endpoint == \
            'https://api.stepfun.com/step_plan/v1/chat/completions'

    def test_http_404_names_the_model_and_the_requested_url(self, monkeypatch):
        fake = FakeClient([FakeStreamResponse(status_code=404, text='{"error": "not found"}')])
        use_fake_client(monkeypatch, fake)
        client = general_llm.GeneralLLMClient(settings=settings(
            deepseek_model='step-5-preview', deepseek_base_url='https://api.stepfun.com/v1'))
        with pytest.raises(GeneralLLMError) as info:
            client.stream_json('sys', 'user')
        message = str(info.value)
        assert 'step-5-preview' in message, '报错要说清是哪套配置（模型名）出的问题'
        assert 'https://api.stepfun.com/v1/chat/completions' in message, '要带上实际请求的地址'
        assert 'DeepSeek' not in message, '生成层可以换任意端点，文案不许写死成某一家'
        assert len(fake.bodies) == 1, '404 属于不可重试'

    def test_missing_key_points_at_the_current_profile(self):
        client = general_llm.GeneralLLMClient(
            settings=settings(deepseek_api_key='', deepseek_model='qwen-plus'))
        with pytest.raises(GeneralLLMError) as info:
            client.complete_json('sys', 'user')
        assert 'qwen-plus' in str(info.value) and '生成层' in str(info.value)


# ---------- 流水线事件 ----------
class TestPipelineStreams:
    def test_concurrent_events_are_presented_in_message_order(self):
        from app.services.pipeline import _ordered_events

        arrived = [
            {'type': 'delta', 'index': 2, 'text': '后'},
            {'type': 'delta', 'index': 1, 'text': '先'},
            {'type': 'result', 'index': 2},
            {'type': 'delta', 'index': 1, 'text': '句'},
            {'type': 'result', 'index': 1},
        ]
        shown = list(_ordered_events(iter(arrived), [1, 2], {'result'}))
        assert [(event['type'], event['index']) for event in shown] == [
            ('delta', 1), ('delta', 1), ('result', 1),
            ('delta', 2), ('result', 2),
        ]

    def test_interpret_stream_emits_preview_then_done(self, tmp_path):
        from app.services.pipeline import PipelineService
        from app.services.review_pool import ReviewPool
        from tests.conftest import FakeClassifier, FakeGeneration

        service = PipelineService(classifier=FakeClassifier(label='陈述事实', emotion='无情绪'),
                                  generation=FakeGeneration(),
                                  pool=ReviewPool(path=tmp_path / 'pool.json'))
        analyzed = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱',
                                    'read_labels': ['我', '她']})
        events = list(service.interpret_stream({'prev': analyzed}))

        assert events[0]['type'] == 'start' and events[0]['kind'] == 'interpretation'
        assert events[0]['indexes'] == [a['index'] for a in analyzed['analyses']]
        previews = [e for e in events if e['type'] == 'delta']
        assert previews and all('index' in e for e in previews), '预览事件必须带序号'
        # text 是增量片段：接起来才等于最终值（不是每片都发累计全文）
        assert len(previews) > 1, '应当分成多片推送'
        for index in events[0]['indexes']:
            assert ''.join(e['text'] for e in previews if e['index'] == index) == '嘴上嫌弃实际在撒娇'
        shown_indexes = [e['index'] for e in events if 'index' in e]
        assert shown_indexes == sorted(shown_indexes)
        assert [e['index'] for e in events if e['type'] == 'result'] == events[0]['indexes']
        done = events[-1]
        assert done['type'] == 'done'
        assert all('intent_detail' in item for item in done['augmentations'].values())
        assert done['failed_indexes'] == []

    def test_suggest_stream_emits_items(self, tmp_path):
        from app.services.pipeline import PipelineService
        from app.services.review_pool import ReviewPool
        from tests.conftest import FakeClassifier, FakeGeneration

        service = PipelineService(classifier=FakeClassifier(label='陈述事实', emotion='无情绪'),
                                  generation=FakeGeneration(),
                                  pool=ReviewPool(path=tmp_path / 'pool.json'))
        analyzed = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})
        events = list(service.suggest_stream({'prev': analyzed}))
        items = [e for e in events if e['type'] == 'item']
        assert [i['suggestion']['text'] for i in items] == ['好呀']
        assert list(events[-1]['augmentations']) == [str(analyzed['messages'][-1]['index'])]

    def test_analyze_stream_reports_each_message(self, tmp_path):
        from app.services.pipeline import PipelineService
        from app.services.review_pool import ReviewPool
        from tests.conftest import FakeClassifier, FakeGeneration

        service = PipelineService(classifier=FakeClassifier(label='陈述事实', emotion='无情绪'),
                                  generation=FakeGeneration(),
                                  pool=ReviewPool(path=tmp_path / 'pool.json'))
        events = list(service.analyze_stream({'transcript': TRANSCRIPT, 'relationship': '恋爱',
                                              'read_labels': ['我', '她']}))
        assert events[0]['type'] == 'start'
        assert events[0]['total'] == 3 and len(events[0]['messages']) == 3
        finished = [e['item']['index'] for e in events if e['type'] == 'message']
        assert finished == [1, 2, 3]
        done = events[-1]
        assert done['type'] == 'done' and done['data']['count'] == 3

    def test_analyze_stream_marks_retry_then_recovers(self, tmp_path):
        import app.services.pipeline as pipeline_module
        from app.services.pipeline import PipelineService
        from app.services.review_pool import ReviewPool
        from tests.conftest import FakeClassifier, FakeGeneration

        service = PipelineService(classifier=FakeClassifier(label='陈述事实', emotion='无情绪',
                                                            fail_times=1),
                                  generation=FakeGeneration(),
                                  pool=ReviewPool(path=tmp_path / 'pool.json'))
        events = []
        original = pipeline_module.time.sleep
        pipeline_module.time.sleep = lambda *_: None
        try:
            events = list(service.analyze_stream({'transcript': TRANSCRIPT, 'relationship': '恋爱'}))
        finally:
            pipeline_module.time.sleep = original
        assert [e['type'] for e in events].count('retrying') == 1
        assert not [e for e in events if e['type'] == 'failed']
        assert events[-1]['data']['failed_count'] == 0

    def test_upstream_rejection_surfaces_instead_of_hanging(self, tmp_path):
        """上游直接拒绝（401/400）必须原地抛错，不能卡在等事件上。

        用户真踩过：分类层 401 时流式请求永不返回——前端一直「生成中」，
        既没有 done 事件、也不会落库，看起来就像「解析完什么都没保存」。
        """
        from app.core.exceptions import JevAPIError
        from app.services.pipeline import PipelineService
        from app.services.review_pool import ReviewPool
        from tests.conftest import FakeClassifier, FakeGeneration

        service = PipelineService(classifier=FakeClassifier(error=JevAPIError(401, 'unauthorized')),
                                  generation=FakeGeneration(),
                                  pool=ReviewPool(path=tmp_path / 'pool.json'))
        with pytest.raises(JevAPIError, match='401'):
            list(service.analyze_stream({'transcript': TRANSCRIPT, 'relationship': '恋爱'}))

    def test_analyze_stream_raises_when_everything_fails(self, tmp_path):
        import app.services.pipeline as pipeline_module
        from app.core.exceptions import JevConnectionError
        from app.services.pipeline import PipelineService
        from app.services.review_pool import ReviewPool
        from tests.conftest import FakeClassifier, FakeGeneration

        service = PipelineService(classifier=FakeClassifier(label='陈述事实', emotion='无情绪',
                                                            fail_times=99),
                                  generation=FakeGeneration(),
                                  pool=ReviewPool(path=tmp_path / 'pool.json'))
        original = pipeline_module.time.sleep
        pipeline_module.time.sleep = lambda *_: None
        try:
            with pytest.raises(JevConnectionError):
                list(service.analyze_stream({'transcript': TRANSCRIPT, 'relationship': '恋爱'}))
        finally:
            pipeline_module.time.sleep = original


# ---------- SSE 端点 ----------
@pytest.fixture
def client(real_pipeline, tmp_path):
    service = SessionService(SessionStore(tmp_path / 'sessions.db'))
    return make_client(pipeline=real_pipeline, sessions=service), service


def events_of(response) -> list[dict]:
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    return [json.loads(line[5:].strip()) for line in response.text.splitlines()
            if line.startswith('data:')]


class TestStreamEndpoints:
    def test_analyze_stream_stores_session_and_reports_progress(self, client):
        http, service = client
        response = http.post('/analyze-chat/stream',
                             json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                             headers=JSON)
        events = events_of(response)
        assert events[0]['type'] == 'start'
        kinds = [e['type'] for e in events]
        assert kinds.count('message') == 1 and kinds[-1] == 'done'
        done = events[-1]
        assert done['data']['count'] == 1
        assert done['session_id'], 'done 事件必须带上落库后的会话 id'
        assert service.load(done['session_id']) is not None

    def test_interpret_stream_forwards_deltas_and_writes_session(self, client):
        http, service = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        events = events_of(http.post('/interpret-chat/stream',
                                     json={'session_id': created['session_id']}, headers=JSON))
        assert [e['type'] for e in events][:2] == ['start', 'delta']
        assert events[-1]['type'] == 'done' and events[-1]['session_id'] == created['session_id']
        stored = service.load(created['session_id'])
        assert stored['analyses'][0]['result']['intent_detail'] == '嘴上嫌弃实际在撒娇'

    def test_suggest_stream_forwards_items(self, client):
        http, service = client
        created = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                            headers=JSON).json()
        events = events_of(http.post('/suggest-chat/stream',
                                     json={'session_id': created['session_id']}, headers=JSON))
        items = [e for e in events if e['type'] == 'item']
        assert items and items[0]['suggestion']['text'] == '好呀'
        assert items[0]['index'] == created['messages'][-1]['index']

    def test_business_error_arrives_as_error_event(self, client):
        """流式端点的业务错误：HTTP 仍是 200，错误靠事件里的中文提示传达。"""
        http, _ = client
        events = events_of(http.post('/interpret-chat/stream', json={}, headers=JSON))
        assert events[-1]['type'] == 'error'
        assert '缺少上一次分析结果' in events[-1]['message']

    def test_invalid_request_event_matches_plain_endpoint(self, client):
        http, _ = client
        events = events_of(http.post('/analyze-chat/stream',
                                     json={'transcript': TRANSCRIPT, 'relationship': '网友'},
                                     headers=JSON))
        assert events[-1]['type'] == 'error'
        plain = http.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '网友'},
                          headers=JSON)
        assert events[-1]['message'] == plain.json()['error'], '两条路径的错误文案必须一致'

    def test_upstream_rejection_becomes_an_error_event_and_stores_nothing(self, tmp_path):
        """端到端：上游拒绝 → 收到 error 事件（HTTP 仍 200），且不落库。

        「分析成功才存档」是刻意口径：整体失败时不往侧栏塞一条空记录。
        """
        from app.core.exceptions import JevAPIError
        from app.repositories.session_store import SessionStore
        from app.services.pipeline import PipelineService
        from app.services.review_pool import ReviewPool
        from app.services.sessions import SessionService
        from tests.conftest import FakeClassifier, FakeGeneration

        pipeline = PipelineService(classifier=FakeClassifier(error=JevAPIError(401, 'unauthorized')),
                                   generation=FakeGeneration(),
                                   pool=ReviewPool(path=tmp_path / 'pool.json'))
        sessions = SessionService(SessionStore(tmp_path / 'sessions.db'))
        http = make_client(pipeline=pipeline, sessions=sessions)
        events = events_of(http.post('/analyze-chat/stream',
                                     json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                                     headers=JSON))
        assert events[-1]['type'] == 'error'
        assert events[-1]['code'] == 'JEV_HTTP_401' and events[-1]['status'] == 502
        assert '401' in events[-1]['message']
        assert sessions.list_sessions()['total'] == 0, '整体失败不该留下会话记录'
