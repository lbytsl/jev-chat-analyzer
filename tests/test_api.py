"""接口契约测试：状态码、错误体形状、响应头、来源守卫。

这些断言就是前端 `index.html` 依赖的契约——`callAPI` 只读 `error` 字段，
所以任何错误都必须保持 `{"error": "…中文提示…"}` 的形状。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    get_classifier,
    get_pipeline,
    get_session_service,
    get_settings_service,
)
from app.api.errors import auth_hint, jev_api_error_message
from app.core.config import FRONTEND_INDEX, MAX_BODY_BYTES, VERSION
from app.core.exceptions import (
    GeneralLLMError,
    InvalidRequest,
    JevAPIError,
    JevConfigurationError,
    JevConnectionError,
    JevResponseError,
)
from app.main import create_app
from app.repositories.session_store import SessionStore
from app.services.sessions import SessionService

TRANSCRIPT = '我：在忙吗\n她：刚开完会'


class FakePipeline:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.analyze_payloads: list[dict] = []

    def _record(self, data: dict) -> dict:
        self.analyze_payloads.append(data)
        if self.error:
            raise self.error
        return {'version': 'v008', 'relationship': data.get('relationship'), 'messages': [],
                'analyses': [], 'count': 0}

    def analyze(self, data: dict) -> dict:
        return self._record(data)

    def interpret(self, data: dict) -> dict:
        return self._record(data)

    def suggest(self, data: dict) -> dict:
        return self._record(data)

    def append(self, data: dict) -> dict:
        return self._record(data)


class FakeClassifier:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.payloads: list[dict] = []

    def classify_payload(self, data: dict, want_interpretation: bool = False,
                         want_suggestions: bool = False) -> dict:
        self.payloads.append(data)
        if self.error:
            raise self.error
        return {'version': 'v008', 'primary_intent': {'key': '接住话了'}}


def make_client(pipeline=None, classifier=None, sessions=None, settings_service=None) -> TestClient:
    app = create_app()
    if pipeline is not None:
        app.dependency_overrides[get_pipeline] = lambda: pipeline
    if classifier is not None:
        app.dependency_overrides[get_classifier] = lambda: classifier
    # 会话库固定换到临时目录：接口测试会真的写会话，绝不能落到 var/sessions.db。
    app.dependency_overrides[get_session_service] = lambda: (
        sessions or SessionService(SessionStore(Path(tempfile.mkdtemp()) / 'sessions.db')))
    # 配置测试同理：注入的实例指向临时 .env，绝不改写真实密钥文件。
    if settings_service is not None:
        app.dependency_overrides[get_settings_service] = lambda: settings_service
    return TestClient(app, raise_server_exceptions=False)


JSON = {'Content-Type': 'application/json'}


class TestPages:
    def test_health(self):
        response = make_client().get('/health')
        assert response.status_code == 200
        body = response.json()
        assert body['version'] == VERSION
        assert set(body) == {'ok', 'api_key', 'general_llm', 'model', 'gen_model', 'version'}
        assert response.headers['X-Jev'] == VERSION
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'

    def test_index_page(self):
        """前端是 Vite 构建产物：已构建就托管页面，没构建就明确告诉用户去 build。"""
        response = make_client().get('/')
        if FRONTEND_INDEX.is_file():
            assert response.status_code == 200
            assert response.headers['content-type'].startswith('text/html')
            assert b'<div id="app">' in response.content
        else:
            assert response.status_code == 503
            assert response.json()['code'] == 'FRONTEND_NOT_BUILT'

    def test_assets_do_not_escape_the_dist_directory(self):
        response = make_client().get('/assets/%2e%2e%2f.env')
        assert response.status_code == 404
        assert b'TYPESAFE_API_KEY' not in response.content

    def test_favicon_is_empty_204(self):
        assert make_client().get('/favicon.ico').status_code == 204

    def test_unknown_page(self):
        response = make_client().get('/nope')
        assert response.status_code == 404
        assert response.json() == {'error': '页面不存在'}

    def test_unknown_endpoint(self):
        response = make_client().post('/nope', json={}, headers=JSON)
        assert response.status_code == 404
        assert response.json() == {'error': '接口不存在'}


class TestGuards:
    def test_foreign_origin_is_rejected(self):
        response = make_client(FakePipeline()).post(
            '/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
            headers={**JSON, 'Origin': 'https://evil.example.com'})
        assert response.status_code == 403
        assert response.json() == {'error': '请求来源不正确'}

    def test_local_origin_is_allowed(self):
        response = make_client(FakePipeline()).post(
            '/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
            headers={**JSON, 'Origin': 'http://127.0.0.1:5500'})
        assert response.status_code == 200

    def test_null_origin_is_allowed_for_file_pages(self):
        response = make_client(FakePipeline()).post(
            '/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
            headers={**JSON, 'Origin': 'null'})
        assert response.status_code == 200

    def test_non_json_content_type_is_rejected(self):
        response = make_client(FakePipeline()).post(
            '/analyze-chat', data='transcript=x', headers={'Content-Type': 'text/plain'})
        assert response.status_code == 403
        assert response.json() == {'error': '请求来源不正确'}

    def test_oversized_body_is_rejected(self):
        payload = {'transcript': 'x' * (MAX_BODY_BYTES + 1), 'relationship': '恋爱'}
        response = make_client(FakePipeline()).post('/analyze-chat', json=payload, headers=JSON)
        assert response.status_code == 400
        assert response.json() == {'error': '输入过长或为空'}

    def test_body_without_content_length_is_rejected(self):
        """分块传输（没有 Content-Length）一律拒掉。

        这条同时钉住一个容易想歪的点：HTTP/1.1 里 Content-Length 是**成帧依据**，
        uvicorn 的 h11 只会按它读 body，所以「谎报一个很小的 Content-Length 就能把
        超大 body 塞进来」并不成立；真正漏的是「根本没有长度」这条路径。
        """
        body = json.dumps({'transcript': TRANSCRIPT, 'relationship': '恋爱'}).encode('utf-8')
        response = make_client(FakePipeline()).post(
            '/analyze-chat', content=iter([body]), headers=JSON)
        assert response.status_code == 400
        assert response.json() == {'error': '输入过长或为空'}

    @pytest.mark.parametrize('path,payload', [
        ('/analyze', {'message': '在吗', 'context': '', 'relationship': '恋爱'}),
        ('/analyze-chat', {'transcript': TRANSCRIPT, 'relationship': '恋爱'}),
        ('/interpret-chat', {'prev': {'analyses': [{}]}}),
        ('/suggest-chat', {'prev': {'analyses': [{}]}}),
        ('/append-chat', {'prev': {'messages': [{}]}, 'transcript': TRANSCRIPT}),
    ])
    def test_payload_is_forwarded(self, path, payload):
        pipeline, classifier = FakePipeline(), FakeClassifier()
        client = make_client(pipeline, classifier)
        response = client.post(path, json=payload, headers=JSON)
        assert response.status_code == 200
        forwarded = (classifier.payloads if path == '/analyze' else pipeline.analyze_payloads)[0]
        for key, value in payload.items():
            assert forwarded[key] == value


class TestErrorMapping:
    @pytest.mark.parametrize('error,status,code', [
        (InvalidRequest('聊天记录不能为空'), 400, None),
        (JevConfigurationError('没 key'), 500, None),
        (JevAPIError(403, 'forbidden'), 502, 'JEV_HTTP_403'),
        (JevAPIError(429, 'too many'), 502, 'JEV_HTTP_429'),
        (JevAPIError(400, 'bad'), 502, 'JEV_HTTP_400'),
        (JevResponseError('结构不对'), 502, 'JEV_INVALID_RESPONSE'),
        (JevConnectionError('连不上'), 503, None),
        (RuntimeError('未预期的异常'), 502, None),
    ])
    def test_pipeline_errors_are_mapped(self, error, status, code):
        client = make_client(FakePipeline(error=error))
        response = client.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                               headers=JSON)
        assert response.status_code == status
        body = response.json()
        assert body['error'], '错误体必须带 error 字段（前端只读它）'
        assert body.get('code') == code
        assert response.headers['X-Jev'] == VERSION, '错误响应也要带版本头'

    def test_missing_key_message_is_actionable(self):
        client = make_client(FakePipeline(error=JevConfigurationError('没 key')))
        response = client.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                               headers=JSON)
        assert 'TYPESAFE_API_KEY' in response.json()['error']

    def test_unexpected_error_does_not_leak_details(self):
        client = make_client(FakePipeline(error=RuntimeError('内部堆栈细节')))
        body = client.post('/analyze-chat', json={'transcript': TRANSCRIPT, 'relationship': '恋爱'},
                           headers=JSON).json()
        assert body == {'error': '这次没有取得 Jev 结果，请稍后重试。'}

    def test_generation_error_is_mapped_on_single_analysis(self):
        client = make_client(FakePipeline(), FakeClassifier(error=GeneralLLMError('生成炸了')))
        response = client.post('/analyze', json={'message': '在吗', 'relationship': '恋爱'}, headers=JSON)
        assert response.status_code == 502
        assert response.json()['error'] == '生成炸了'

    def test_broken_json_body(self):
        response = make_client(FakePipeline()).post('/analyze-chat', content=b'{not json',
                                                    headers=JSON)
        assert response.status_code == 400
        assert response.json() == {'error': '请求格式不正确。'}


class TestAuthHint:
    """401/403 的排查提示：按「密钥前缀 + 接口地址」指出配串了（这是真踩过的坑）。"""

    def test_openrouter_key_on_the_native_gateway(self):
        hint = auth_hint('sk-or-v1-abcdefghij', 'https://api.typesafe.ai')
        assert 'OpenRouter' in hint and '不配套' in hint
        assert 'abcdefghij' not in hint, '提示里不许回显密钥'

    def test_other_key_on_openrouter(self):
        assert 'sk-or-' in auth_hint('ts-abc', 'https://openrouter.ai/api/alpha/decisions')

    def test_matched_pair_falls_back_to_the_checklist(self):
        hint = auth_hint('sk-or-v1-abcdefghij', 'https://openrouter.ai/api/alpha/decisions')
        assert 'typesafe/jev-1.13' in hint

    def test_status_message_is_readable(self):
        assert 'Jev 拒绝了请求' in jev_api_error_message(401)


class TestOpenAPI:
    def test_schema_is_exposed(self):
        schema = make_client().get('/openapi.json').json()
        assert '/analyze-chat' in schema['paths']
        assert schema['info']['version'] == VERSION
