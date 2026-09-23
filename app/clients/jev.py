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

from app.clients.http import read_timeout, shared_client
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    JevAPIError,
    JevConfigurationError,
    JevConnectionError,
)
from app.core.logging import get_logger
from app.core.retry import backoff_delay, parse_retry_after

logger = get_logger('jev')

TIMEOUT_SECONDS = 45.0          # 单次读超时上限（模型推理本身就慢）
# 一次 decide() 的总预算，含重试与退避 sleep。没有它的话最坏是「45s × 4 次 + 退避 ≈ 3 分钟」，
# 用户那边看不出与卡死的区别；超预算就直接放弃这一条，让上层的失败标记去处理。
TOTAL_BUDGET_SECONDS = 60.0
MAX_ATTEMPTS = 4
RETRYABLE_STATUS = (408, 425, 429)
DETAIL_LIMIT = 2000
HTTP_CLIENT_NAME = 'jev'        # 进程级共享客户端（连接池复用），见 clients/http.py

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


def sleep_within_budget(delay: float, deadline: float) -> bool:
    """睡 delay 秒，但不超过剩余预算；预算已经耗尽则返回 False（别再重试了）。

    deadline 用 time.monotonic()（不受系统时钟调整影响）。
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(delay, remaining))
    return True


class JevClient:
    def __init__(self, settings: Settings | None = None, timeout: float = TIMEOUT_SECONDS,
                 total_budget: float = TOTAL_BUDGET_SECONDS):
        self._settings = settings or get_settings()
        self._timeout = timeout
        self._total_budget = total_budget

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
        """发一次请求并重试，总时长受 `self._total_budget` 限制（含退避 sleep）。

        连接走进程级共享客户端（clients/http.py），不再每次新建——一条消息两次调用、
        50 条就是 100 次请求，握手开销不该乘 100。
        """
        client = shared_client(HTTP_CLIENT_NAME, max_connections=8)
        deadline = time.monotonic() + self._total_budget
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning('Jev 请求超出 %ss 预算，停止重试：%s', self._total_budget, url)
                break
            # 读超时按剩余预算收窄，避免「预算只剩 2s，却还挂 45s 等一次读」。
            try:
                response = client.post(url, json=body, headers=headers,
                                       timeout=read_timeout(min(self._timeout, remaining)))
            except (httpx.TimeoutException, httpx.RequestError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                if (attempt < MAX_ATTEMPTS - 1
                        and not sleep_within_budget(backoff_delay(attempt), deadline)):
                    break
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
                if not sleep_within_budget(1.5, deadline):
                    break
                continue
            # 认证和参数错误应立即暴露；限流、超时和服务端抖动可以安全重试。
            if status not in RETRYABLE_STATUS and not 500 <= status < 600:
                raise JevAPIError(status, detail)
            logger.warning('Jev 返回 HTTP %s（第 %s 次尝试）', status, attempt + 1)
            last_error = JevAPIError(status, detail)
            if (attempt < MAX_ATTEMPTS - 1
                    and not sleep_within_budget(self._retry_delay(response, attempt), deadline)):
                break

        if isinstance(last_error, JevAPIError):
            raise last_error
        raise JevConnectionError('Jev 上游暂时连接不上') from last_error

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        """本次退避时长；退避数学与生成层共用（见 core/retry.py）。"""
        retry_after = response.headers.get('Retry-After') if response.headers else None
        return backoff_delay(attempt, parse_retry_after(retry_after))
