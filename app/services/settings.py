"""可视化配置：读出当前 Jev / LLM 配置、按界面改动写回 .env、可选连通性自检。

几个刻意的设计：

- **不回传明文密钥**：界面只拿到「是否已配置 + 打码后的尾巴」，避免密钥出现在
  前端内存、浏览器历史或截图里；保存时留空 = 不改动，所以用户不用重新粘贴。
- **保存即生效**：写完 `.env` 后清掉 Settings 与各服务实例的缓存，下一次请求就用新配置，
  不需要重启（这是最容易踩坑的地方：改了文件但进程里还是旧值）。
- **测试连接可带未保存的值**：传进来的字段作为临时覆盖去探测，用户可以先验证再保存。
"""
from __future__ import annotations

from pathlib import Path

from app.clients.general_llm import GeneralLLMClient
from app.clients.jev import JevClient
from app.core.config import (
    ENV_PATH,
    SUGGESTIONS_MAX,
    SUGGESTIONS_MIN,
    Settings,
    clamp_suggestions_count,
)
from app.core.env_file import read_env, update_env
from app.core.exceptions import GeneralLLMError, InvalidRequest

# 界面字段 → .env 键 / Settings 字段
CLASSIFICATION_FIELDS = {
    'base_url': ('TYPESAFE_BASE_URL', 'typesafe_base_url'),
    'model': ('TYPESAFE_DEFAULT_MODEL', 'typesafe_default_model'),
    'api_key': ('TYPESAFE_API_KEY', 'typesafe_api_key'),
}
GENERATION_FIELDS = {
    'base_url': ('DEEPSEEK_BASE_URL', 'deepseek_base_url'),
    'model': ('DEEPSEEK_MODEL', 'deepseek_model'),
    'api_key': ('DEEPSEEK_API_KEY', 'deepseek_api_key'),
}
# 输出设置：不是接口凭据，但同样写在 .env 里，配置面板第三个 Tab 用它。
OUTPUT_FIELDS = {
    'suggestions_count': ('GEN_SUGGESTIONS_COUNT', 'gen_suggestions_count'),
}
ALL_FIELDS = {'classification': CLASSIFICATION_FIELDS, 'generation': GENERATION_FIELDS,
              'output': OUTPUT_FIELDS}

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
            'classification': self._layer(settings, 'typesafe', 'classification'),
            'generation': self._layer(settings, 'deepseek', 'generation'),
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

    def _layer(self, settings: Settings, prefix: str, group: str) -> dict:
        base_url = (getattr(settings, prefix + '_base_url') or '').rstrip('/')
        model = getattr(settings, prefix + '_default_model' if prefix == 'typesafe'
                        else 'deepseek_model') or ''
        api_key = getattr(settings, prefix + '_api_key') or ''
        # 端点按各客户端自己的规则算：Jev 可能是完整路径，也可能是主机名 + /v1/systemone。
        endpoint = (JevClient(settings).endpoint if group == 'classification'
                    else base_url + '/chat/completions' if base_url else '')
        return {
            'base_url': base_url,
            'model': model,
            'api_key': {'configured': bool(api_key), 'masked': mask_key(api_key)},
            'endpoint': endpoint,
        }

    # ---------- 写 ----------
    def update(self, payload: dict) -> dict:
        updates: dict[str, str] = {}
        for group, fields in ALL_FIELDS.items():
            section = payload.get(group) or {}
            if not isinstance(section, dict):
                raise InvalidRequest('配置格式不正确。')
            for name, (env_key, _) in fields.items():
                if name not in section:
                    continue
                value = section.get(name)
                if value is None:
                    continue
                if group == 'output':
                    updates[env_key] = str(self._assert_count(value))
                    continue
                text = str(value).strip()
                # 留空 = 不改动（密钥尤其重要：界面只显示打码值，不能拿它覆盖真密钥）
                if not text:
                    continue
                if name == 'base_url':
                    self._assert_url(text)
                updates[env_key] = text
        if not updates:
            raise InvalidRequest('没有需要保存的改动。')
        update_env(self.env_path, updates)
        self.reload()
        result = self.describe()
        result['saved'] = sorted(updates)
        return result

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
        for group, fields in ALL_FIELDS.items():
            section = payload.get(group) or {}
            if not isinstance(section, dict):
                continue
            for name, (_, attr) in fields.items():
                value = section.get(name)
                if value is None:
                    continue
                if group == 'output':
                    data[attr] = self._assert_count(value)
                    continue
                text = str(value).strip()
                if not text:
                    continue
                if name == 'base_url':
                    self._assert_url(text)
                data[attr] = text
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
        return {'ok': True, 'detail': '已连通，返回：' + str(parsed)[:60], 'endpoint': client.endpoint}

    # 供测试与界面展示：.env 里当前写了什么（不含默认值兜底）
    def env_snapshot(self) -> dict:
        return read_env(self.env_path)


def _readable(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    return text
