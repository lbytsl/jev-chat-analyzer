"""可视化配置测试：打码、按行合并写 .env、留空不改动、自检把失败变成可读提示。

这些测试必须用临时 .env —— 真实 `.env` 里是用户正在用的密钥，绝不能碰。
"""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.env_file import read_env, update_env
from app.core.exceptions import InvalidRequest
from app.services.settings import SettingsService, mask_key
from tests.test_api import make_client

JSON = {'Content-Type': 'application/json'}

ENV_SAMPLE = """# ===== 密钥 =====
TYPESAFE_API_KEY=sk-or-v1-abcdefghijklmnop
TYPESAFE_BASE_URL=https://openrouter.ai/api/alpha/decisions
TYPESAFE_DEFAULT_MODEL=typesafe/jev-1.13

# ---------- 生成层 ----------
DEEPSEEK_API_KEY=sk-keepme1234
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
"""


def write_env(tmp_path, text=ENV_SAMPLE):
    path = tmp_path / '.env'
    path.write_text(text, encoding='utf-8')
    return path


def fake_settings(**overrides) -> Settings:
    data = {
        'typesafe_api_key': 'sk-or-v1-abcdefghijklmnop',
        'typesafe_base_url': 'https://openrouter.ai/api/alpha/decisions',
        'typesafe_default_model': 'typesafe/jev-1.13',
        'deepseek_api_key': 'sk-keepme1234',
        'deepseek_base_url': 'https://api.deepseek.com',
        'deepseek_model': 'deepseek-flash',
    }
    data.update(overrides)
    return Settings(**data)


class FakeJev:
    def __init__(self, settings):
        self.settings = settings
        self.endpoint = settings.typesafe_base_url

    def decide(self, state, questions):
        return {'model': self.settings.typesafe_default_model,
                'answers': {name: {'type': 'noul', 'noul': 0.9} for name in questions}}


class BrokenJev(FakeJev):
    def decide(self, state, questions):
        from app.core.exceptions import JevAPIError
        raise JevAPIError(401, 'unauthorized')


class FakeLLM:
    def __init__(self, settings):
        self.settings = settings
        self.endpoint = settings.deepseek_base_url + '/chat/completions'

    def complete_json(self, system, user, accept):
        return {'ok': True}


class BrokenLLM(FakeLLM):
    def complete_json(self, system, user, accept):
        from app.core.exceptions import GeneralLLMError
        raise GeneralLLMError('DeepSeek 没有返回可用的 JSON')


class TestEnvFile:
    def test_update_keeps_comments_and_order(self, tmp_path):
        path = write_env(tmp_path)
        changed = update_env(path, {'TYPESAFE_DEFAULT_MODEL': 'typesafe/jev-1.14'})
        text = path.read_text(encoding='utf-8')
        assert changed == ['TYPESAFE_DEFAULT_MODEL']
        assert text.startswith('# ===== 密钥 =====')
        assert '# ---------- 生成层 ----------' in text
        assert 'TYPESAFE_DEFAULT_MODEL=typesafe/jev-1.14' in text
        assert text.index('TYPESAFE_API_KEY') < text.index('TYPESAFE_DEFAULT_MODEL')

    def test_update_appends_missing_key(self, tmp_path):
        path = write_env(tmp_path, 'TYPESAFE_API_KEY=abc\n')
        changed = update_env(path, {'DEEPSEEK_MODEL': 'glm-4'})
        assert changed == ['DEEPSEEK_MODEL']
        assert read_env(path) == {'TYPESAFE_API_KEY': 'abc', 'DEEPSEEK_MODEL': 'glm-4'}

    def test_update_is_idempotent(self, tmp_path):
        path = write_env(tmp_path)
        update_env(path, {'DEEPSEEK_MODEL': 'deepseek-flash'})
        assert update_env(path, {'DEEPSEEK_MODEL': 'deepseek-flash'}) == []
        assert path.read_text(encoding='utf-8').count('DEEPSEEK_MODEL') == 1

    def test_values_with_spaces_are_quoted_and_read_back(self, tmp_path):
        path = write_env(tmp_path, '')
        update_env(path, {'DEEPSEEK_MODEL': 'my model #1'})
        assert read_env(path)['DEEPSEEK_MODEL'] == 'my model #1'


class TestMasking:
    def test_mask_keeps_prefix_and_tail(self):
        assert mask_key('sk-or-v1-abcdefghijklmnop') == 'sk-or…mnop'
        assert mask_key('') == ''
        assert mask_key('abc') == '…abc'


class TestDescribe:
    def test_describe_masks_secrets(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings(),
                                  jev_factory=FakeJev, llm_factory=FakeLLM)
        info = service.describe()
        assert info['classification']['api_key'] == {'configured': True, 'masked': 'sk-or…mnop'}
        assert 'abcdefghijklmnop' not in str(info)
        assert info['generation']['endpoint'] == 'https://api.deepseek.com/chat/completions'
        assert info['classification']['endpoint'] == 'https://openrouter.ai/api/alpha/decisions'

    def test_describe_marks_missing_key(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path),
                                  settings=fake_settings(deepseek_api_key=''),
                                  jev_factory=FakeJev, llm_factory=FakeLLM)
        assert service.describe()['generation']['api_key'] == {'configured': False, 'masked': ''}


class TestOutputSettings:
    def test_describe_includes_suggestions_count(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path),
                                  settings=fake_settings(gen_suggestions_count=4))
        output = service.describe()['output']
        assert output['suggestions_count'] == 4 and output['min'] == 1 and output['max'] == 6

    def test_update_writes_count(self, tmp_path):
        path = write_env(tmp_path)
        service = SettingsService(env_path=path, settings=fake_settings())
        result = service.update({'output': {'suggestions_count': 5}})
        assert result['saved'] == ['GEN_SUGGESTIONS_COUNT']
        assert read_env(path)['GEN_SUGGESTIONS_COUNT'] == '5'

    def test_update_rejects_out_of_range(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings())
        with pytest.raises(InvalidRequest, match='之间'):
            service.update({'output': {'suggestions_count': 9}})

    def test_update_rejects_non_integer(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings())
        with pytest.raises(InvalidRequest, match='整数'):
            service.update({'output': {'suggestions_count': '三条'}})

    def test_pending_count_used_for_probe(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings(),
                                  jev_factory=FakeJev, llm_factory=FakeLLM)
        assert service.test_connections({'output': {'suggestions_count': 6}})['generation']['ok'] is True


class TestUpdate:
    def test_update_writes_only_filled_fields(self, tmp_path):
        path = write_env(tmp_path)
        service = SettingsService(env_path=path, settings=fake_settings())
        result = service.update({'generation': {'model': 'deepseek-chat', 'api_key': ''}})
        assert result['saved'] == ['DEEPSEEK_MODEL']
        env = read_env(path)
        assert env['DEEPSEEK_MODEL'] == 'deepseek-chat'
        assert env['DEEPSEEK_API_KEY'] == 'sk-keepme1234'  # 留空 = 不动

    def test_update_rejects_bad_url(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings())
        with pytest.raises(InvalidRequest, match='http'):
            service.update({'classification': {'base_url': 'openrouter.ai/api'}})

    def test_update_requires_something_to_save(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings())
        with pytest.raises(InvalidRequest, match='没有需要保存的改动'):
            service.update({'classification': {'model': '   '}})

    def test_update_clears_caches(self, tmp_path, monkeypatch):
        cleared = []
        monkeypatch.setattr('app.api.deps.reset_service_caches', lambda: cleared.append(True))
        service = SettingsService(env_path=write_env(tmp_path))
        service.update({'generation': {'model': 'deepseek-chat'}})
        assert cleared == [True]

    def test_update_appends_new_env_path(self, tmp_path):
        service = SettingsService(env_path=tmp_path / 'new' / '.env', settings=fake_settings())
        service.update({'classification': {'model': 'jev-latest'}})
        assert read_env(tmp_path / 'new' / '.env')['TYPESAFE_DEFAULT_MODEL'] == 'jev-latest'


class TestConnections:
    def test_both_layers_ok(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings(),
                                  jev_factory=FakeJev, llm_factory=FakeLLM)
        result = service.test_connections()
        assert result['classification']['ok'] is True
        assert result['generation']['ok'] is True

    def test_pending_values_are_used_for_the_probe(self, tmp_path):
        """界面上还没保存的地址也要能拿来探测（先验证再保存）。"""
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings(),
                                  jev_factory=FakeJev, llm_factory=FakeLLM)
        result = service.test_connections({'classification': {'base_url': 'https://api.typesafe.ai'}})
        assert result['classification']['ok'] is True
        assert result['classification']['endpoint'] == 'https://api.typesafe.ai'
        # 没传的层保持当前配置
        assert result['generation']['endpoint'] == 'https://api.deepseek.com/chat/completions'

    def test_real_clients_own_endpoint_rules(self, tmp_path):
        """端点拼接规则归各客户端管：Jev 只写主机名时补 /v1/systemone，写了完整路径就原样用。"""
        from app.clients.jev import JevClient
        from app.clients.general_llm import GeneralLLMClient
        assert JevClient(fake_settings(typesafe_base_url='https://api.typesafe.ai')).endpoint == \
            'https://api.typesafe.ai/v1/systemone'
        assert JevClient(fake_settings(
            typesafe_base_url='https://openrouter.ai/api/alpha/decisions')).endpoint == \
            'https://openrouter.ai/api/alpha/decisions'
        assert GeneralLLMClient(fake_settings(
            deepseek_base_url='http://127.0.0.1:8000/')).endpoint == 'http://127.0.0.1:8000/chat/completions'

    def test_failures_are_readable(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings(),
                                  jev_factory=BrokenJev, llm_factory=BrokenLLM)
        result = service.test_connections()
        assert result['classification']['ok'] is False and result['classification']['detail']
        assert result['generation']['ok'] is False and 'JSON' in result['generation']['detail']


class TestConfigApi:
    def _client(self, tmp_path, **kwargs):
        service = SettingsService(env_path=write_env(tmp_path),
                                  settings=fake_settings(**kwargs), jev_factory=FakeJev,
                                  llm_factory=FakeLLM)
        return make_client(settings_service=service), service

    def test_get_config(self, tmp_path):
        http, _ = self._client(tmp_path)
        body = http.get('/config').json()
        assert body['classification']['model'] == 'typesafe/jev-1.13'
        assert body['classification']['api_key']['configured'] is True
        assert 'abcdefghijklmnop' not in str(body)

    def test_patch_config(self, tmp_path):
        http, service = self._client(tmp_path)
        response = http.patch('/config', json={'classification': {'model': 'typesafe/jev-1.14'}},
                              headers=JSON)
        assert response.status_code == 200
        assert response.json()['saved'] == ['TYPESAFE_DEFAULT_MODEL']
        assert service.env_snapshot()['TYPESAFE_DEFAULT_MODEL'] == 'typesafe/jev-1.14'

    def test_patch_config_rejects_bad_url(self, tmp_path):
        http, _ = self._client(tmp_path)
        response = http.patch('/config', json={'generation': {'base_url': 'ftp://x'}}, headers=JSON)
        assert response.status_code == 400 and 'http' in response.json()['error']

    def test_patch_requires_json_content_type(self, tmp_path):
        http, _ = self._client(tmp_path)
        response = http.patch('/config', content=b'{}', headers={'Content-Type': 'text/plain'})
        assert response.status_code == 403

    def test_test_endpoint(self, tmp_path):
        http, _ = self._client(tmp_path)
        body = http.post('/config/test', json={}, headers=JSON).json()
        assert body['classification']['ok'] is True and body['generation']['ok'] is True
