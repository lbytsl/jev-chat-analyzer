"""生成层客户端：任何 OpenAI 兼容端点（DeepSeek / 通义 / GLM / Kimi / 本地 vLLM…）。

只负责「发消息、拿回一个 JSON 对象」，提示词在 domain/prompts.py，字段整形在
services/generation.py。这样换端点只动配置，换提示词只动领域层。

两种取数方式：
- `complete_json`    一次性拿完整回答（脚本、测试、不支持流式的端点用）；
- `stream_json`      边收边把「已累计的原文」交给回调，结束时同样返回解析后的对象。
  回调拿到的是**原始累计文本**（半截 JSON），怎么解读交给上层（services/generation.py）。

一条实测约束：`deepseek-flash` 偶发把 max_tokens 跑满、把 JSON 截断成空串
（finish_reason=length）。所以两种方式都对「截断 / 解析失败 / 缺少必需字段」做重试，
并逐轮加大 max_tokens；流式重试前会先 `on_reset()`，让前端丢掉上一轮的半截内容。
"""
from __future__ import annotations

import json
import time
from typing import Callable

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import GeneralLLMError

TIMEOUT_SECONDS = 40.0
MAX_ATTEMPTS = 3
INITIAL_MAX_TOKENS = 1200
MAX_TOKENS_CEILING = 4000

# 重试前先让调用方清掉半截内容（流式才有意义）。
DeltaCallback = Callable[[str], None]


def parse_json_content(content) -> dict | None:
    """从模型返回里尽量抠出 JSON 对象：先剥 markdown 代码块，再裸解析；失败再截取首个 {...} 到最后 }。"""
    if not isinstance(content, str):
        return None
    text = content.strip()
    # 模型偶发把 JSON 包在 ```json ... ``` 里（即便声明了 response_format=json_object）。
    if text.startswith('```'):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


class GeneralLLMClient:
    def __init__(self, settings: Settings | None = None, timeout: float = TIMEOUT_SECONDS):
        self._settings = settings or get_settings()
        self._timeout = timeout

    @property
    def endpoint(self) -> str:
        return (self._settings.deepseek_base_url or '').rstrip('/') + '/chat/completions'

    @property
    def _headers(self) -> dict:
        return {'Authorization': 'Bearer ' + (self._settings.deepseek_api_key or ''),
                'Content-Type': 'application/json'}

    def _body(self, system: str, user: str, max_tokens: int, stream: bool) -> dict:
        return {
            'model': self._settings.deepseek_model,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'response_format': {'type': 'json_object'},
            'temperature': 0.3,
            'max_tokens': max_tokens,
            'stream': stream,
        }

    def _check_key(self) -> None:
        if not self._settings.deepseek_api_key:
            raise GeneralLLMError('未配置 DeepSeek API key（请在项目根目录 .env 里补 DEEPSEEK_API_KEY）')

    @staticmethod
    def _status_error(status: int, detail: str) -> tuple[GeneralLLMError, bool]:
        """把 HTTP 状态码翻成 (错误, 是否值得重试)。401/403 没救，直接不重试。"""
        if status in (401, 403):
            return GeneralLLMError('DeepSeek 鉴权失败（HTTP {}）：{}'.format(status, detail)), False
        if status == 429 or 500 <= status < 600:
            return GeneralLLMError('DeepSeek 返回 HTTP {}：{}'.format(status, detail)), True
        return GeneralLLMError('DeepSeek 返回 HTTP {}：{}'.format(status, detail)), False

    @staticmethod
    def _parse_delta_line(line: str) -> tuple[str | None, str | None]:
        """从一条 SSE 行里取出 (增量文本, finish_reason)；非数据行 / 结束标记都返回 (None, None)。"""
        if not line or not line.startswith('data:'):
            return None, None
        data = line[5:].strip()
        if not data or data == '[DONE]':
            return None, None
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return None, None
        choices = chunk.get('choices') or []
        if not choices:
            return None, None
        choice = choices[0]
        return (choice.get('delta', {}).get('content') or None), choice.get('finish_reason')

    def _invalid_answer(self, buffer: str, finish: str | None) -> GeneralLLMError:
        snippet = (buffer or '')[:200].replace('\n', ' ')
        if finish == 'length':
            return GeneralLLMError('回答被截断（finish_reason=length），内容片段：{}'.format(snippet))
        return GeneralLLMError('返回内容不是可解析的 JSON，内容片段：{}'.format(snippet))

    def complete_json(self, system: str, user: str,
                      accept: Callable[[dict], bool] | None = None) -> dict:
        """请求 JSON 输出并返回解析后的对象（一次性拿完整回答）。

        accept 用来判断「这轮算不算成功」（例如必须有 suggestions）；不满足就按
        截断处理、加大 max_tokens 重试。任何失败抛 GeneralLLMError。
        """
        self._check_key()
        url = self.endpoint
        max_tokens = INITIAL_MAX_TOKENS
        last_error: Exception | None = None

        with httpx.Client(timeout=self._timeout) as client:
            for attempt in range(MAX_ATTEMPTS):
                try:
                    response = client.post(url, json=self._body(system, user, max_tokens, False),
                                           headers=self._headers)
                except (httpx.TimeoutException, httpx.RequestError, TimeoutError, ConnectionError) as exc:
                    last_error = GeneralLLMError('DeepSeek 上游暂时连接不上：{}'.format(exc))
                    if attempt < MAX_ATTEMPTS - 1:
                        time.sleep(1.5 * (attempt + 1))
                    continue

                if response.status_code >= 400:
                    error, retryable = self._status_error(response.status_code, response.text[:1500])
                    if not retryable:
                        raise error
                    last_error = error
                    if attempt < MAX_ATTEMPTS - 1:
                        time.sleep(1.5 * (attempt + 1))
                    continue

                try:
                    payload = response.json()
                except ValueError as exc:
                    last_error = GeneralLLMError('DeepSeek 返回结构无法解析：{}'.format(exc))
                    continue
                try:
                    content = payload['choices'][0]['message']['content']
                    finish = payload['choices'][0].get('finish_reason')
                except (KeyError, IndexError, TypeError) as exc:
                    last_error = GeneralLLMError('DeepSeek 返回结构无法解析：{}'.format(exc))
                    continue

                # v008：判定成功的条件改为「拿到可用的 suggestions」即可
                # （intent_detail 允许为空 = 这句没有潜台词）。
                parsed = parse_json_content(content)
                if isinstance(parsed, dict) and (accept is None or accept(parsed)):
                    return parsed

                # 解析失败或被截断：下一轮加大 max_tokens 再试。
                last_error = self._invalid_answer(content or '', finish)
                if finish == 'length':
                    max_tokens = min(max_tokens * 2, MAX_TOKENS_CEILING)
                elif attempt < MAX_ATTEMPTS - 1:
                    max_tokens = min(max_tokens + 600, MAX_TOKENS_CEILING)

        raise last_error or GeneralLLMError('DeepSeek 未能生成有效结果')

    def stream_json(self, system: str, user: str, accept: Callable[[dict], bool] | None = None,
                    on_delta: DeltaCallback | None = None,
                    on_reset: Callable[[], None] | None = None) -> dict:
        """流式请求 JSON：边收边把「已累计的原文」交给 on_delta，结束时返回解析后的对象。

        与 complete_json 完全同构——校验条件、重试策略、max_tokens 递增都一样，差别只在
        取数方式。重试前调 on_reset()，让前端把上一轮的半截内容清掉再重画。

        注意：有些兼容端点（含本地 vLLM 类）是「生成完再一次性 flush」，这种端点下
        回调只会在末尾被调一次——协议层没问题，只是看不到逐字效果。
        """
        self._check_key()
        url = self.endpoint
        max_tokens = INITIAL_MAX_TOKENS
        last_error: Exception | None = None

        with httpx.Client(timeout=self._timeout) as client:
            for attempt in range(MAX_ATTEMPTS):
                buffer = ''
                finish = None
                try:
                    with client.stream('POST', url, json=self._body(system, user, max_tokens, True),
                                       headers=self._headers) as response:
                        if response.status_code >= 400:
                            error, retryable = self._status_error(
                                response.status_code, response.read().decode('utf-8', 'replace')[:1500])
                            if not retryable:
                                raise error
                            last_error = error
                            if attempt < MAX_ATTEMPTS - 1:
                                time.sleep(1.5 * (attempt + 1))
                            continue
                        for line in response.iter_lines():
                            chunk, reason = self._parse_delta_line(line)
                            if reason:
                                finish = reason
                            if chunk is None:
                                continue
                            buffer += chunk
                            if on_delta is not None:
                                on_delta(buffer)
                except (httpx.TimeoutException, httpx.RequestError, TimeoutError, ConnectionError) as exc:
                    last_error = GeneralLLMError('DeepSeek 上游暂时连接不上：{}'.format(exc))
                    if on_reset is not None:
                        on_reset()
                    if attempt < MAX_ATTEMPTS - 1:
                        time.sleep(1.5 * (attempt + 1))
                    continue

                parsed = parse_json_content(buffer)
                if isinstance(parsed, dict) and (accept is None or accept(parsed)):
                    return parsed

                # 半截 / 被截断：清掉前端那一轮，加大 max_tokens 再来。
                last_error = self._invalid_answer(buffer, finish)
                if on_reset is not None:
                    on_reset()
                if finish == 'length' or not buffer:
                    max_tokens = min(max_tokens * 2, MAX_TOKENS_CEILING)
                elif attempt < MAX_ATTEMPTS - 1:
                    max_tokens = min(max_tokens + 600, MAX_TOKENS_CEILING)

        raise last_error or GeneralLLMError('DeepSeek 未能生成有效结果')
