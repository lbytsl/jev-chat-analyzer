"""可视化配置测试：打码、按行合并写 .env、留空不改动、自检把失败变成可读提示。

这些测试必须用临时 .env —— 真实 `.env` 里是用户正在用的密钥，绝不能碰。
"""
from __future__ import annotations

import json

import pytest

from app.core.config import (
    LLM_ACTIVE_KEY,
    LLM_PROFILES_KEY,
    MAX_LLM_PROFILES,
    LLMProfile,
    Settings,
    dump_llm_profiles,
    load_llm_profiles,
)
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
        # 显式清空多套配置：Settings 还会去读真实 .env，本机若写过 LLM_PROFILES 会把这里的值盖掉
        'llm_profiles': '',
        'llm_active': '',
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

    def test_json_value_survives_the_round_trip(self, tmp_path):
        """LLM_PROFILES 是单行 JSON：写侧转义引号，读侧必须原样还原（否则配置全废）。"""
        path = write_env(tmp_path, '')
        profiles = [{'name': '本地 vLLM #1', 'base_url': 'http://127.0.0.1:8000/v1',
                     'model': 'qwen2.5', 'api_key': 'sk-a"b\\c'}]
        update_env(path, {'LLM_PROFILES': json.dumps(profiles, ensure_ascii=False)})
        assert json.loads(read_env(path)['LLM_PROFILES']) == profiles
        # pydantic-settings（界面保存后真正生效的那条路）也要能读回来
        assert json.loads(Settings(_env_file=path).llm_profiles) == profiles

    def test_single_quoted_values_are_literal(self, tmp_path):
        """单引号是「原样」，与 dotenv 一致：里面的反斜杠不做转义还原。"""
        path = write_env(tmp_path, "KEY='a\\nb'\n")
        assert read_env(path)['KEY'] == 'a\\nb'


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
        """只给单套字段（老路径）：改的是「当前启用」那套，密钥留空 = 沿用原来那把。"""
        path = write_env(tmp_path)
        service = SettingsService(env_path=path, settings=fake_settings())
        result = service.update({'generation': {'model': 'deepseek-chat', 'api_key': ''}})
        assert result['saved'] == ['LLM_ACTIVE', 'LLM_PROFILES']
        env = read_env(path)
        assert env['DEEPSEEK_API_KEY'] == 'sk-keepme1234'  # 老键不动
        profiles = json.loads(env['LLM_PROFILES'])
        assert profiles == [{'name': '默认', 'base_url': 'https://api.deepseek.com',
                             'model': 'deepseek-chat', 'api_key': 'sk-keepme1234'}]
        assert env['LLM_ACTIVE'] == '默认'

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
        from app.clients.general_llm import GeneralLLMClient
        from app.clients.jev import JevClient
        assert JevClient(fake_settings(typesafe_base_url='https://api.typesafe.ai')).endpoint == \
            'https://api.typesafe.ai/v1/systemone'
        assert JevClient(fake_settings(
            typesafe_base_url='https://openrouter.ai/api/alpha/decisions')).endpoint == \
            'https://openrouter.ai/api/alpha/decisions'
        assert GeneralLLMClient(fake_settings(
            deepseek_base_url='http://127.0.0.1:8000/')).endpoint == 'http://127.0.0.1:8000/chat/completions'
        # 用户把完整端点粘进「接口地址」也要能用：原样请求，不再补一段路径
        assert GeneralLLMClient(fake_settings(
            deepseek_base_url='https://api.stepfun.com/step_plan/v1/chat/completions')).endpoint == \
            'https://api.stepfun.com/step_plan/v1/chat/completions'

    def test_describe_shows_the_url_that_will_really_be_requested(self, tmp_path):
        """界面上的端点提示必须与客户端实际请求的一致（尤其用户填了完整端点时）。"""
        from app.clients.general_llm import GeneralLLMClient
        settings = fake_settings(
            deepseek_base_url='https://api.stepfun.com/step_plan/v1/chat/completions')
        service = SettingsService(env_path=write_env(tmp_path), settings=settings)
        assert service.describe()['generation']['endpoint'] == \
            'https://api.stepfun.com/step_plan/v1/chat/completions'
        assert GeneralLLMClient(settings).endpoint == \
            service.describe()['generation']['endpoint']

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


# ===== 生成层多套配置（v009） =====
PROFILES = [
    {'name': 'DeepSeek 官方', 'base_url': 'https://api.deepseek.com', 'model': 'deepseek-chat',
     'api_key': 'sk-aaa1111'},
    {'name': '本地 vLLM', 'base_url': 'http://127.0.0.1:8000/v1', 'model': 'qwen2.5',
     'api_key': 'sk-bbb2222'},
]


def profile_settings(active='本地 vLLM', **overrides) -> Settings:
    """模拟「已经保存过多套配置」的 .env：LLM_PROFILES + LLM_ACTIVE。"""
    data = {'llm_profiles': dump_llm_profiles([LLMProfile(**item) for item in PROFILES]),
            'llm_active': active}
    data.update(overrides)
    return fake_settings(**data)


def write_env_with_profiles(tmp_path, active='本地 vLLM'):
    path = write_env(tmp_path)
    update_env(path, {LLM_PROFILES_KEY: dump_llm_profiles([LLMProfile(**item) for item in PROFILES]),
                      LLM_ACTIVE_KEY: active})
    return path


def config_client(tmp_path, **kwargs):
    service = SettingsService(env_path=write_env_with_profiles(tmp_path),
                              settings=profile_settings(**kwargs), jev_factory=FakeJev,
                              llm_factory=FakeLLM)
    return make_client(settings_service=service), service


class TestLLMProfiles:
    def test_falls_back_to_deepseek_when_never_saved(self, tmp_path):
        """没写过 LLM_PROFILES 时，界面看到的是 DEEPSEEK_* 那一套（老 .env 照旧能用）。"""
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings())
        layer = service.describe()['generation']
        assert layer['stored'] is False
        assert [item['name'] for item in layer['profiles']] == ['默认']
        assert layer['active'] == '默认'
        assert layer['profiles'][0]['model'] == 'deepseek-flash'
        assert layer['profiles'][0]['api_key'] == {'configured': True, 'masked': 'sk-ke…1234'}

    def test_active_profile_drives_the_generation_client(self):
        """启用哪套，生成链路读到的 deepseek_* 就是哪套（客户端不需要知道多套的存在）。"""
        from app.clients.general_llm import GeneralLLMClient
        settings = profile_settings(active='本地 vLLM')
        assert settings.deepseek_model == 'qwen2.5'
        assert settings.deepseek_base_url == 'http://127.0.0.1:8000/v1'
        assert settings.deepseek_api_key == 'sk-bbb2222'
        assert GeneralLLMClient(settings).endpoint == 'http://127.0.0.1:8000/v1/chat/completions'

    def test_unknown_active_name_falls_back_to_the_first(self):
        """启用的名字对不上（被改名 / 手改错）时退回第一套，不至于没有可用配置。"""
        settings = profile_settings(active='不存在的名字')
        assert settings.deepseek_model == 'deepseek-chat'

    def test_describe_lists_every_profile_with_masked_keys(self, tmp_path):
        service = SettingsService(env_path=write_env_with_profiles(tmp_path),
                                  settings=profile_settings())
        layer = service.describe()['generation']
        assert [item['name'] for item in layer['profiles']] == ['DeepSeek 官方', '本地 vLLM']
        assert [item['active'] for item in layer['profiles']] == [False, True]
        assert layer['active'] == '本地 vLLM' and layer['model'] == 'qwen2.5'
        assert layer['stored'] is True and layer['profiles_error'] == ''
        assert 'sk-bbb2222' not in str(layer)          # 明文密钥不出现在响应里

    def test_saving_back_the_masked_key_keeps_the_real_one(self, tmp_path):
        """界面只有打码值：整表保存时留空 / 误传打码值都必须沿用原来那把密钥。"""
        path = write_env_with_profiles(tmp_path)
        service = SettingsService(env_path=path, settings=profile_settings())
        service.update({'generation': {
            'profiles': [
                {'name': 'DeepSeek 官方', 'base_url': 'https://api.deepseek.com/v1',
                 'model': 'deepseek-chat', 'api_key': ''},
                {'name': '本地 vLLM', 'base_url': 'http://127.0.0.1:8000/v1',
                 'model': 'qwen2.5', 'api_key': 'sk-bb…2222'},
            ],
            'active': 'DeepSeek 官方',
        }})
        profiles = json.loads(read_env(path)['LLM_PROFILES'])
        assert profiles[0]['api_key'] == 'sk-aaa1111'          # 留空 = 沿用
        assert profiles[0]['base_url'] == 'https://api.deepseek.com/v1'
        assert profiles[1]['api_key'] == 'sk-bbb2222'          # 打码值不当真密钥存
        assert read_env(path)['LLM_ACTIVE'] == 'DeepSeek 官方'

    def test_renaming_a_profile_keeps_its_key(self, tmp_path):
        """改名不该把密钥弄丢：服务端按界面回传的 source（原名字）把原来那把接上。"""
        path = write_env_with_profiles(tmp_path)
        service = SettingsService(env_path=path, settings=profile_settings())
        service.update({'generation': {'profiles': [
            {'name': 'DeepSeek 新名字', 'source': 'DeepSeek 官方',
             'base_url': 'https://api.deepseek.com', 'model': 'deepseek-chat', 'api_key': ''},
            {'name': '本地 vLLM', 'source': '本地 vLLM',
             'base_url': 'http://127.0.0.1:8000/v1', 'model': 'qwen2.5', 'api_key': ''},
        ], 'active': 'DeepSeek 新名字'}})
        profiles = json.loads(read_env(path)['LLM_PROFILES'])
        assert [item['name'] for item in profiles] == ['DeepSeek 新名字', '本地 vLLM']
        assert profiles[0]['api_key'] == 'sk-aaa1111'
        assert profiles[1]['api_key'] == 'sk-bbb2222'

    def test_new_profile_does_not_inherit_a_key(self, tmp_path):
        """新增（没带 source）的那套不该继承别人的密钥，得等用户自己填。"""
        path = write_env_with_profiles(tmp_path)
        service = SettingsService(env_path=path, settings=profile_settings())
        service.update({'generation': {'profiles': [
            {'name': '通义千问', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
             'model': 'qwen-plus', 'api_key': ''},
        ]}})
        assert json.loads(read_env(path)['LLM_PROFILES'])[0]['api_key'] == ''

    def test_switch_active_only_writes_the_active_key(self, tmp_path):
        path = write_env_with_profiles(tmp_path, active='DeepSeek 官方')
        before = read_env(path)['LLM_PROFILES']
        service = SettingsService(env_path=path, settings=profile_settings(active='DeepSeek 官方'))
        result = service.update({'generation': {'active': '本地 vLLM'}})
        assert result['saved'] == ['LLM_ACTIVE']
        assert read_env(path)['LLM_ACTIVE'] == '本地 vLLM'
        assert read_env(path)['LLM_PROFILES'] == before        # 配置表一个字节都没动

    def test_saved_profiles_are_usable_after_reload(self, tmp_path):
        """保存 → 清缓存 → 重新读 .env：新启用那套必须立刻生效（不用重启服务）。"""
        path = write_env(tmp_path)
        service = SettingsService(env_path=path, settings=fake_settings())
        service.update({'generation': {'profiles': PROFILES, 'active': '本地 vLLM'}})
        assert Settings(_env_file=path).deepseek_model == 'qwen2.5'

    @pytest.mark.parametrize('profiles, message', [
        ([], '至少要留一套'),
        ([{'name': '  ', 'base_url': 'https://x.com', 'model': 'm'}], '都要填一个名字'),
        ([{'name': 'a', 'base_url': '', 'model': 'm'}], '还没填接口地址'),
        ([{'name': 'a', 'base_url': 'ftp://x', 'model': 'm'}], 'http'),
        ([{'name': 'a', 'base_url': 'https://x.com', 'model': ' '}], '还没填模型名'),
        ([{'name': 'a', 'base_url': 'https://x.com', 'model': 'm'},
          {'name': 'a', 'base_url': 'https://y.com', 'model': 'm'}], '重复'),
    ])
    def test_rejects_bad_profiles(self, tmp_path, profiles, message):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings())
        with pytest.raises(InvalidRequest, match=message):
            service.update({'generation': {'profiles': profiles}})

    def test_rejects_too_many_profiles(self, tmp_path):
        many = [{'name': 'p{}'.format(index), 'base_url': 'https://x.com', 'model': 'm'}
                for index in range(MAX_LLM_PROFILES + 1)]
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings())
        with pytest.raises(InvalidRequest, match='最多保存'):
            service.update({'generation': {'profiles': many}})

    def test_rejects_unknown_active_name(self, tmp_path):
        service = SettingsService(env_path=write_env(tmp_path), settings=fake_settings())
        with pytest.raises(InvalidRequest, match='不存在'):
            service.update({'generation': {'active': '没这套'}})

    def test_probe_uses_the_pending_values(self, tmp_path):
        """「测试连接」测的是界面上那套（可能还没保存），不能被当前启用的配置盖住。"""
        service = SettingsService(env_path=write_env_with_profiles(tmp_path),
                                  settings=profile_settings(), jev_factory=FakeJev,
                                  llm_factory=FakeLLM)
        result = service.test_connections({'generation': {
            'base_url': 'https://api.moonshot.cn/v1', 'model': 'moonshot-v1-8k'}})
        assert result['generation']['ok'] is True
        assert result['generation']['endpoint'] == 'https://api.moonshot.cn/v1/chat/completions'


class TestLLMProfilesParsing:
    def test_broken_json_falls_back_instead_of_crashing(self, tmp_path):
        settings = Settings(_env_file=tmp_path / 'missing.env', llm_profiles='{不是 JSON')
        assert settings.deepseek_model == 'deepseek-flash'
        assert [item.name for item in settings.effective_llm_profiles] == ['默认']
        assert 'JSON' in settings.llm_profiles_error

    @pytest.mark.parametrize('raw, message', [
        ('{"a": 1}', 'JSON 数组'),
        ('[1, 2]', '没有可用的配置项'),
        ('no json at all', 'JSON'),
    ])
    def test_unusable_values_are_ignored(self, raw, message):
        profiles, error = load_llm_profiles(raw)
        assert profiles == [] and message in error

    def test_bad_items_are_skipped(self):
        """没名字 / 重名的项跳过，别的项照常可用（手改坏了不至于整张表作废）。"""
        raw = json.dumps([
            {'name': '', 'base_url': 'https://x.com', 'model': 'm'},
            {'name': '好的', 'base_url': 'https://x.com', 'model': 'm'},
            {'name': '好的', 'base_url': 'https://y.com', 'model': 'n'},
            '不是对象',
        ], ensure_ascii=False)
        profiles, error = load_llm_profiles(raw)
        assert error == '' and [item.name for item in profiles] == ['好的']
        assert profiles[0].base_url == 'https://x.com'

    def test_empty_value_means_never_saved(self):
        assert load_llm_profiles('') == ([], '')
        assert load_llm_profiles(None) == ([], '')


class TestLLMProfilesApi:
    def test_patch_saves_profiles_and_switches(self, tmp_path):
        http, service = config_client(tmp_path)
        response = http.patch('/config', json={'generation': {
            'profiles': PROFILES, 'active': '本地 vLLM'}}, headers=JSON)
        assert response.status_code == 200
        body = response.json()
        assert body['saved'] == ['LLM_ACTIVE', 'LLM_PROFILES']
        assert [item['name'] for item in body['generation']['profiles']] == \
            ['DeepSeek 官方', '本地 vLLM']
        assert 'sk-bbb2222' not in str(body)                    # 响应里只有打码值
        assert service.env_snapshot()['LLM_ACTIVE'] == '本地 vLLM'

    def test_get_config_exposes_profiles(self, tmp_path):
        http, _ = config_client(tmp_path)
        layer = http.get('/config').json()['generation']
        assert layer['active'] == '本地 vLLM'
        assert [item['active'] for item in layer['profiles']] == [False, True]
        assert layer['profiles'][0]['api_key']['configured'] is True
        assert layer['profiles_error'] == ''

    def test_patch_rejects_bad_profiles(self, tmp_path):
        http, _ = config_client(tmp_path)
        response = http.patch('/config', json={'generation': {'profiles': [
            {'name': 'a', 'base_url': 'not-a-url', 'model': 'm'}]}}, headers=JSON)
        assert response.status_code == 400 and 'http' in response.json()['error']
