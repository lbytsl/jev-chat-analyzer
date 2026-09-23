"""Jev 分类网关客户端。

两种接入方式，路径规则不同，不能一律拼 `/v1/systemone`：

- TypeSafe 原生网关：`TYPESAFE_BASE_URL` 只写主机名（https://api.typesafe.ai）→ 补 `/v1/systemone`
- OpenRouter alpha：BASE_URL 已是完整端点（https://openrouter.ai/api/alpha/decisions）→ 原样使用

请求体是 Jev 私有协议：`{model, state, questions}`，`questions` 是「问题名 → 判定任务」的映射。
重试策略刻意保守：鉴权/参数错误立即抛出，只有连接抖动与 429/5xx 才重试，
避免把配置问题伪装成「网络不好」。
"""
from __future__ import annotations

import time
import urllib.parse

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    JevAPIError,
    JevConfigurationError,
    JevConnectionError,
)
from app.core.logging import get_logger

logger = get_logger('jev')

TIMEOUT_SECONDS = 45.0
MAX_ATTEMPTS = 4
RETRYABLE_STATUS = (408, 425, 429)
DETAIL_LIMIT = 2000

# TypeSafe 上游网关会拒绝 httpx/urllib 的默认请求签名（HTTP 403 / error 1010），
# 必须伪装成浏览器 UA。这是实测结论，不是随便加的。
BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15')


def resolve_endpoint(base_url: str) -> str:
    """base 只写主机名时补原生路径；已经带路径（OpenRouter）则原样使用。

    2026-09 迁到 OpenRouter 时，因为无条件追加原生路径，请求打到了
    `/api/alpha/decisions/v1/systemone`，服务端直接 404，看起来像「key 没读到」。
    """
    base = (base_url or 'https://api.typesafe.ai').rstrip('/')
    if urllib.parse.urlsplit(base).path.strip('/'):
        return base
    return base + '/v1/systemone'


class JevClient:
    def __init__(self, settings: Settings | None = None, timeout: float = TIMEOUT_SECONDS):
        self._settings = settings or get_settings()
        self._timeout = timeout

    @property
    def endpoint(self) -> str:
        return resolve_endpoint(self._settings.typesafe_base_url)

    def decide(self, state: dict, questions: dict) -> dict:
        """发一次 Jev 请求，返回原始 JSON 响应。

        情绪改两级路由后，一次分析会连着调两次，重试逻辑集中在这里复用。
        """
        key = self._settings.typesafe_api_key
        if not key:
            raise JevConfigurationError('未配置 Jev API key')
        body = {
            'model': self._settings.typesafe_default_model,
            'state': state,
            'questions': questions,
        }
        headers = {
            'Authorization': 'Bearer ' + key,
            'Content-Type': 'application/json',
            'User-Agent': BROWSER_UA,
        }
        return self._post_with_retry(self.endpoint, body, headers)

    def _post_with_retry(self, url: str, body: dict, headers: dict) -> dict:
        last_error: Exception | None = None
        with httpx.Client(timeout=self._timeout) as client:
            for attempt in range(MAX_ATTEMPTS):
                try:
                    response = client.post(url, json=body, headers=headers)
                except (httpx.TimeoutException, httpx.RequestError, TimeoutError, ConnectionError) as exc:
                    last_error = exc
                    if attempt < MAX_ATTEMPTS - 1:
                        time.sleep(1.2 * (attempt + 1))
                    continue

                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise JevAPIError(response.status_code, response.text[:DETAIL_LIMIT]) from exc

                detail = response.text[:DETAIL_LIMIT]
                status = response.status_code
                # 当前 Key 随后的最小请求可以正常成功，说明偶发 403 不一定是永久鉴权失败。
                # 只额外重试一次；第二次仍为 403 就立即暴露，避免无效地重放整批消息。
                if status == 403 and attempt == 0:
                    last_error = JevAPIError(status, detail)
                    time.sleep(1.5)
                    continue
                # 认证和参数错误应立即暴露；限流、超时和服务端抖动可以安全重试。
                if status not in RETRYABLE_STATUS and not 500 <= status < 600:
                    raise JevAPIError(status, detail)
                last_error = JevAPIError(status, detail)
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(self._retry_delay(response, attempt))

        if isinstance(last_error, JevAPIError):
            raise last_error
        raise JevConnectionError('Jev 上游暂时连接不上') from last_error

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get('Retry-After') if response.headers else None
        try:
            delay = max(1.2 * (attempt + 1), float(retry_after)) if retry_after else 1.2 * (attempt + 1)
        except (TypeError, ValueError):
            delay = 1.2 * (attempt + 1)
        return min(delay, 8)
