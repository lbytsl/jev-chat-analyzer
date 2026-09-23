"""通用响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """GET /health —— 启动脚本与前端都靠它判断「本地服务是否可用、跑的是哪版」。"""

    ok: bool = Field(description='Jev 分类层是否可用（key 已配置）')
    api_key: str = Field(description='「已配置」/「缺失」')
    general_llm: str
    model: str
    gen_model: str
    version: str


class ErrorResponse(BaseModel):
    """统一错误体：前端只读 `error`，`code` 用于区分上游错误类型。"""

    model_config = ConfigDict(extra='allow')

    error: str
    code: str | None = None
