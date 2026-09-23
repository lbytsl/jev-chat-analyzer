"""分析接口。

五个端点对应前端的动作，响应结构对前端是硬契约：

| 端点 | 用途 | 是否调生成层 |
|------|------|--------------|
| POST /analyze        | 单条消息分类（脚本 / 夹具） | 不调 |
| POST /analyze-chat   | 整段分析 | 由两个勾选框决定（两块各自独立调用） |
| POST /interpret-chat | 已有结果上补跑「潜台词」 | 只跑潜台词 |
| POST /suggest-chat   | 已有结果上补跑「推荐回复」 | 只跑推荐回复 |
| POST /append-chat    | 追加新消息，只跑分类 | 不调 |

潜台词与推荐回复是**两个独立端点**：各自一套提示词、各自一次调用、各自标记失败。
这样点哪个只付哪个的 token，一边挂了不影响另一边，也不会再出现「勾了推荐回复却顺带
冒出潜台词」的串味。

「持久记忆」落在写结果的动作收尾：分析完就写进会话库（见 app/services/sessions.py）。
补跑类端点两种用法都兼顾——带 `session_id` 时以库里的会话为准（前端不必回传 prev，
返回的 `session` 字段是「合并 + 落库」后的权威副本）；带 `prev` 时保持老行为，不落库。

路由里的 `def`（不是 `async def`）：分类/生成都是阻塞 IO，交给 Starlette 的线程池，
否则会把事件循环卡死（一次整段分析要几十秒）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import ClassifierDep, PipelineDep, SessionDep
from app.api.guards import guard_json_post
from app.api.streaming import sse_response
from app.services.pipeline import PipelineService
from app.services.sessions import SessionService
from app.schemas.analysis import (
    AnalysisResult,
    AppendRequest,
    GenerationRequest,
    GenerationResponse,
    MessageAnalysisRequest,
    TranscriptAnalysisRequest,
    TranscriptAnalysisResponse,
)
from app.schemas.common import ErrorResponse

router = APIRouter(tags=['分析'])

GuardDep = Annotated[None, Depends(guard_json_post)]

_ERRORS = {400: {'model': ErrorResponse, 'description': '入参或业务校验失败'},
           403: {'model': ErrorResponse, 'description': '来源或 Content-Type 不正确'},
           500: {'model': ErrorResponse, 'description': '本地缺少 Jev 配置'},
           502: {'model': ErrorResponse, 'description': 'Jev 上游报错或返回结构不兼容'},
           503: {'model': ErrorResponse, 'description': 'Jev 上游连接不上'}}


@router.post('/analyze', responses={**_ERRORS, 200: {'model': AnalysisResult}},
             summary='单条消息：意图 + 情绪')
def analyze_single(payload: MessageAnalysisRequest, classifier: ClassifierDep,
                   _: GuardDep) -> dict:
    return classifier.classify_payload(payload.model_dump())


@router.post('/analyze-chat', responses={**_ERRORS, 200: {'model': TranscriptAnalysisResponse}},
             summary='整段聊天记录分析（并存入会话）')
def analyze_transcript(payload: TranscriptAnalysisRequest, pipeline: PipelineDep,
                       sessions: SessionDep, _: GuardDep) -> dict:
    data = payload.model_dump()
    transcript = (data.get('transcript') or '').strip()
    result = pipeline.analyze(data)
    # 每次导入都持久记忆：同正文原地更新当前会话，正文变了另起一条（见 SessionService）。
    meta = sessions.save_analysis(result, transcript, data.get('session_id'))
    return {**result, **meta}


@router.post('/interpret-chat', responses={**_ERRORS, 200: {'model': GenerationResponse}},
             summary='补跑潜台词（可指定单条）')
def interpret_chat(payload: GenerationRequest, pipeline: PipelineDep, sessions: SessionDep,
                   _: GuardDep) -> dict:
    return _run_generation(payload, pipeline, sessions, kind='interpretation')


@router.post('/suggest-chat', responses={**_ERRORS, 200: {'model': GenerationResponse}},
             summary='补跑推荐回复（可指定单条）')
def suggest_chat(payload: GenerationRequest, pipeline: PipelineDep, sessions: SessionDep,
                 _: GuardDep) -> dict:
    return _run_generation(payload, pipeline, sessions, kind='suggestions')


# ---------- 流式（SSE）版本：同一份实现，边算边推 ----------
@router.post('/analyze-chat/stream', summary='整段分析（流式：每条算完就推）')
def analyze_chat_stream(payload: TranscriptAnalysisRequest, pipeline: PipelineDep,
                        sessions: SessionDep, _: GuardDep) -> StreamingResponse:
    return sse_response(_stream_analyze(payload, pipeline, sessions))


@router.post('/interpret-chat/stream', summary='补跑潜台词（流式：逐字推）')
def interpret_chat_stream(payload: GenerationRequest, pipeline: PipelineDep,
                          sessions: SessionDep, _: GuardDep) -> StreamingResponse:
    return sse_response(_stream_generation(payload, pipeline, sessions, kind='interpretation'))


@router.post('/suggest-chat/stream', summary='补跑推荐回复（流式：逐条推）')
def suggest_chat_stream(payload: GenerationRequest, pipeline: PipelineDep,
                        sessions: SessionDep, _: GuardDep) -> StreamingResponse:
    return sse_response(_stream_generation(payload, pipeline, sessions, kind='suggestions'))


def _stream_analyze(payload: TranscriptAnalysisRequest, pipeline: PipelineService,
                    sessions: SessionService):
    """整段分析的流式流程：原样转发进度事件，`done` 时落库并把会话元数据带上。

    与非流式 `/analyze-chat` 完全同一套实现（`analyze_stream` 是唯一实现），
    所以两种调用方式的结果、落库内容、响应结构都一致。
    """
    data = payload.model_dump()
    transcript = (data.get('transcript') or '').strip()
    for event in pipeline.analyze_stream(data):
        if event['type'] == 'done':
            meta = sessions.save_analysis(event['data'], transcript, data.get('session_id'))
            event = {**event, **meta}
        yield event


def _stream_generation(payload: GenerationRequest, pipeline: PipelineService,
                       sessions: SessionService, kind: str):
    """补跑生成层的流式流程：转发预览事件，`done` 时合并进会话。"""
    data = payload.model_dump()
    session_id = data.get('session_id')
    stored = None
    if session_id:
        stored = sessions.load(session_id)
        data['prev'] = stored
    runner = pipeline.interpret_stream if kind == 'interpretation' else pipeline.suggest_stream
    for event in runner(data):
        if event['type'] == 'done' and session_id and stored is not None:
            # 读-合并-写回交给会话层加锁完成：两类生成并发时不会互相覆盖。
            sessions.apply_augmentations(session_id, event.get('augmentations') or {}, kind)
            event = {**event, 'session_id': session_id}
        yield event


def _run_generation(payload: GenerationRequest, pipeline: PipelineService,
                    sessions: SessionService, kind: str) -> dict:
    """两个补跑端点的公共流程：跑生成层 → （带会话时）合并进会话并落库。

    响应只包含**本次这一类**的产物（`augmentations`），不再回带整份会话：
    回带会话会让「推荐回复」的返回里出现存量的潜台词字段，看起来像这个端点也生成了潜台词。
    会话合并仍由服务端完成（加锁的读-改-写），前端按同一套「字段是否存在」规则合并本次增量。
    """
    data = payload.model_dump()
    session_id = data.get('session_id')
    stored = None
    if session_id:
        stored = sessions.load(session_id)
        data['prev'] = stored
    runner = pipeline.interpret if kind == 'interpretation' else pipeline.suggest
    response = runner(data)
    if session_id and stored is not None:
        # 读-合并-写回交给会话层加锁完成：两类生成并发时不会互相覆盖。
        sessions.apply_augmentations(session_id, response.get('augmentations') or {}, kind)
        response = {**response, 'session_id': session_id}
    return response


@router.post('/append-chat', responses={**_ERRORS, 200: {'model': TranscriptAnalysisResponse}},
             summary='追加新消息（只跑分类，并更新会话）')
def append_chat(payload: AppendRequest, pipeline: PipelineDep, sessions: SessionDep,
                _: GuardDep) -> dict:
    data = payload.model_dump()
    session_id = data.get('session_id')
    if session_id:
        data['prev'] = sessions.load(session_id)
    result = pipeline.append(data)
    transcript = (data.get('transcript') or '').strip()
    if session_id:
        meta = sessions.update_analysis(session_id, result, transcript)
    else:
        # 老脚本不带 session_id 时也照样存档，保证「每次导入都有记录」这条不破。
        meta = sessions.save_analysis(result, transcript, None)
    return {**result, **meta}
