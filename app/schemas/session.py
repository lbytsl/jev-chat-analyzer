"""会话（持久记忆）相关的请求 / 响应模型。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _LooseModel(BaseModel):
    model_config = ConfigDict(extra='allow')


class SessionMeta(_LooseModel):
    """列表项：只带侧栏要显示的信息，不含 analyses 全文。"""

    id: str
    title: str = Field(description='默认取对方昵称')
    relationship: str
    preview: str = Field(default='', description='最后一条消息的截断预览')
    message_count: int = 0
    count_other: int = 0
    count_me: int = 0
    failed_count: int = 0
    gen_interpretation: bool = False
    gen_suggestions: bool = False
    created_at: int = 0
    updated_at: int = 0


class SessionListResponse(_LooseModel):
    sessions: list[SessionMeta]
    total: int = Field(description='库里的会话总数（列表只返回最近 N 条）')


class SessionDeleteRequest(_LooseModel):
    ids: list[str] = Field(default_factory=list, description='要删除的会话 id 列表（支持多选删除）')


class SessionDeleteResponse(_LooseModel):
    deleted: int
    session_ids: list[str] = Field(default_factory=list, description='实际删掉的会话 id')


# 会话详情 = 分析 envelope + 会话元数据；envelope 字段很多且会演进，这里只标注关键项，
# 其余用 extra 放行（路由不设 response_model，模型仅用于 OpenAPI 展示）。
class SessionDetail(_LooseModel):
    version: str
    session_id: str
    title: str = ''
    transcript: str = ''
    relationship: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    analyses: list[dict[str, Any]] = Field(default_factory=list)
    gen_interpretation: bool = False
    gen_suggestions: bool = False
    reply_target: dict[str, Any] | None = None
    created_at: int = 0
    updated_at: int = 0
