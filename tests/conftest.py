"""测试公共夹具：假上游 + 从标签库派生的合法标签。

所有测试都用假客户端，不连网络、不烧额度；真实上游的联调由 `python -m app serve`
并在浏览器里跑一遍来完成。

会话库一律落临时目录：`var/sessions.db` 里有真实聊天原文，测试绝不能往里写。
"""
from __future__ import annotations

import pytest

from app.domain.labels import get_label_library
from app.repositories.session_store import SessionStore
from app.services.generation import (
    GenerationService,
    InterpretationContent,
    SuggestionsContent,
)
from app.services.pipeline import PipelineService
from app.services.review_pool import ReviewPool
from app.services.sessions import SessionService


@pytest.fixture(scope='session')
def library():
    return get_label_library()


@pytest.fixture
def session_service(tmp_path):
    """独立会话库（临时目录），供服务层与接口测试使用。"""
    return SessionService(SessionStore(tmp_path / 'sessions.db'))


@pytest.fixture
def real_pipeline(tmp_path):
    """真流水线 + 假分类/假生成：验证「分析 → 落库 → 补跑」整条链路，但不碰网络。"""
    return PipelineService(classifier=FakeClassifier(), generation=FakeGeneration(),
                           pool=ReviewPool(path=tmp_path / 'pool.json'))


def first_intent(library, relationship: str, *, generic: bool = False) -> str:
    """从该场景的意图候选里挑一个（generic=True 时挑泛化兜底标签，用于回流池断言）。"""
    for label in library.intent_candidates[relationship]:
        if (label in library.generic_intents) is generic:
            return label
    raise AssertionError(f'{relationship} 没有 {"泛化" if generic else "普通"} 意图候选')


def first_emotion(library, relationship: str, family: str) -> str:
    for label, entry in library.emotions.items():
        if entry.get('family') == family and relationship in entry['scenarios']:
            return label
    raise AssertionError(f'{relationship} 在「{family}」下没有情绪候选')


class FakeJev:
    """按 questions 里出现的问题名决定回哪一段，模拟两级路由的两次调用。"""

    def __init__(self, *, intent: str, family: str, emotion: str,
                 intent_score: float = 0.8, family_score: float = 0.8,
                 emotion_score: float = 0.8, family_probs: dict | None = None,
                 model: str = 'fake-jev'):
        self.intent = intent
        self.family = family
        self.emotion = emotion
        self.intent_score = intent_score
        self.family_score = family_score
        self.emotion_score = emotion_score
        self.family_probs = family_probs
        self.model = model
        self.calls: list[dict] = []

    @staticmethod
    def _choice(label: str, probabilities: dict, confidence: float = 0.9) -> dict:
        return {'type': 'choice', 'choice': label, 'probabilities': probabilities,
                'confidence': confidence}

    def decide(self, state, questions):
        self.calls.append(questions)
        answers = {
            'primary_intent': self._choice(self.intent, {self.intent: self.intent_score}),
        }
        if 'emotion_family' in questions:
            probs = self.family_probs or {self.family: self.family_score}
            answers['emotion_family'] = self._choice(self.family, probs)
        if 'emotion' in questions:
            answers['emotion'] = self._choice(self.emotion, {self.emotion: self.emotion_score})
        return {'model': self.model, 'answers': answers, 'usage': {'input_tokens': 10, 'output_tokens': 2}}


class FakeGeneration(GenerationService):
    """生成层替身：只覆盖两个真正打上游的 `generate_*`，其余照真实实现走。

    **继承**（而不是鸭子类型的独立类）是有意的：真实服务上还挂着 `run_interpretation` /
    `run_suggestions` 这类「不抛」入口，替身一旦漏实现，测试就会在 `AttributeError` 里
    绕圈（曾经就是这样），而继承能保证接口变了一处都不用改。

    `fail_interpretation` / `fail_suggestions` 可以分别制造失败，用来验证两边互不牵连。
    """

    def __init__(self, fail: bool = False, fail_interpretation: bool | None = None,
                 fail_suggestions: bool | None = None, detail: str = '嘴上嫌弃实际在撒娇'):
        # 不调 super().__init__()：它会建真实客户端（虽然不发请求），这里用不上。
        self.fail_interpretation = fail if fail_interpretation is None else fail_interpretation
        self.fail_suggestions = fail if fail_suggestions is None else fail_suggestions
        self.detail = detail
        self.calls: list[tuple] = []
        self.interpretation_calls: list[tuple] = []
        self.suggestions_calls: list[tuple] = []

    def _record(self, kind: str, relationship, message, speaker):
        self.calls.append((kind, relationship, message, speaker))

    def generate_interpretation(self, relationship, context, message, speaker, intent_result,
                                emotion_result, on_event=None):
        self._record('interpretation', relationship, message, speaker)
        self.interpretation_calls.append((relationship, message, speaker))
        if self.fail_interpretation:
            from app.core.exceptions import GeneralLLMError
            raise GeneralLLMError('假潜台词失败')
        if on_event is not None:
            # 真实实现是一边收一边推；替身按契约推两片——text 是**增量**，接起来才等于完整值。
            on_event({'type': 'delta', 'kind': 'interpretation', 'text': self.detail[:4]})
            on_event({'type': 'delta', 'kind': 'interpretation', 'text': self.detail[4:]})
        return InterpretationContent(interpretation='', intent_detail=self.detail, emotion_detail='')

    def generate_suggestions(self, relationship, context, message, speaker, intent_result,
                             emotion_result, on_event=None):
        self._record('suggestions', relationship, message, speaker)
        self.suggestions_calls.append((relationship, message, speaker))
        if self.fail_suggestions:
            from app.core.exceptions import GeneralLLMError
            raise GeneralLLMError('假推荐回复失败')
        if on_event is not None:
            on_event({'type': 'item', 'kind': 'suggestions',
                      'suggestion': {'label': '接住', 'text': '好呀'}})
        return SuggestionsContent(suggestions=[{'label': '接住', 'text': '好呀'}])


class FakeClassifier:
    """分类层替身：只回固定结构，用来验证流水线的编排（不碰标签库判定逻辑）。

    `fail_times` 制造断连（JevConnectionError，会被补跑）；`error` 制造业务错误
    （JevAPIError 之类，不补跑、必须原地冒泡），两者都只作用于前若干次调用。
    """

    def __init__(self, label: str = '陈述事实', emotion: str = '无情绪', fail_times: int = 0,
                 error: Exception | None = None):
        self.label = label
        self.emotion = emotion
        self.fail_times = fail_times
        self.error = error
        self.calls: list[dict] = []

    def _canned(self, want_interpretation: bool, want_suggestions: bool) -> dict:
        skipped = not (want_interpretation or want_suggestions)
        return {
            'version': 'v008',
            'model': 'fake-jev',
            'elapsed_ms': 1,
            'primary_intent': {'key': self.label, 'label': self.label, 'score': 0.9,
                               'ranked': [{'label': self.label, 'score': 0.9},
                                          {'label': '其他', 'score': 0.05}]},
            'emotion': {'key': self.emotion, 'label': self.emotion, 'score': 0.9,
                        'ranked': [{'label': self.emotion, 'score': 0.9},
                                   {'label': '其他', 'score': 0.05}]},
            # 真实分类结果一定带这几个字段（前端靠 gen_skipped 判断要不要显示潜台词），
            # 假结果也补齐，否则流水线/接口测试会踩到「真实响应里有、假响应里没有」的坑。
            'gen_skipped': skipped,
            'gen_failed': False,
            'interpretation_failed': False,
            'intent_detail': '嘴上嫌弃实际在撒娇' if want_interpretation else None,
            'suggestions': [{'label': '接住', 'text': '好呀'}] if want_suggestions else None,
            'answers': {},
            'usage': {},
            'input': {},
        }

    def classify(self, state, want_interpretation=False, want_suggestions=False,
                 on_generation=None):
        self.calls.append({'state': state, 'want_interpretation': want_interpretation,
                           'want_suggestions': want_suggestions})
        if self.fail_times > 0:
            from app.core.exceptions import JevConnectionError
            self.fail_times -= 1
            raise JevConnectionError('假的连接失败')
        if self.error is not None:
            raise self.error
        if on_generation is not None:
            # 真实实现里这两个事件来自生成层边收边解析；替身直接照契约推一次。
            if want_interpretation:
                on_generation({'type': 'delta', 'kind': 'interpretation', 'text': '嘴上嫌弃实际在撒娇'})
            if want_suggestions:
                on_generation({'type': 'item', 'kind': 'suggestions',
                               'suggestion': {'label': '接住', 'text': '好呀'}})
        return self._canned(want_interpretation, want_suggestions)
