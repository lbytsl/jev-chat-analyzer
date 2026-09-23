"""可视化配置接口：读当前 Jev / LLM 配置、保存到 .env、连通性自检。

- `GET /config`     只回打码后的密钥，前端不会拿到明文；
- `PATCH /config`   按行合并写回 .env，然后清缓存让配置立刻生效（不用重启）；
- `POST /config/test` 可以带上还没保存的值做探测，先验证再保存。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import SettingsDep
from app.api.guards import guard_json_post
from app.schemas.common import ErrorResponse
from app.schemas.config import ConfigResponse, ConfigTestResponse, ConfigUpdateRequest

router = APIRouter(tags=['配置'])

_ERRORS = {400: {'model': ErrorResponse, 'description': '配置项不合法'},
           403: {'model': ErrorResponse, 'description': '来源或 Content-Type 不正确'}}


@router.get('/config', response_model=ConfigResponse, summary='当前模型配置（密钥打码）')
def get_config(settings: SettingsDep) -> dict:
    return settings.describe()


@router.patch('/config', response_model=ConfigResponse, responses=_ERRORS,
              dependencies=[Depends(guard_json_post)], summary='保存模型配置到 .env')
def update_config(payload: ConfigUpdateRequest, settings: SettingsDep) -> dict:
    return settings.update(payload.model_dump())


@router.post('/config/test', response_model=ConfigTestResponse, responses=_ERRORS,
             dependencies=[Depends(guard_json_post)], summary='连通性自检（可用未保存的值）')
def test_config(payload: ConfigUpdateRequest, settings: SettingsDep) -> dict:
    return settings.test_connections(payload.model_dump())
