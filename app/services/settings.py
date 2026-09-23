"""可视化配置：读出当前 Jev / LLM 配置、按界面改动写回 .env、可选连通性自检。

几个刻意的设计：

- **不回传明文密钥**：界面只拿到「是否已配置 + 打码后的尾巴」，避免密钥出现在
  前端内存、浏览器历史或截图里；保存时留空 = 不改动，所以用户不用重新粘贴。
- **保存即生效**：写完 `.env` 后清掉 Settings 与各服务实例的缓存，下一次请求就用新配置，
  不需要重启（这是最容易踩坑的地方：改了文件但进程里还是旧值）。
- **测试连接可带未保存的值**：传进来的字段作为临时覆盖去探测，用户可以先验证再保存。
- **生成层可以存多套**（v1.0.0）：地址 / 模型 / 密钥能保存若干套并切换启用哪套。整表存在
  `.env` 的 `LLM_PROFILES`（单行 JSON），被启用的那套记在 `LLM_ACTIVE`；两个键都没写过时
  把 `DEEPSEEK_*` 当成唯一一套——老 .env、脚本、夹具照旧可用，行为与以前完全一致。
  保存整表时密钥留空 = 沿用同名那套原来的密钥（界面只显示打码值，这条不能少）。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.clients.general_llm import GeneralLLMClient
from app.clients.jev import JevClient
from app.core.config import (
    ENV_PATH,
    LLM_ACTIVE_KEY,
    LLM_PROFILE_NAME_MAX,
    LLM_PROFILES_KEY,
    MAX_LLM_PROFILES,
    SUGGESTIONS_MAX,
    SUGGESTIONS_MIN,
    LLMProfile,
    Settings,
    clamp_suggestions_count,
    dump_llm_profiles,
    pick_llm_profile,
)
from app.core.env_file import read_env, update_env
from app.core.exceptions import GeneralLLMError, InvalidRequest

# 界面字段 → .env 键 / Settings 字段
CLASSIFICATION_FIELDS = {
    'base_url': ('TYPESAFE_BASE_URL', 'typesafe_base_url'),
    'model': ('TYPESAFE_DEFAULT_MODEL', 'typesafe_default_model'),
    'api_key': ('TYPESAFE_API_KEY', 'typesafe_api_key'),
}
# 生成层的单套字段 → Settings 字段。多套模式下这几个名字不再直接写进 .env，而是落到
# 「当前启用」那套上（整张表由 LLM_PROFILES 承载）；没写过多套配置时，它们等价于表里唯一
# 的那套（DEEPSEEK_* 当兜底）。所以这里只留界面字段名 → 属性名的映射。
GENERATION_FIELDS = {
    'base_url': 'deepseek_base_url',
    'model': 'deepseek_model',
    'api_key': 'deepseek_api_key',
}

MASK_TAIL = 4


def mask_key(value: str) -> str:
    """密钥只留前缀与尾巴：sk-or-…1a2b。"""
    if not value:
        return ''
    if len(value) <= MASK_TAIL + 4:
        return '…' + value[-MASK_TAIL:]
    head = value[:5]
    return head + '…' + value[-MASK_TAIL:]


class SettingsService:
    def __init__(self, env_path: Path | str = ENV_PATH, settings=None,
                 jev_factory=JevClient, llm_factory=GeneralLLMClient):
        self.env_path = Path(env_path)
        # settings / 工厂都可在测试里替换，避免真连上游、也避免动到真实的 .env
        self._settings = settings
        self._jev_factory = jev_factory
        self._llm_factory = llm_factory

    # ---------- 读 ----------
    def describe(self) -> dict:
        settings = self.current()
        return {
            'env_path': str(self.env_path),
            'classification': self._classification_layer(settings),
            'generation': self._generation_layer(settings),
            'output': {
                'suggestions_count': clamp_suggestions_count(settings.gen_suggestions_count),
                'min': SUGGESTIONS_MIN,
                'max': SUGGESTIONS_MAX,
            },
        }

    def current(self) -> Settings:
        if self._settings is not None:
            return self._settings
        from app.core.config import get_settings
        return get_settings()

    @staticmethod
    def _masked(api_key: str) -> dict:
        return {'configured': bool(api_key), 'masked': mask_key(api_key or '')}

    def _classification_layer(self, settings: Settings) -> dict:
        base_url = (settings.typesafe_base_url or '').rstrip('/')
        return {
            'base_url': base_url,
            'model': settings.typesafe_default_model or '',
            'api_key': self._masked(settings.typesafe_api_key),
            # 端点按客户端自己的规则算：Jev 可能是完整路径，也可能是主机名 + /v1/systemone。
            'endpoint': JevClient(settings).endpoint,
        }

    def _generation_layer(self, settings: Settings) -> dict:
        """生成层：既回「当前启用那套」的扁平字段（老前端照旧可用），也回整张配置表。"""
        profiles = settings.effective_llm_profiles
        active = pick_llm_profile(profiles, settings.llm_active)
        base_url = (settings.deepseek_base_url or '').rstrip('/')
        return {
            'base_url': base_url,
            'model': settings.deepseek_model or '',
            # 端点按客户端的规则算（写前缀会补 /chat/completions，写完整端点则原样用），
            # 这里不能自己拼，否则界面上显示的和真正请求的会不一致。
            'endpoint': GeneralLLMClient(settings).endpoint if base_url else '',
            'api_key': self._masked(settings.deepseek_api_key),
            'active': active.name if active else '',
            'profiles': [
                {'name': profile.name,
                 'base_url': profile.base_url,
                 'model': profile.model,
                 'api_key': self._masked(profile.api_key),
                 'active': bool(active and profile.name == active.name)}
                for profile in profiles
            ],
            'profiles_error': settings.llm_profiles_error,
            'stored': bool(settings.llm_profile_list),
            # 上限由服务端给界面，省得两边各写一份常量
            'max_profiles': MAX_LLM_PROFILES,
            'name_max': LLM_PROFILE_NAME_MAX,
        }

    # ---------- 写 ----------
    def update(self, payload: dict) -> dict:
        updates: dict[str, str] = {}
        updates.update(self._classification_updates(self._section(payload, 'classification')))
        updates.update(self._generation_updates(self._section(payload, 'generation')))
        output = self._section(payload, 'output')
        if output.get('suggestions_count') is not None:
            updates['GEN_SUGGESTIONS_COUNT'] = str(
                self._assert_count(output['suggestions_count']))
        if not updates:
            raise InvalidRequest('没有需要保存的改动。')
        update_env(self.env_path, updates)
        self.reload()
        result = self.describe()
        result['saved'] = sorted(updates)
        return result

    @staticmethod
    def _section(payload: dict, group: str) -> dict:
        """取出某个 Tab 的改动；没传 = 这层不改，传了但不是对象 = 格式错。"""
        section = payload.get(group)
        if section is None:
            return {}
        if not isinstance(section, dict):
            raise InvalidRequest('配置格式不正确。')
        return section

    @staticmethod
    def _text_field(section: dict, name: str) -> str | None:
        """取一个文本字段；没传 / 留空都返回 None = 不改动。

        密钥必须如此：界面只显示打码值，拿它回写就等于把真密钥换成一串省略号。
        """
        if section.get(name) is None:
            return None
        return str(section[name]).strip() or None

    def _classification_updates(self, section: dict) -> dict[str, str]:
        updates: dict[str, str] = {}
        for name, (env_key, _) in CLASSIFICATION_FIELDS.items():
            value = self._text_field(section, name)
            if value is None:
                continue
            if name == 'base_url':
                self._assert_url(value)
            updates[env_key] = value
        return updates

    def _generation_updates(self, section: dict) -> dict[str, str]:
        """生成层：整表替换多套配置（profiles），或者只改「当前启用」那套（老路径）。"""
        settings = self.current()
        profiles = list(settings.effective_llm_profiles)
        active = pick_llm_profile(profiles, settings.llm_active)
        updates: dict[str, str] = {}

        if section.get('profiles') is not None:
            profiles = self._normalize_profiles(
                section['profiles'], profiles)
            updates[LLM_PROFILES_KEY] = dump_llm_profiles(profiles)
        else:
            edits = self._generation_edits(section)
            if edits:
                profiles = [replace(profile, **edits)
                            if active and profile.name == active.name else profile
                            for profile in profiles]
                updates[LLM_PROFILES_KEY] = dump_llm_profiles(profiles)

        # 切换启用：名字以「最终那张表」为准，指到不存在的配置就直接报错（否则会被静默忽略）
        wanted = str(section.get('active') or '').strip()
        if wanted and wanted not in {profile.name for profile in profiles}:
            raise InvalidRequest('要启用的配置「{}」不存在。'.format(wanted))
        active = pick_llm_profile(profiles, wanted or (active.name if active else ''))
        # 只在真的存过一次配置表时才写启用名，避免 .env 里出现一个指向兜底配置的孤立键
        if active is not None and (updates or settings.llm_profile_list):
            updates[LLM_ACTIVE_KEY] = active.name
        return updates

    def _generation_edits(self, section: dict) -> dict[str, str]:
        """生成层单套字段的改动；键名与 LLMProfile 的字段同名（base_url / model / api_key）。"""
        edits: dict[str, str] = {}
        for name in GENERATION_FIELDS:
            value = self._text_field(section, name)
            if value is None:
                continue
            if name == 'base_url':
                self._assert_url(value)
            edits[name] = value
        return edits

    def _normalize_profiles(self, raw_list, current: list[LLMProfile]) -> list[LLMProfile]:
        """校验并整形界面上提交的整张表（业务校验就在这里，模型层不做）。

        密钥对齐用界面回传的 `source`（这套配置在服务端原来的名字），而不是当前名字：
        否则「改个名字」就会匹配不到旧项，密钥被静默清成空。
        """
        if not isinstance(raw_list, list):
            raise InvalidRequest('配置列表格式不正确。')
        if not raw_list:
            raise InvalidRequest('至少要留一套模型配置。')
        if len(raw_list) > MAX_LLM_PROFILES:
            raise InvalidRequest('最多保存 {} 套模型配置。'.format(MAX_LLM_PROFILES))
        old = {profile.name: profile for profile in current}
        profiles: list[LLMProfile] = []
        seen: set[str] = set()
        for item in raw_list:
            if not isinstance(item, dict):
                raise InvalidRequest('配置列表格式不正确。')
            name = str(item.get('name') or '').strip()
            if not name:
                raise InvalidRequest('每套配置都要填一个名字。')
            if len(name) > LLM_PROFILE_NAME_MAX:
                raise InvalidRequest('配置名字最多 {} 个字。'.format(LLM_PROFILE_NAME_MAX))
            if name in seen:
                raise InvalidRequest('配置名字「{}」重复了。'.format(name))
            seen.add(name)
            base_url = str(item.get('base_url') or '').strip()
            if not base_url:
                raise InvalidRequest('配置「{}」还没填接口地址。'.format(name))
            self._assert_url(base_url)
            model = str(item.get('model') or '').strip()
            if not model:
                raise InvalidRequest('配置「{}」还没填模型名。'.format(name))
            api_key = str(item.get('api_key') or '').strip()
            source = str(item.get('source') or '').strip()
            previous = old.get(source) if source else None
            if previous is None:
                previous = old.get(name)        # 没带 source 的调用方（脚本 / 测试）按名字对齐
            if previous is not None and (not api_key or api_key == mask_key(previous.api_key)):
                api_key = previous.api_key      # 留空（或误把打码值传回来）= 沿用原来那把
            profiles.append(LLMProfile(name=name, base_url=base_url.rstrip('/'),
                                       model=model, api_key=api_key))
        return profiles

    @staticmethod
    def _assert_count(value) -> int:
        try:
            count = int(str(value).strip())
        except (TypeError, ValueError):
            raise InvalidRequest('推荐回复条数要填整数。') from None
        if not SUGGESTIONS_MIN <= count <= SUGGESTIONS_MAX:
            raise InvalidRequest('推荐回复条数请在 {}–{} 之间。'.format(SUGGESTIONS_MIN, SUGGESTIONS_MAX))
        return count

    @staticmethod
    def _assert_url(value: str) -> None:
        if not value.startswith(('http://', 'https://')):
            raise InvalidRequest('接口地址要以 http:// 或 https:// 开头。')

    def reload(self) -> None:
        """清掉配置与服务的缓存，让下一次请求用新值（不用重启进程）。"""
        if self._settings is not None:
            return
        from app.api.deps import reset_service_caches
        from app.core.config import get_settings
        get_settings.cache_clear()
        reset_service_caches()

    # ---------- 连通性自检 ----------
    def test_connections(self, payload: dict | None = None) -> dict:
        settings = self._with_overrides(payload or {})
        return {
            'classification': self._test_classification(settings),
            'generation': self._test_generation(settings),
        }

    def _with_overrides(self, payload: dict) -> Settings:
        """把界面上的（可能还没保存的）值套到当前配置上，只用于这次探测。"""
        data = self.current().model_dump()
        for name, (_, attr) in CLASSIFICATION_FIELDS.items():
            value = self._text_field(self._section(payload, 'classification'), name)
            if value is None:
                continue
            if name == 'base_url':
                self._assert_url(value)
            data[attr] = value
        edits = self._generation_edits(self._section(payload, 'generation'))
        if edits:
            for name, value in edits.items():
                data[GENERATION_FIELDS[name]] = value
            # 探测要用界面上的值：清掉多套配置，否则初始化时又被「当前启用」那套盖回去
            data['llm_profiles'] = ''
            data['llm_active'] = ''
        count = self._section(payload, 'output').get('suggestions_count')
        if count is not None:
            data['gen_suggestions_count'] = self._assert_count(count)
        return Settings(**data)

    def _test_classification(self, settings: Settings) -> dict:
        client = self._jev_factory(settings)
        questions = {'ping': {
            'type': 'noul',
            'instructions': '这条消息是否在表达时间紧迫？',
            'criteria': {'true': '明确提到时间或急迫', 'false': '没有表达紧迫'},
        }}
        state = {'message': '在吗', 'context': '', 'relationship': '恋爱', 'speaker': 'other'}
        try:
            response = client.decide(state, questions)
        except Exception as exc:  # noqa: BLE001 —— 自检要把任何失败都变成一句可读的话
            return {'ok': False, 'detail': _readable(exc), 'endpoint': client.endpoint}
        answers = response.get('answers') or {}
        if 'ping' not in answers:
            return {'ok': False, 'detail': '上游有响应，但没有返回预期结果，请确认地址与模型是否配对。',
                    'endpoint': client.endpoint}
        return {'ok': True, 'detail': '已连通：' + str(response.get('model') or settings.typesafe_default_model),
                'endpoint': client.endpoint}

    def _test_generation(self, settings: Settings) -> dict:
        client = self._llm_factory(settings)
        try:
            parsed = client.complete_json(
                '只输出 JSON，形如 {"ok": true}', '连通性测试：请按要求的 JSON 回复。',
                accept=lambda payload: isinstance(payload, dict))
        except GeneralLLMError as exc:
            return {'ok': False, 'detail': str(exc), 'endpoint': client.endpoint}
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'detail': _readable(exc), 'endpoint': client.endpoint}
        return {'ok': True,
                'detail': '已连通（{}），返回：{}'.format(settings.deepseek_model, str(parsed)[:60]),
                'endpoint': client.endpoint}

    # 供测试与界面展示：.env 里当前写了什么（不含默认值兜底）
    def env_snapshot(self) -> dict:
        return read_env(self.env_path)


def _readable(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    return text
