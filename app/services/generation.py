"""生成层用例：潜台词与推荐回复各自独立——两套提示词、两次调用。

为什么要拆开（而不是一次调用返回两个字段）：

- 两件事的约束完全不同（潜台词 6-14 字、不许用第二人称；建议要 N 个明显不同的方向），
  混在一个提示词里时，任何一侧出问题都会把整次调用判失败，另一侧的结果也一起丢掉；
- 只要潜台词时不该顺带生成建议（反之亦然）：拆开后一次点击只付一次 token，也不会再出现
  「勾了推荐回复、最后一条却冒出潜台词」这种串味；
- 失败可以各自降级：潜台词挂了不影响回复建议，反之亦然。

流式：两个方法都可以接一个 `on_event` 回调。给了就走流式请求，把「模型正写到哪」推出去：

- 潜台词 → `{'type': 'delta', 'text': '这次新写出来的片段'}`（**增量**，不是累计全文：
  前端自己往后接，省掉每片都重发整句的 O(n²) 流量，语义也与 OpenAI 的 delta 一致）；
- 推荐回复 → `{'type': 'item', 'suggestion': {...}}`，建议一条条冒出来；
- 重试（截断/格式坏）或模型改写 → `{'type': 'reset'}`，让前端清掉已显示的内容重来。

回调拿到的是**半截 JSON 原文**，怎么解读由本模块的 `partial_string` / `partial_items` 负责
（累计值 → 与上一次发出的比对求增量）。最终结果永远以「完整回答解析 + 整形」为准，流式只是预览。

约定：两个方法都只抛 `GeneralLLMError`，由调用方标记对应的失败字段，不阻断分类结果。

v008 起 `interpretation` / `emotion_detail` 已废弃（键名保留恒为空，旧前端读它们不会报错）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from app.clients.general_llm import GeneralLLMClient
from app.core.config import clamp_suggestions_count, get_settings
from app.core.exceptions import GeneralLLMError
from app.domain.prompts import build_interpretation_messages, build_suggestions_messages

INTENT_DETAIL_MAX = 24

# 半截转义（`\u78` 这种还没写完的）要从片段尾部摘掉才能当 JSON 字符串解析。
_BROKEN_ESCAPE = re.compile(r'\\u[0-9a-fA-F]{0,3}$|\\(?!["\\/bfnrtu])$')


def _decode_partial(raw: str) -> str:
    """把 JSON 字符串片段解码成可显示文本；尾部半截转义先摘掉再试。"""
    candidate = raw
    for _ in range(3):
        try:
            return json.loads('"' + candidate + '"')
        except json.JSONDecodeError:
            trimmed = _BROKEN_ESCAPE.sub('', candidate)
            if trimmed == candidate:
                break
            candidate = trimmed
    return raw


def partial_string(text: str, key: str) -> str:
    """从半截 JSON 里抠出 `key` 的字符串值（值可能还没写完）。"""
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', text)
    if not match:
        return ''
    body, escaped = [], False
    for char in text[match.end():]:
        if escaped:
            body.append(char)
            escaped = False
        elif char == '\\':
            body.append(char)
            escaped = True
        elif char == '"':
            break
        else:
            body.append(char)
    return _decode_partial(''.join(body))


def partial_items(text: str, key: str, limit: int) -> list[dict]:
    """从半截 JSON 里取出**已经写完整**的数组项（数组还在陆续到达时用）。

    只做括号配对，不去猜字符串里的花括号——预览够用，最终结果以完整解析为准。
    """
    marker = text.find('"' + key + '"')
    if marker < 0:
        return []
    items, depth, start = [], 0, None
    for position in range(marker, len(text)):
        char = text[position]
        if char == '{':
            if depth == 0:
                start = position
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    item = json.loads(text[start:position + 1])
                except json.JSONDecodeError:
                    item = None
                if isinstance(item, dict):
                    items.append(item)
                start = None
                if len(items) >= limit:
                    break
    return items


@dataclass(frozen=True, slots=True)
class InterpretationContent:
    """潜台词：intent_detail 允许为空字符串（这句确实没有潜台词）。"""

    interpretation: str = ''
    intent_detail: str = ''
    emotion_detail: str = ''

    def to_dict(self) -> dict:
        return {'interpretation': self.interpretation,
                'intent_detail': self.intent_detail,
                'emotion_detail': self.emotion_detail}


@dataclass(frozen=True, slots=True)
class SuggestionsContent:
    suggestions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {'suggestions': list(self.suggestions)}


class GenerationService:
    def __init__(self, client: GeneralLLMClient | None = None, settings=None):
        self._settings = settings or get_settings()
        # 客户端也拿同一份 settings：界面上改完配置、服务实例重建后，这里读到的就是新值。
        self._client = client or GeneralLLMClient(self._settings)

    def suggestions_count(self) -> int:
        return clamp_suggestions_count(self._settings.gen_suggestions_count)

    # ---------- 潜台词 ----------
    def generate_interpretation(self, relationship, context, message, speaker, intent_result,
                                emotion_result, on_event: Callable[[dict], None] | None = None
                                ) -> InterpretationContent:
        system, user = build_interpretation_messages(
            relationship, context, message, speaker, intent_result, emotion_result)
        # 成功条件：拿到 intent_detail 键即可（空字符串是合法结果 = 这句没有潜台词）。
        accept = lambda payload: 'intent_detail' in payload
        if on_event is None:
            parsed = self._client.complete_json(system, user, accept=accept)
        else:
            watch, reset = self._interpretation_watcher(on_event)
            parsed = self._client.stream_json(system, user, accept=accept,
                                              on_delta=watch, on_reset=reset)
        return self._shape_interpretation(parsed)

    @staticmethod
    def _shape_interpretation(parsed: dict) -> InterpretationContent:
        detail = str(parsed.get('intent_detail') or '').strip().replace('\n', '')[:INTENT_DETAIL_MAX]
        # interpretation / emotion_detail 恒为空：前端不再展示，保留键名只为兼容旧前端。
        return InterpretationContent(interpretation='', intent_detail=detail, emotion_detail='')

    @staticmethod
    def _interpretation_watcher(on_event: Callable[[dict], None]):
        """把「累计原文」翻成**增量片段**推出去。返回 (on_delta, on_reset) 两个回调。

        客户端给的是累计原文（`{"intent_detail": "撒娇式应`…），这里每次都重新抠出当前值，
        与上一次发出的差一段，只把差的那段推给前端——每片不重发整句，前端负责往后接。

        模型若改写了已发出的内容（重试、或自己回头改字），就发 `reset` 让前端清空重画；
        最终值仍以完整解析的结果为准，预览不影响它。
        """
        state = {'sent': ''}

        def watch(buffer: str) -> None:
            text = partial_string(buffer, 'intent_detail')
            text = text.strip().replace('\n', '')[:INTENT_DETAIL_MAX]
            if text == state['sent']:
                return
            if text.startswith(state['sent']):
                fragment = text[len(state['sent']):]
                state['sent'] = text
                on_event({'type': 'delta', 'kind': 'interpretation', 'text': fragment})
                return
            # 已发出的内容被改写了：先清空，再把新内容整段发出去。
            state['sent'] = text
            on_event({'type': 'reset', 'kind': 'interpretation'})
            if text:
                on_event({'type': 'delta', 'kind': 'interpretation', 'text': text})

        def reset() -> None:
            state['sent'] = ''
            on_event({'type': 'reset', 'kind': 'interpretation'})

        return watch, reset

    # ---------- 推荐回复 ----------
    def generate_suggestions(self, relationship, context, message, speaker, intent_result,
                             emotion_result, on_event: Callable[[dict], None] | None = None
                             ) -> SuggestionsContent:
        count = self.suggestions_count()
        system, user = build_suggestions_messages(
            relationship, context, message, speaker, intent_result, emotion_result, count=count)
        accept = lambda payload: bool(payload.get('suggestions'))
        if on_event is None:
            parsed = self._client.complete_json(system, user, accept=accept)
        else:
            watch, reset = self._suggestions_watcher(on_event, count)
            parsed = self._client.stream_json(system, user, accept=accept,
                                              on_delta=watch, on_reset=reset)
        return self._shape_suggestions(parsed, count)

    @staticmethod
    def _suggestions_watcher(on_event: Callable[[dict], None], count: int
                             ) -> tuple[Callable[[str], None], Callable[[], None]]:
        """建议是一条条写进数组的：每写完一条推一条，前端就能逐个冒出来。

        返回 `(on_delta, on_reset)`。重试时模型会把整个数组重写一遍，所以 `reset` 必须把
        「已推送条数」一起归零——否则第二轮的第一条会被当成「发过了」而漏掉，前端要等
        `done` 才看得到建议（预览等于白做）。
        """
        state = {'sent': 0}

        def watch(buffer: str) -> None:
            items = partial_items(buffer, 'suggestions', count)
            while state['sent'] < len(items):
                item = GenerationService._normalize_suggestion(items[state['sent']])
                state['sent'] += 1
                if item is not None:
                    on_event({'type': 'item', 'kind': 'suggestions', 'suggestion': item})

        def reset() -> None:
            state['sent'] = 0
            on_event({'type': 'reset', 'kind': 'suggestions'})

        return watch, reset

    @staticmethod
    def _normalize_suggestion(item) -> dict | None:
        """单条建议整形：必须有正文；标签为空就兜底成「建议」。预览与最终结果共用这一套。"""
        if not isinstance(item, dict):
            return None
        label = str(item.get('label') or '').strip()
        text = str(item.get('text') or '').strip().replace('\n', '')
        if not text:
            return None
        return {'label': label[:12] or '建议', 'text': text}

    @classmethod
    def _shape_suggestions(cls, parsed: dict, count: int) -> SuggestionsContent:
        """整形与兜底：最多 count 条、必须有正文；一条都留不下就按失败处理。"""
        suggestions = []
        for item in (parsed.get('suggestions') or [])[:count]:
            normalized = cls._normalize_suggestion(item)
            if normalized is not None:
                suggestions.append(normalized)
        if not suggestions:
            raise GeneralLLMError('推荐回复返回内容不完整（suggestions={} 条）'.format(len(suggestions)))
        return SuggestionsContent(suggestions=suggestions)
