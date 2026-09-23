"""生成层测试：潜台词与推荐回复是两条独立链路（各自提示词、各自整形、各自失败）。"""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.exceptions import GeneralLLMError
from app.services.generation import GenerationService


def settings_with(count: int) -> Settings:
    return Settings(deepseek_api_key='sk-test', gen_suggestions_count=count)


class StubClient:
    """记下发出去的提示词，返回固定内容（模拟一次成功的模型响应）。"""

    def __init__(self, suggestions: int = 6, intent_detail: str = '嘴上嫌弃实际在撒娇'):
        self.calls: list[tuple[str, str]] = []
        self._suggestions = suggestions
        self._intent_detail = intent_detail

    def complete_json(self, system, user, accept):
        self.calls.append((system, user))
        payload = {'intent_detail': self._intent_detail,
                   'suggestions': [{'label': '方向{}'.format(i + 1), 'text': '话术{}'.format(i + 1)}
                                   for i in range(self._suggestions)]}
        assert accept is None or accept(payload)
        return payload


INTENT = {'label': '打情骂俏', 'definition': '带刺但没真怒气', 'score': 0.72}
EMOTION = {'label': '有点开心', 'definition': '被接住后的轻松', 'score': 0.61}


def build(client, count: int = 3) -> GenerationService:
    return GenerationService(client=client, settings=settings_with(count))


class TestInterpretation:
    def test_prompt_asks_for_interpretation_only(self):
        client = StubClient()
        content = build(client).generate_interpretation('恋爱', '我：在吗', '你管我', 'other',
                                                        INTENT, EMOTION)
        system, user = client.calls[0]
        assert content.intent_detail == '嘴上嫌弃实际在撒娇'
        assert content.interpretation == '' and content.emotion_detail == ''
        assert 'intent_detail' in system and 'suggestions' not in system
        assert 'intent_detail' in user and 'suggestions' not in user

    def test_intent_detail_is_trimmed(self):
        client = StubClient(intent_detail='这句其实没有潜台词' * 10)
        content = build(client).generate_interpretation('恋爱', '', '收到', 'other', INTENT, EMOTION)
        assert len(content.intent_detail) == 24

    def test_empty_interpretation_is_allowed(self):
        """纯事务交接的句子没有潜台词：空字符串是合法结果，不该按失败处理。"""
        client = StubClient(intent_detail='')
        content = build(client).generate_interpretation('恋爱', '', '收到，谢谢', 'other',
                                                       INTENT, EMOTION)
        assert content.intent_detail == ''


class TestSuggestions:
    def test_default_count_is_three(self):
        client = StubClient()
        content = build(client).generate_suggestions('恋爱', '我：在吗', '你管我', 'other',
                                                     INTENT, EMOTION)
        assert len(content.suggestions) == 3

    def test_configured_count_drives_prompt_and_shaping(self):
        client = StubClient()
        content = build(client, count=5).generate_suggestions('恋爱', '我：在吗', '你管我', 'other',
                                                              INTENT, EMOTION)
        system, user = client.calls[0]
        assert len(content.suggestions) == 5
        assert '数组长度必须是 5' in system
        assert '给出 5 条 suggestions' in user
        # 只问建议，不提潜台词
        assert 'intent_detail' not in system and 'intent_detail' not in user

    def test_count_is_clamped_to_allowed_range(self):
        client = StubClient()
        # 配置是人工可改的：越界值要收进范围，而不是把提示词写坏
        content = build(client, count=99).generate_suggestions('恋爱', '', '在', 'other',
                                                               INTENT, EMOTION)
        assert len(content.suggestions) == 6
        assert '数组长度必须是 6' in client.calls[0][0]

    def test_shape_rejects_empty_suggestions(self):
        class EmptyClient(StubClient):
            def complete_json(self, system, user, accept):
                return {'suggestions': []}

        with pytest.raises(GeneralLLMError, match='suggestions'):
            build(EmptyClient()).generate_suggestions('恋爱', '', '在', 'other', INTENT, EMOTION)

    def test_labels_are_normalized(self):
        class MessyClient(StubClient):
            def complete_json(self, system, user, accept):
                return {'suggestions': [{'label': '', 'text': '好呀\n'},
                                        {'label': 'x' * 20, 'text': '  '},
                                        {'text': '在的'}]}

        content = build(MessyClient()).generate_suggestions('恋爱', '', '在', 'other',
                                                            INTENT, EMOTION)
        assert content.suggestions == [{'label': '建议', 'text': '好呀'}, {'label': '建议', 'text': '在的'}]


class FailingClient:
    def __init__(self, error: Exception | None = None):
        self.error = error or GeneralLLMError('DeepSeek 返回 HTTP 402：Insufficient Balance')

    def complete_json(self, system, user, accept):
        raise self.error


class TestFailure:
    def test_interpretation_failure_is_raised_not_swallowed(self):
        with pytest.raises(GeneralLLMError, match='402'):
            build(FailingClient()).generate_interpretation('恋爱', '', '在', 'other', INTENT, EMOTION)

    def test_suggestions_failure_is_raised_not_swallowed(self):
        with pytest.raises(GeneralLLMError, match='402'):
            build(FailingClient()).generate_suggestions('恋爱', '', '在', 'other', INTENT, EMOTION)

    def test_two_kinds_use_separate_prompts(self):
        """两条链路各自发一次请求，提示词互不相同（不共享同一次调用）。"""
        client = StubClient()
        service = build(client)
        service.generate_interpretation('恋爱', 'ctx', 'msg', 'other', INTENT, EMOTION)
        service.generate_suggestions('恋爱', 'ctx', 'msg', 'other', INTENT, EMOTION)
        assert len(client.calls) == 2
        assert client.calls[0] != client.calls[1]
