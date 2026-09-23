"""分类用例：一次分析 = 两次 Jev 调用（两级路由）。

第一级：意图 + 情绪大类（一次调用）。情绪不再一次性铺 28 个候选——候选越多分数越摊、
平票越多；先收拢到 8 个大类，第二级再在类内 ≤6 个里细分。

一级判错会连坐第二级：前两名咬得紧时把两类一起交给第二级，别让第一级那点犹豫直接
决定最终标签。意图判成调情/撒娇、情绪却落在硬负面时，把第二级候选换成柔软的两类，
避免出现「在调情 + 在发火」这种自相矛盾的组合。
"""
from __future__ import annotations

import time
from typing import Callable

from app.clients.jev import JevClient
from app.core.config import (
    EMOTION_MIN,
    FAMILY_TIE_GAP,
    MAX_CONTEXT_CHARS,
    MAX_MESSAGE_CHARS,
    TIE_GAP,
    VERSION,
)
from app.core.exceptions import (
    GeneralLLMError,
    InvalidRequest,
    JevResponseError,
)
from app.core.logging import get_logger
from app.domain.labels import (
    HARD_NEGATIVE_FAMILIES,
    RELATIONSHIPS,
    SOFT_NEGATIVE_FAMILIES,
    WARM_INTENTS,
    LabelLibrary,
    get_label_library,
)
from app.domain.prompts import build_classification_questions
from app.services.generation import GenerationService

logger = get_logger('gen')


class ClassifierService:
    def __init__(self, jev: JevClient | None = None,
                 generation: GenerationService | None = None,
                 library: LabelLibrary | None = None):
        self._jev = jev or JevClient()
        self._generation = generation or GenerationService()
        self._library = library or get_label_library()

    # ---------- 入口 ----------
    def classify_payload(self, data: dict, want_interpretation: bool = False,
                         want_suggestions: bool = False) -> dict:
        """单条消息入口（原 `evaluate`）：校验入参后分类。

        默认不跑生成层：潜台词与推荐回复各自有独立端点，单条分类接口只负责意图 + 情绪。
        """
        if not isinstance(data, dict):
            raise InvalidRequest('请输入聊天内容。')
        for key in ('message', 'context', 'relationship'):
            if not isinstance(data.get(key, ''), str):
                raise InvalidRequest('输入格式不正确。')
        message = data.get('message', '')
        context = data.get('context', '')
        if not message.strip() or len(message) > MAX_MESSAGE_CHARS or len(context) > MAX_CONTEXT_CHARS:
            raise InvalidRequest('聊天内容不能为空，且需控制在 {} 字以内；背景不超过 {} 字。'
                                 .format(MAX_MESSAGE_CHARS, MAX_CONTEXT_CHARS))
        if data.get('relationship') not in RELATIONSHIPS:
            raise InvalidRequest('请选择关系或场景。')
        state = {key: data.get(key, '').strip() for key in ('message', 'context', 'relationship')}
        return self.classify(state, want_interpretation=want_interpretation,
                             want_suggestions=want_suggestions)

    def classify(self, state: dict, want_interpretation: bool = False,
                 want_suggestions: bool = False,
                 on_generation: Callable[[dict], None] | None = None) -> dict:
        """对一条消息做意图 + 情绪判定（原 `evaluate_state`），可选地顺带跑生成层。

        两个开关独立：潜台词与推荐回复是两次调用、两个提示词，各自失败各自标记。
        `on_generation` 只影响生成层的取数方式（流式预览），不影响判定结果。
        """
        library = self._library
        relationship = state.get('relationship')
        if relationship not in library.intent_candidates:
            raise InvalidRequest('请选择关系或场景。')
        speaker = state.get('speaker', 'other')
        start = time.perf_counter()

        result1 = self._jev.decide(state, build_classification_questions(library, relationship, speaker))
        raw_answers = dict(result1.get('answers', {}))
        primary = raw_answers.get('primary_intent', {})
        family_answer = raw_answers.get('emotion_family', {})
        if (primary.get('type') != 'choice' or primary.get('choice') not in library.intent_candidates[relationship]
                or not isinstance(primary.get('probabilities'), dict)):
            raise JevResponseError('模型返回了无效主要意图')
        if (family_answer.get('type') != 'choice' or family_answer.get('choice') not in library.families
                or not isinstance(family_answer.get('probabilities'), dict)):
            raise JevResponseError('模型返回了无效情绪大类判断')

        family_probs = {f: float(p) for f, p in family_answer['probabilities'].items()
                        if f in library.families}
        family_ranked = sorted(family_probs.items(), key=lambda x: -x[1])
        families = [family_answer['choice']]
        if len(family_ranked) > 1 and (family_ranked[0][1] - family_ranked[1][1]) < FAMILY_TIE_GAP:
            families.append(family_ranked[1][0])
        # 判成调情/撒娇、情绪却判到「生气」「冷淡抽离」时，两层结论互相打脸：换到柔软的两类再细分。
        if primary['choice'] in WARM_INTENTS and family_answer['choice'] in HARD_NEGATIVE_FAMILIES:
            families = [f for f in families if f not in HARD_NEGATIVE_FAMILIES] + list(SOFT_NEGATIVE_FAMILIES)

        candidates = library.emotion_candidates_for(relationship, families)
        if not candidates:
            # 选中的大类在该场景没有标签（例如职场里没有「心动」），退回全量，避免第二级无候选可判。
            candidates = dict(library.emotion_candidates[relationship])
            families = library.family_order

        # 第二级：只在上面圈定的大类里判细分（第二次调用）。
        result2 = self._jev.decide(state, build_classification_questions(
            library, relationship, speaker, emotion_families=families, primary_intent=primary['choice']))
        raw_answers.update(result2.get('answers', {}))
        emotion_answer = result2.get('answers', {}).get('emotion', {})
        if (emotion_answer.get('type') != 'choice' or emotion_answer.get('choice') not in candidates
                or not isinstance(emotion_answer.get('probabilities'), dict)):
            raise JevResponseError('模型返回了无效情绪判断')

        primary_intent = self._build_primary_intent(primary, library, relationship)
        emotion_result, emotion_family = self._build_emotion(
            emotion_answer, family_answer, family_probs, family_ranked, candidates, families,
            relationship, library)

        # 潜台词与回复建议交给生成层按整段上下文生成（Jev 不会自由生成），两条各自独立调用。
        # 生成失败不影响分类结果：按种类标记，前端如实显示失败、不回退死模板。
        gen = self._run_generation(state, relationship, speaker, primary_intent, emotion_result,
                                   want_interpretation, want_suggestions, on_generation)

        return {
            'version': VERSION,
            'model': result1.get('model'),
            'elapsed_ms': round((time.perf_counter() - start) * 1000),
            'answers': raw_answers,
            'primary_intent': primary_intent,
            'emotion': emotion_result,
            'emotion_family': emotion_family,
            'interpretation': gen.get('interpretation'),
            'intent_detail': gen.get('intent_detail'),
            # 情绪的真实说法优先取生成层；分类层的「没情绪」是兜底桶，不是真实判断。
            'emotion_detail': gen.get('emotion_detail'),
            'suggestions': gen.get('suggestions'),
            # gen_failed 专指「推荐回复」失败（底部回复面板据此显示失败）；
            # 潜台词失败单独用 interpretation_failed 标记，避免潜台词挂了却提示回复建议失败。
            'gen_failed': bool(gen.get('gen_failed')),
            'interpretation_failed': bool(gen.get('interpretation_failed')),
            'gen_error': gen.get('gen_error'),
            'gen_skipped': not (want_interpretation or want_suggestions),
            # 两级路由是两次调用，usage 按调用顺序都留着，方便对齐额度和延迟。
            'usage': {'stage1': result1.get('usage', {}), 'stage2': result2.get('usage', {})},
            'input': state,
        }

    def _run_generation(self, state, relationship, speaker, primary_intent, emotion_result,
                        want_interpretation: bool, want_suggestions: bool,
                        on_generation: Callable[[dict], None] | None = None) -> dict:
        """两条互不牵连的生成调用：各自 try，各自标记失败。"""
        gen = {'interpretation': None, 'intent_detail': None, 'emotion_detail': None,
               'suggestions': None, 'gen_failed': False, 'interpretation_failed': False,
               'gen_error': None}
        context, message = state.get('context', ''), state.get('message', '')
        if want_interpretation:
            try:
                content = self._generation.generate_interpretation(
                    relationship, context, message, speaker, primary_intent, emotion_result,
                    on_event=on_generation)
                gen.update(content.to_dict())
            except GeneralLLMError as exc:
                logger.warning('潜台词生成失败：%s', exc)
                gen['interpretation_failed'] = True
                gen['gen_error'] = str(exc)
        if want_suggestions:
            try:
                content = self._generation.generate_suggestions(
                    relationship, context, message, speaker, primary_intent, emotion_result,
                    on_event=on_generation)
                gen['suggestions'] = content.suggestions
            except GeneralLLMError as exc:
                logger.warning('推荐回复生成失败：%s', exc)
                gen['gen_failed'] = True
                gen['gen_error'] = str(exc)
        return gen

    # ---------- 组装 ----------
    @staticmethod
    def _build_primary_intent(primary: dict, library: LabelLibrary, relationship: str) -> dict:
        intent_probs = primary['probabilities']
        ranked = sorted(((label, prob) for label, prob in intent_probs.items()
                         if label in library.intent_candidates[relationship]),
                        key=lambda x: -x[1])[:5]
        key = primary['choice']
        return {
            'key': key,
            'label': key,
            'score': round(float(intent_probs.get(key, 0)), 3),
            'score_kind': 'choice_probability',
            'confidence': primary.get('confidence'),
            'probabilities': intent_probs,
            'ranked': [{'label': label, 'score': round(float(prob), 3)} for label, prob in ranked],
            'definition': library.intent_definition(key),
        }

    @staticmethod
    def _build_emotion(emotion_answer, family_answer, family_probs, family_ranked, candidates,
                       families, relationship, library: LabelLibrary):
        emotion_probs = emotion_answer['probabilities']
        ranked = sorted(((label, prob) for label, prob in emotion_probs.items() if label in candidates),
                        key=lambda x: -x[1])[:5]
        key = emotion_answer['choice']
        score = round(float(emotion_probs.get(key, 0)), 3)
        second = round(float(ranked[1][1]), 3) if len(ranked) > 1 else 0.0
        family = library.emotions[key].get('family') or families[0]
        # 低置信度就退到一级大类展示：「他现在是不爽这一类，细分我没把握」比硬憋一个二级标签诚实。
        coarse = score < EMOTION_MIN or (score - second) < TIE_GAP
        emotion_result = {
            'key': key,
            'label': key,
            'display': library.family_display(family, relationship) if coarse
                       else library.display_name(key, relationship),
            'family': family,
            'family_score': round(float(family_probs.get(family, 0)), 3),
            'families_considered': families,
            'coarse': coarse,
            'score': score,
            'score_kind': 'choice_probability',
            'confidence': emotion_answer.get('confidence'),
            'probabilities': emotion_probs,
            'ranked': [{'label': label, 'score': round(float(prob), 3)} for label, prob in ranked],
            'definition': library.emotion_definition(key, relationship),
        }
        emotion_family = {
            'key': families[0],
            'label': families[0],
            'score': round(float(family_probs.get(families[0], 0)), 3),
            'score_kind': 'choice_probability',
            'confidence': family_answer.get('confidence'),
            'probabilities': family_probs,
            'ranked': [{'label': f, 'score': round(float(p), 3)} for f, p in family_ranked[:5]],
            'definition': (library.families.get(families[0]) or {}).get('definition'),
        }
        return emotion_result, emotion_family
