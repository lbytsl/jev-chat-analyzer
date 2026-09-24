"""分析相关请求 / 响应模型。

请求模型刻意宽松（`extra='allow'`、字段可空）：业务校验统一在 services 层做，
这样 400 的响应体仍然是「一句中文提示」，前端 `callAPI` 拿到 `error` 就能直接显示。
响应模型只用于 OpenAPI 文档（路由里通过 `responses=` 引用，不做 response_model，
避免 FastAPI 过滤掉上游返回的动态字段）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _LooseModel(BaseModel):
    model_config = ConfigDict(extra='allow')


# ---------- 请求 ----------
class MessageAnalysisRequest(_LooseModel):
    """POST /analyze —— 单条消息分类（外部脚本与夹具用，前端走 /analyze-chat）。

    字符串字段默认空串（而不是 None）：旧版用 `data.get(key, '')`，缺字段当成空串走
    业务校验，于是报的是「请选择关系或场景」这类可操作的提示，而不是格式错误。
    """

    message: str = ''
    context: str = ''
    relationship: str = Field(default='', description='暧昧 / 恋爱 / 上下级 / 同事')


class TranscriptAnalysisRequest(_LooseModel):
    """POST /analyze-chat —— 整段聊天记录分析。"""

    transcript: str = Field(default='', description='粘贴的聊天记录，支持「名字 ⏎ 时间 ⏎ 内容」或「我：…」两种格式')
    relationship: str = ''
    me_label: str | None = Field(default=None, description='指定哪个标签是「我」')
    read_labels: list[str] | None = Field(default=None, description='要解读的说话人标签列表（可含「我」）')
    include_me: bool | None = Field(default=None, description='旧契约：等价于 read_labels = 全部对方 + 我')
    gen_interpretation: bool = False
    gen_suggestions: bool = False
    session_id: str | None = Field(default=None, description='当前会话：同正文原地更新，不同正文另起一条')


class GenerationRequest(_LooseModel):
    """POST /interpret-chat 与 POST /suggest-chat 的公共请求体（两者都只补跑生成层，不重跑分类）。

    两种用法：
    - 传 `prev`（老路径）：前端把已经拿到的结果原样回传，后端只补生成，不落库；
    - 传 `session_id`（新路径）：后端直接读会话库，补完生成后写回，并把合并好的完整结果
      放在 `session` 字段返回（前端不必自己合并，也不会和服务端存的内容分叉）。
    """

    prev: dict[str, Any] | None = Field(default=None, description='上一次 /analyze-chat 的完整返回')
    session_id: str | None = Field(default=None, description='会话 id（与 prev 二选一，优先 session_id）')
    indexes: list[int] | None = Field(
        default=None,
        description='只对这些消息序号生成（单条生成用）；不传 = 潜台词全体 / 推荐回复只跑最后一条',
    )


class AppendRequest(_LooseModel):
    """POST /append-chat —— 追加新消息，只对新消息跑分类。"""

    prev: dict[str, Any] | None = None
    session_id: str | None = None
    transcript: str = ''
    old_count: int | None = Field(default=None, description='上一次已分析的消息条数')


# ---------- 响应（仅用于文档） ----------
class RankedLabel(_LooseModel):
    label: str
    score: float


class LabelAnswer(_LooseModel):
    key: str
    label: str
    canonical_key: str | None = None
    canonical_label: str | None = None
    score: float | None = None
    score_kind: str | None = None
    confidence: float | None = None
    definition: str | None = None
    probabilities: dict[str, float] = Field(default_factory=dict)
    ranked: list[RankedLabel] = Field(default_factory=list)
    # 情绪层专有
    display: str | None = None
    family: str | None = None
    family_score: float | None = None
    families_considered: list[str] | None = None
    coarse: bool | None = None


class Suggestion(_LooseModel):
    label: str
    text: str


class ConfidenceLayer(_LooseModel):
    label: str | None = None
    score: float = 0
    gap: float = 0
    ok: bool = False
    top: list[RankedLabel] = Field(default_factory=list)


class ConfidenceFlags(_LooseModel):
    intent: ConfidenceLayer
    emotion: ConfidenceLayer
    low_intent: bool = False
    low_emotion: bool = False
    low: bool = False
    reasons: list[str] = Field(default_factory=list)
    trigger: str | None = None
    pool_worthy: bool = False
    pool_reasons: list[str] = Field(default_factory=list)


class AnalysisResult(_LooseModel):
    """单条消息的分类 + 生成结果。`answers` 是 Jev 原始返回，原样透传便于排查。"""

    version: str
    analysis_schema: str | None = None
    prompt_version: str | None = None
    label_version: str | None = None
    model: str | None = None
    elapsed_ms: int
    intent_family: LabelAnswer | None = None
    primary_intent: LabelAnswer
    emotion: LabelAnswer
    emotion_family: LabelAnswer | None = None
    relation_direction: LabelAnswer | None = None
    response_need: LabelAnswer | None = None
    communication_style: LabelAnswer | None = None
    uncertainty: dict[str, Any] | None = None
    answers: dict[str, Any] = Field(default_factory=dict)
    interpretation: str | None = None
    intent_detail: str | None = None
    emotion_detail: str | None = None
    suggestions: list[Suggestion] | None = None
    gen_failed: bool = Field(default=False, description='推荐回复生成失败')
    interpretation_failed: bool = Field(default=False, description='潜台词生成失败')
    gen_error: str | None = None
    gen_skipped: bool = False
    low_confidence: bool | None = None
    confidence_flags: ConfidenceFlags | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)


class MessageItem(_LooseModel):
    index: int
    speaker: str = Field(description='me / other')
    label: str | None = None
    text: str
    timestamp: str | None = None


class AnalysisItem(_LooseModel):
    index: int
    speaker: str = Field(description='我 / 对方（注意这里是中文，与 MessageItem 不同）')
    label: str | None = None
    message: str
    context: str = ''
    result: AnalysisResult


class SpeakerItem(_LooseModel):
    label: str
    role: str
    count: int


class TranscriptAnalysisResponse(_LooseModel):
    version: str
    relationship: str
    messages: list[MessageItem]
    analyses: list[AnalysisItem]
    count: int
    count_other: int
    count_me: int
    failed_count: int
    failed_indexes: list[int]
    include_me: bool
    speakers: list[SpeakerItem]
    me_label: str | None = None
    other_label: str | None = None
    other_labels: list[str] = Field(default_factory=list)
    read_labels: list[str] = Field(default_factory=list)
    gen_interpretation: bool = False
    gen_suggestions: bool = False
    reply_target: AnalysisItem | None = None


class GenerationResponse(_LooseModel):
    """POST /interpret-chat 与 POST /suggest-chat 的响应。

    `augmentations` 按消息序号索引，**只包含本次这一类的字段**（两个端点的产物不混在一起）：
    - 潜台词：`intent_detail`（成功时存在）
    - 推荐回复：`suggestions`（成功时存在）
    失败项只有 `gen_failed` / `gen_error`，前端按「字段是否存在」合并，不会误清另一类结果。

    不回带整份会话：会话语境里的另一类字段是**存量数据**（上一次生成的），夹在响应里会让人
    误以为这个端点也生成了另一类内容；前端按同一套规则把 `augmentations` 合并进本地结果。
    """

    version: str
    relationship: str
    kind: str = Field(description='interpretation / suggestions')
    augmentations: dict[str, Any] = Field(default_factory=dict,
                                          description='按消息序号索引的本次生成结果（只有这一类字段）')
    failed_indexes: list[int] = Field(default_factory=list)
    last_index: int
    session_id: str | None = Field(default=None, description='带会话调用时回传，便于前端刷新列表')
