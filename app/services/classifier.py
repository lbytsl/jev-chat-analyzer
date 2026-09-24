"""分类用例：一次分析 = 两次 Jev 调用（意图与情绪共用两级路由）。

第一级：意图大类 + 情绪大类 + 关系方向 + 期待回应；第二级：细意图 + 细情绪 + 表达方式。
模型只看中性规范名，旧的产品化名字留在兼容/展示层，不再直接充当 choice key。

一级判错会连坐第二级：前两名咬得紧时把两类一起交给第二级，别让第一级那点犹豫直接
决定最终标签。意图判成调情/撒娇、情绪却落在硬负面时，把第二级候选换成柔软的两类，
避免出现「在调情 + 在发火」这种自相矛盾的组合。
"""
from __future__ import annotations

import time
from typing import Callable

from app.clients.jev import JevClient
from app.core.config import (
    ANALYSIS_SCHEMA,
    CLASSIFICATION_PROMPT_VERSION,
    EMOTION_MIN,
    FAMILY_TIE_GAP,
    INTENT_FAMILY_TIE_GAP,
    LABEL_VERSION,
    MAX_CONTEXT_CHARS,
    MAX_MESSAGE_CHARS,
    TIE_GAP,
    VERSION,
)
from app.core.exceptions import (
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
from app.domain.prompts import build_stage1_questions, build_stage2_questions
from app.services.generation import GenerationService

logger = get_logger('classifier')

INTENT_FAMILY_KEYS = {
    '关系': 'intent.family.relationship',
    '事务': 'intent.family.transaction',
    '冲突': 'intent.family.conflict',
    '中性': 'intent.family.neutral',
}
RELATION_DIRECTION_KEYS = {
    '靠近': 'relation.approach', '维持': 'relation.maintain', '试探': 'relation.probe',
    '拉远': 'relation.distance', '对抗': 'relation.confront', '划界': 'relation.boundary',
    '纯事务': 'relation.transactional', '无明显方向': 'relation.unclear',
}
RESPONSE_NEED_KEYS = {
    '无需回应': 'response.none', '确认即可': 'response.acknowledge',
    '需要信息': 'response.information', '需要行动': 'response.action',
    '需要安抚': 'response.reassurance', '需要解释': 'response.explanation',
    '需要表态': 'response.position', '等待接话': 'response.continue',
    '不确定': 'response.unclear',
}
STYLE_KEYS = {
    '直接表达': 'style.direct', '委婉暗示': 'style.indirect',
    '反问试探': 'style.rhetorical', '玩笑调侃': 'style.playful',
    '解释澄清': 'style.explain', '讽刺表达': 'style.sarcastic',
    '冷淡处理': 'style.cold', '施压催促': 'style.pressure',
    '安抚示好': 'style.reassure', '克制表达': 'style.controlled',
    '掩饰情绪': 'style.masked', '普通陈述': 'style.plain',
    '无法判断': 'style.unclear',
}

# 只收非常明确、低歧义的边界表达。不能扩成「包含不/别就算拒绝」这类宽规则，否则
# “别忘了想我”“不想不理你”也会被误伤。命中后只约束关系方向，不替模型改意图和情绪。
EXPLICIT_BOUNDARY_PHRASES = (
    '别再联系我', '不要再联系我', '到此为止', '我不想继续', '我拒绝',
    '我不接受', '现在不想说', '我不想聊', '先别聊', '先不要聊',
)


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

        result1 = self._jev.decide(state, build_stage1_questions(library, relationship, speaker))
        answers1 = dict(result1.get('answers', {}))
        intent_family_answer = self._require_choice(
            answers1.get('intent_family'), library.intent_family_definitions, '意图大类')
        emotion_family_answer = self._require_choice(
            answers1.get('emotion_family'), library.families, '情绪大类')
        relation_answer = self._require_choice(
            answers1.get('relation_direction'), library.relation_directions, '关系方向')
        response_answer = self._require_choice(
            answers1.get('response_need'), library.response_needs, '期待回应')
        boundary_override = False
        if (relation_answer['choice'] == '靠近'
                and self._has_explicit_boundary(state.get('message', ''))):
            relation_answer = self._override_choice(relation_answer, '划界')
            boundary_override = True

        _, _, intent_families = self._route_families(
            intent_family_answer, library.intent_family_definitions, INTENT_FAMILY_TIE_GAP)
        family_probs, family_ranked, families = self._route_families(
            emotion_family_answer, library.families, FAMILY_TIE_GAP)

        # 粗路由已明确是在靠近，硬负面多半来自带刺字面；沿用旧版的柔和负面调和，且不新增第三次调用。
        if relation_answer['choice'] == '靠近' and emotion_family_answer['choice'] in HARD_NEGATIVE_FAMILIES:
            families = [family for family in families if family not in HARD_NEGATIVE_FAMILIES]
            families += [family for family in SOFT_NEGATIVE_FAMILIES if family not in families]

        intent_candidates = library.intent_candidates_for(relationship, intent_families)
        if not intent_candidates:
            intent_candidates = dict(library.intent_candidates[relationship])
            intent_families = library.intent_family_order
        emotion_candidates = library.emotion_candidates_for(relationship, families)
        if not emotion_candidates:
            emotion_candidates = dict(library.emotion_candidates[relationship])
            families = library.family_order

        result2 = self._jev.decide(state, build_stage2_questions(
            library, relationship, intent_families, families, speaker,
            relation_direction=relation_answer['choice']))
        answers2 = dict(result2.get('answers', {}))
        raw_primary = self._require_choice_shape(answers2.get('primary_intent'), '主要意图')
        primary_label = library.resolve_intent_label(raw_primary['choice'])
        if primary_label not in intent_candidates:
            raise JevResponseError('模型返回了无效主要意图')
        primary = {**raw_primary, 'choice': primary_label,
                   'probabilities': library.normalize_intent_probabilities(
                       raw_primary['probabilities'])}

        raw_emotion = self._require_choice_shape(answers2.get('emotion'), '情绪')
        emotion_label = library.resolve_emotion_label(raw_emotion['choice'])
        if emotion_label not in emotion_candidates:
            raise JevResponseError('模型返回了无效情绪判断')
        emotion_answer = {**raw_emotion, 'choice': emotion_label,
                          'probabilities': library.normalize_emotion_probabilities(
                              raw_emotion['probabilities'])}
        style_answer = self._require_choice(
            answers2.get('communication_style'), library.communication_styles, '表达方式')

        primary_intent = self._build_primary_intent(primary, library, relationship)
        emotion_result, emotion_family = self._build_emotion(
            emotion_answer, emotion_family_answer, family_probs, family_ranked,
            emotion_candidates, families,
            relationship, library)
        intent_family = self._build_dimension(
            intent_family_answer, library.intent_family_definitions, INTENT_FAMILY_KEYS)
        relation_direction = self._build_dimension(
            relation_answer, library.relation_directions, RELATION_DIRECTION_KEYS)
        response_need = self._build_dimension(
            response_answer, library.response_needs, RESPONSE_NEED_KEYS)
        communication_style = self._build_dimension(
            style_answer, library.communication_styles, STYLE_KEYS)
        uncertainty = self._build_uncertainty(
            primary_intent, emotion_result, relation_direction, response_need,
            communication_style)
        if boundary_override:
            uncertainty['reasons'].append('explicit_boundary_override')
            uncertainty['level'] = 'high'
        if (primary_label in WARM_INTENTS
                and emotion_result.get('family') in HARD_NEGATIVE_FAMILIES):
            uncertainty['reasons'].append('cross_dimension_conflict')
            uncertainty['level'] = 'high'
        if primary_label in {'派活了', '求配合'} and response_answer['choice'] == '无需回应':
            uncertainty['reasons'].append('cross_dimension_conflict')
            uncertainty['level'] = 'high'

        # 潜台词与回复建议交给生成层按整段上下文生成（Jev 不会自由生成），两条各自独立调用。
        # 生成失败不影响分类结果：按种类标记，前端如实显示失败、不回退死模板。
        gen = self._run_generation(
            state, relationship, speaker, primary_intent, emotion_result, response_need,
            want_interpretation, want_suggestions, on_generation)

        return {
            'version': VERSION,
            'analysis_schema': library.schema_version or ANALYSIS_SCHEMA,
            'prompt_version': CLASSIFICATION_PROMPT_VERSION,
            'label_version': library.label_version or LABEL_VERSION,
            'model': result1.get('model'),
            'elapsed_ms': round((time.perf_counter() - start) * 1000),
            'answers': {'stage1': answers1, 'stage2': answers2},
            'intent_family': intent_family,
            'primary_intent': primary_intent,
            'emotion': emotion_result,
            'emotion_family': emotion_family,
            'relation_direction': relation_direction,
            'response_need': response_need,
            'communication_style': communication_style,
            'uncertainty': uncertainty,
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

    def _run_generation(self, state: dict, relationship: str, speaker: str,
                        primary_intent: dict, emotion_result: dict, response_need: dict,
                        want_interpretation: bool, want_suggestions: bool,
                        on_generation: Callable[[dict], None] | None = None) -> dict:
        """两条互不牵连的生成调用：各自跑、各自标记失败。

        失败字段名只在这里定义一次（`interpretation_failed` vs `gen_failed`）：混用会让
        「潜台词挂了」显示成「回复建议失败」。try/except 与日志已收进 GenerationService。
        """
        gen = {'interpretation': None, 'intent_detail': None, 'emotion_detail': None,
               'suggestions': None, 'gen_failed': False, 'interpretation_failed': False,
               'gen_error': None}
        context, message = state.get('context', ''), state.get('message', '')
        if want_interpretation:
            outcome = self._generation.run_interpretation(
                relationship, context, message, speaker, primary_intent, emotion_result,
                on_event=on_generation)
            if outcome.ok:
                gen.update(outcome.content.to_dict())
            else:
                gen['interpretation_failed'] = True
                gen['gen_error'] = outcome.error
        if want_suggestions:
            outcome = self._generation.run_suggestions(
                relationship, context, message, speaker, primary_intent, emotion_result,
                on_event=on_generation, response_need_result=response_need)
            if outcome.ok:
                gen['suggestions'] = outcome.content.suggestions
            else:
                gen['gen_failed'] = True
                gen['gen_error'] = outcome.error
        return gen

    # ---------- 组装 ----------
    @staticmethod
    def _require_choice_shape(answer, name: str) -> dict:
        if (not isinstance(answer, dict) or answer.get('type') != 'choice'
                or not isinstance(answer.get('choice'), str)
                or not isinstance(answer.get('probabilities'), dict)):
            raise JevResponseError('模型返回了无效{}判断'.format(name))
        return answer

    @classmethod
    def _require_choice(cls, answer, candidates, name: str) -> dict:
        answer = cls._require_choice_shape(answer, name)
        if answer['choice'] not in candidates:
            raise JevResponseError('模型返回了无效{}判断'.format(name))
        return answer

    @staticmethod
    def _has_explicit_boundary(message: str) -> bool:
        compact = ''.join(str(message).split())
        return any(phrase in compact for phrase in EXPLICIT_BOUNDARY_PHRASES)

    @staticmethod
    def _override_choice(answer: dict, choice: str) -> dict:
        probabilities = dict(answer['probabilities'])
        probabilities[choice] = max(
            float(probabilities.get(choice, 0)),
            float(probabilities.get(answer['choice'], 0)),
        )
        return {**answer, 'choice': choice, 'probabilities': probabilities}

    @staticmethod
    def _route_families(answer: dict, candidates, tie_gap: float):
        probabilities = {label: float(score)
                         for label, score in answer['probabilities'].items()
                         if label in candidates}
        ranked = sorted(probabilities.items(), key=lambda item: -item[1])
        selected = [answer['choice']]
        if len(ranked) > 1 and (ranked[0][1] - ranked[1][1]) < tie_gap:
            if ranked[1][0] not in selected:
                selected.append(ranked[1][0])
        return probabilities, ranked, selected

    @staticmethod
    def _build_dimension(answer: dict, definitions: dict[str, str], key_map: dict[str, str]) -> dict:
        probabilities = {label: float(score)
                         for label, score in answer['probabilities'].items()
                         if label in definitions}
        ranked = sorted(probabilities.items(), key=lambda item: -item[1])[:5]
        label = answer['choice']
        return {
            'key': key_map[label],
            'label': label,
            'display': label,
            'score': round(float(probabilities.get(label, 0)), 3),
            'score_kind': 'choice_probability',
            'confidence': answer.get('confidence'),
            'probabilities': probabilities,
            'ranked': [{'label': item, 'score': round(float(score), 3)}
                       for item, score in ranked],
            'definition': definitions[label],
        }

    @staticmethod
    def _build_uncertainty(primary_intent: dict, emotion: dict, relation_direction: dict,
                           response_need: dict, communication_style: dict) -> dict:
        layers = {
            'primary_intent': primary_intent,
            'emotion': emotion,
            'relation_direction': relation_direction,
            'response_need': response_need,
            'communication_style': communication_style,
        }
        reasons, alternatives, high = [], {}, False
        for name, layer in layers.items():
            ranked = layer.get('ranked') or []
            score = float(layer.get('score') or 0)
            second = float(ranked[1].get('score') or 0) if len(ranked) > 1 else 0.0
            if score < EMOTION_MIN:
                reasons.append(name + '_low_score')
                high = high or name in ('primary_intent', 'emotion')
            if len(ranked) > 1 and score - second < TIE_GAP:
                reasons.append(name + '_top2_close')
                alternatives[name] = ranked[:2]
                high = high or name in ('primary_intent', 'emotion')
        if high:
            level = 'high'
        elif reasons:
            level = 'medium'
        else:
            level = 'low'
        return {'level': level, 'reasons': reasons, 'alternatives': alternatives}

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
            'canonical_key': library.intent_key(key),
            'canonical_label': library.intent_model_label(key),
            'display': library.intent_display_name(key, relationship),
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
            'canonical_key': library.emotion_key(key),
            'canonical_label': library.emotion_model_label(key),
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
        family_key = family_answer['choice']
        emotion_family = {
            'key': family_key,
            'label': family_key,
            'score': round(float(family_probs.get(family_key, 0)), 3),
            'score_kind': 'choice_probability',
            'confidence': family_answer.get('confidence'),
            'probabilities': family_probs,
            'ranked': [{'label': f, 'score': round(float(p), 3)} for f, p in family_ranked[:5]],
            'definition': (library.families.get(family_key) or {}).get('definition'),
        }
        return emotion_result, emotion_family
