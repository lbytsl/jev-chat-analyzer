"""会话接口：持久记忆的读取、切换与删除。

分析类接口（/analyze-chat 等）负责把结果写进会话；这里只负责「读出来、删掉」。
切换会话就是一次本地文件读，不触发任何模型调用。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import SessionDep
from app.api.guards import guard_json_post, guard_origin
from app.core.exceptions import InvalidRequest, NotFoundError
from app.schemas.common import ErrorResponse
from app.schemas.session import (
    SessionDeleteRequest,
    SessionDeleteResponse,
    SessionDetail,
    SessionListResponse,
)

router = APIRouter(tags=['会话'])

_ERRORS = {400: {'model': ErrorResponse, 'description': '入参不合法'},
           403: {'model': ErrorResponse, 'description': '来源或 Content-Type 不正确'},
           404: {'model': ErrorResponse, 'description': '会话不存在'}}


@router.get('/sessions', response_model=SessionListResponse,
            summary='会话列表（最近 N 条，按最后活跃倒序）')
def list_sessions(sessions: SessionDep, limit: int | None = Query(default=None, ge=1)) -> dict:
    return sessions.list_sessions(limit)


@router.get('/sessions/{session_id}', responses={**_ERRORS, 200: {'model': SessionDetail}},
            summary='会话详情（含全部消息与分析结果）')
def get_session(session_id: str, sessions: SessionDep) -> dict:
    return sessions.load(session_id)


@router.delete('/sessions/{session_id}', response_model=SessionDeleteResponse, responses=_ERRORS,
               dependencies=[Depends(guard_origin)], summary='删除单个会话')
def delete_session(session_id: str, sessions: SessionDep) -> dict:
    if not sessions.delete([session_id]):
        raise NotFoundError('这个会话不存在，可能已经被删除。')
    return {'deleted': 1, 'session_ids': [session_id]}


@router.post('/sessions/delete', response_model=SessionDeleteResponse, responses=_ERRORS,
             dependencies=[Depends(guard_json_post)], summary='批量删除会话（多选删除）')
def delete_sessions(payload: SessionDeleteRequest, sessions: SessionDep) -> dict:
    ids = [str(item) for item in (payload.ids or []) if str(item).strip()]
    if not ids:
        raise InvalidRequest('请先勾选要删除的会话。')
    return {'deleted': sessions.delete(ids), 'session_ids': ids}
