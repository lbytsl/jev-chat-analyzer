"""可视化配置的请求 / 响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _LooseModel(BaseModel):
    model_config = ConfigDict(extra='allow')


class MaskedKey(_LooseModel):
    configured: bool = Field(description='是否已经配了密钥')
    masked: str = Field(default='', description='打码后的样子（sk-o…a1b2），不回传明文')


class ModelLayer(_LooseModel):
    base_url: str = ''
    model: str = ''
    endpoint: str = Field(default='', description='实际会请求的地址（按各客户端的规则拼好）')
    api_key: MaskedKey


class LLMProfileView(_LooseModel):
    """生成层里的一套配置（密钥同样只回打码值）。"""

    name: str = Field(description='配置名字，同一份 .env 内唯一')
    base_url: str = ''
    model: str = ''
    api_key: MaskedKey
    active: bool = Field(default=False, description='是不是当前启用的那套')


class GenerationLayer(ModelLayer):
    active: str = Field(default='', description='当前启用的配置名')
    profiles: list[LLMProfileView] = Field(default_factory=list,
                                            description='保存过的多套配置（没写过时是 DEEPSEEK_* 那套）')
    profiles_error: str = Field(default='', description='LLM_PROFILES 手改坏了时的一句提示')
    stored: bool = Field(default=False, description='是否已写进 .env（false = 用的是 DEEPSEEK_* 兜底）')
    max_profiles: int = Field(default=20, description='最多能存几套（界面据此禁用「新增」）')
    name_max: int = Field(default=24, description='配置名字的长度上限')


class OutputSettings(_LooseModel):
    suggestions_count: int = Field(default=3, description='每次生成几条推荐回复')
    min: int = 1
    max: int = 6


class ConfigResponse(_LooseModel):
    env_path: str = Field(description='配置写入的 .env 路径')
    classification: ModelLayer = Field(description='分类层：Jev 意图 / 情绪')
    generation: GenerationLayer = Field(description='生成层：潜台词 / 回复建议（OpenAI 兼容）')
    output: OutputSettings = Field(description='输出设置：推荐回复条数等')
    saved: list[str] | None = Field(default=None, description='本次实际写入的 .env 键')


class LayerUpdate(_LooseModel):
    base_url: str | None = Field(default=None, description='留空 = 不改动')
    model: str | None = None
    api_key: str | None = Field(default=None, description='留空 = 保留原来的密钥')


class LLMProfileUpdate(_LooseModel):
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = Field(default=None, description='留空 = 保留原来那把密钥')
    source: str | None = Field(default=None,
                               description='这套配置在服务端原来的名字；改名后靠它找到原来的密钥')


class GenerationUpdate(LayerUpdate):
    """生成层改动：给 profiles = 整表替换（增删改都在里面），给 active = 切换启用哪套。

    两个都不给、只给 base_url / model / api_key 时，改的是「当前启用」那一套（兼容旧前端）。
    """

    profiles: list[LLMProfileUpdate] | None = None
    active: str | None = None


class OutputUpdate(_LooseModel):
    suggestions_count: int | None = None


class ConfigUpdateRequest(_LooseModel):
    classification: LayerUpdate | None = None
    generation: GenerationUpdate | None = None
    output: OutputUpdate | None = None
    scope: str | None = Field(default=None, description='连通性自检范围：classification / generation / all')


class TestResult(_LooseModel):
    ok: bool
    detail: str = ''
    endpoint: str = ''


class ConfigTestResponse(_LooseModel):
    classification: TestResult | None = None
    generation: TestResult | None = None
