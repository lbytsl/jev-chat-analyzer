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


class OutputSettings(_LooseModel):
    suggestions_count: int = Field(default=3, description='每次生成几条推荐回复')
    min: int = 1
    max: int = 6


class ConfigResponse(_LooseModel):
    env_path: str = Field(description='配置写入的 .env 路径')
    classification: ModelLayer = Field(description='分类层：Jev 意图 / 情绪')
    generation: ModelLayer = Field(description='生成层：潜台词 / 回复建议（OpenAI 兼容）')
    output: OutputSettings = Field(description='输出设置：推荐回复条数等')
    saved: list[str] | None = Field(default=None, description='本次实际写入的 .env 键')


class LayerUpdate(_LooseModel):
    base_url: str | None = Field(default=None, description='留空 = 不改动')
    model: str | None = None
    api_key: str | None = Field(default=None, description='留空 = 保留原来的密钥')


class OutputUpdate(_LooseModel):
    suggestions_count: int | None = None


class ConfigUpdateRequest(_LooseModel):
    classification: LayerUpdate | None = None
    generation: LayerUpdate | None = None
    output: OutputUpdate | None = None


class TestResult(_LooseModel):
    ok: bool
    detail: str = ''
    endpoint: str = ''


class ConfigTestResponse(_LooseModel):
    classification: TestResult
    generation: TestResult
