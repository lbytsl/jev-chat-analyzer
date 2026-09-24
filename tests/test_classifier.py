"""分类层测试：两级路由、平票退让、调情×硬负面调和、生成失败降级。全部用假 Jev。"""
from __future__ import annotations

import pytest

from app.core.exceptions import InvalidRequest, JevResponseError
from app.domain.labels import HARD_NEGATIVE_FAMILIES, WARM_INTENTS
from app.services.classifier import ClassifierService
from tests.conftest import FakeGeneration, FakeJev, first_emotion, first_intent

RELATIONSHIP = '恋爱'


def build(*, intent=None, family='开心', emotion=None, generation=None, **kwargs) -> tuple[ClassifierService, FakeJev]:
    intent = intent or first_intent(library_of(), RELATIONSHIP)
    emotion = emotion or first_emotion(library_of(), RELATIONSHIP, family)
    jev = FakeJev(intent=intent, family=family, emotion=emotion, **kwargs)
    service = ClassifierService(jev=jev, generation=generation or FakeGeneration(), library=library_of())
    return service, jev


def library_of():
    from app.domain.labels import get_label_library
    return get_label_library()


STATE = {'message': '在吗', 'context': '对方：到家了', 'relationship': RELATIONSHIP, 'speaker': 'other'}


class TestClassify:
    def test_two_stage_route_and_result_shape(self):
        service, jev = build()
        result = service.classify(STATE)

        # 两级路由 = 两次调用：先粗路由与互动维度，再在圈定的大类里细分。
        assert len(jev.calls) == 2
        assert 'intent_family' in jev.calls[0] and 'primary_intent' not in jev.calls[0]
        assert 'emotion_family' in jev.calls[0] and 'emotion' not in jev.calls[0]
        assert set(jev.calls[1]) == {'primary_intent', 'emotion', 'communication_style'}

        assert result['version'] and result['model'] == 'fake-jev'
        assert result['analysis_schema'] == '2.0'
        assert result['answers'].keys() == {'stage1', 'stage2'}
        assert result['primary_intent']['key'] == jev.intent
        assert result['primary_intent']['canonical_key'].startswith('intent.')
        assert result['emotion']['key'] == jev.emotion
        assert result['emotion']['canonical_key'].startswith('emotion.')
        assert result['emotion_family']['key'] == jev.family
        assert result['relation_direction']['label'] == '维持'
        assert result['response_need']['label'] == '不确定'
        assert result['communication_style']['label'] == '普通陈述'
        assert result['usage'].keys() == {'stage1', 'stage2'}
        assert result['gen_skipped'] is True
        assert result['input'] == STATE
        # 排序后的候选供前端展示，分数统一保留 3 位。
        assert all(isinstance(item['score'], float) for item in result['primary_intent']['ranked'])

    def test_coarse_fallback_to_family_display_when_unsure(self):
        service, jev = build(emotion_score=0.2)
        result = service.classify(STATE)
        assert result['emotion']['coarse'] is True
        assert result['emotion']['display'] == library_of().family_display(jev.family, RELATIONSHIP)

    def test_fine_label_used_when_confident(self):
        service, jev = build(emotion_score=0.9)
        result = service.classify(STATE)
        assert result['emotion']['coarse'] is False
        assert result['emotion']['display'] == library_of().display_name(jev.emotion, RELATIONSHIP)

    def test_family_tie_brings_second_family_into_second_stage(self):
        library = library_of()
        families = library.family_order
        first, second = families[0], families[1]
        jev = FakeJev(intent=first_intent(library, RELATIONSHIP), family=first,
                      emotion=first_emotion(library, RELATIONSHIP, first),
                      family_probs={first: 0.4, second: 0.35})
        service = ClassifierService(jev=jev, generation=FakeGeneration(), library=library)
        result = service.classify(STATE)
        assert result['emotion']['families_considered'] == [first, second]
        assert set(jev.calls[1]['emotion']['criteria']) == set(library.emotion_model_criteria(
            RELATIONSHIP, [first, second]))

    def test_intent_family_tie_brings_second_family_into_second_stage(self):
        library = library_of()
        families = library.intent_family_order
        first, second = families[0], families[1]
        intent = next(label for label in library.intent_candidates[RELATIONSHIP]
                      if library.intents[label]['family'] == first)
        jev = FakeJev(intent=intent, intent_family=first, family='开心',
                      emotion=first_emotion(library, RELATIONSHIP, '开心'),
                      intent_family_probs={first: 0.4, second: 0.35})
        service = ClassifierService(jev=jev, generation=FakeGeneration(), library=library)

        service.classify(STATE)

        assert set(jev.calls[1]['primary_intent']['criteria']) == set(
            library.intent_model_criteria(RELATIONSHIP, [first, second]))

    def test_flirty_intent_with_hard_negative_family_switches_to_soft_candidates(self):
        """两层结论不能互相打脸：调情 + 生气 → 第二级换成有怨气/委屈难过。"""
        library = library_of()
        warm_intent = next(label for label in library.intent_candidates[RELATIONSHIP]
                           if label in WARM_INTENTS)
        hard_family = next(f for f in library.family_order if f in HARD_NEGATIVE_FAMILIES)
        jev = FakeJev(intent=warm_intent, family=hard_family, relation_direction='靠近',
                      emotion=first_emotion(library, RELATIONSHIP, hard_family))
        service = ClassifierService(jev=jev, generation=FakeGeneration(), library=library)

        # 第二级只在柔软的两类里选，但返回的标签必须合法：换成柔和大类里的标签。
        soft_family = '有怨气'
        jev.emotion = first_emotion(library, RELATIONSHIP, soft_family)
        result = service.classify(STATE)

        assert result['emotion']['families_considered'] == ['有怨气', '委屈难过']
        assert set(jev.calls[1]['emotion']['criteria']) == set(
            library.emotion_model_criteria(RELATIONSHIP, ['有怨气', '委屈难过']))

    def test_invalid_intent_answer_raises_response_error(self):
        library = library_of()
        jev = FakeJev(intent='不存在的意图', family='开心',
                      emotion=first_emotion(library, RELATIONSHIP, '开心'))
        service = ClassifierService(jev=jev, generation=FakeGeneration(), library=library)
        with pytest.raises(JevResponseError):
            service.classify(STATE)

    def test_invalid_emotion_answer_raises_response_error(self):
        library = library_of()
        jev = FakeJev(intent=first_intent(library, RELATIONSHIP), family='开心',
                      emotion='不存在的情绪')
        service = ClassifierService(jev=jev, generation=FakeGeneration(), library=library)
        with pytest.raises(JevResponseError):
            service.classify(STATE)

    def test_explicit_boundary_cannot_be_reversed_into_approach(self):
        service, _ = build(relation_direction='靠近')
        result = service.classify({**STATE, 'message': '我现在不想说这个，先别聊了'})

        assert result['relation_direction']['label'] == '划界'
        assert 'explicit_boundary_override' in result['uncertainty']['reasons']
        assert result['uncertainty']['level'] == 'high'

    def test_non_boundary_negation_is_not_overridden(self):
        service, _ = build(relation_direction='靠近')
        result = service.classify({**STATE, 'message': '别忘了想我'})

        assert result['relation_direction']['label'] == '靠近'
        assert 'explicit_boundary_override' not in result['uncertainty']['reasons']

    def test_generation_failure_is_downgraded(self):
        """生成层失败只按种类标记，分类结果照常返回。"""
        service, _ = build(generation=FakeGeneration(fail=True))
        result = service.classify(STATE, want_interpretation=True, want_suggestions=True)
        assert result['gen_failed'] is True and result['interpretation_failed'] is True
        assert result['gen_error'], '失败要留下可读原因'
        assert result['suggestions'] is None
        assert result['primary_intent']['key']

    def test_generation_result_is_merged(self):
        service, _ = build()
        result = service.classify(STATE, want_interpretation=True, want_suggestions=True)
        assert result['gen_failed'] is False and result['interpretation_failed'] is False
        assert result['intent_detail'] == '嘴上嫌弃实际在撒娇'
        assert result['suggestions'] == [{'label': '接住', 'text': '好呀'}]
        # v008 起这两个字段只在键名上保留：恒为空，前端不再展示。
        assert result['interpretation'] == '' and result['emotion_detail'] == ''

    def test_interpretation_only_does_not_call_suggestions(self):
        """两条生成互不牵连：只勾潜台词时不产生回复建议（也不再「顺带」带回）。"""
        generation = FakeGeneration()
        service, _ = build(generation=generation)
        result = service.classify(STATE, want_interpretation=True)
        assert result['intent_detail'] == '嘴上嫌弃实际在撒娇'
        assert result['suggestions'] is None and result['gen_failed'] is False
        assert len(generation.interpretation_calls) == 1
        assert generation.suggestions_calls == []

    def test_suggestions_only_does_not_produce_interpretation(self):
        """只勾推荐回复时不再顺带生成潜台词（旧实现是一次调用返回两个字段）。"""
        generation = FakeGeneration()
        service, _ = build(generation=generation)
        result = service.classify(STATE, want_suggestions=True)
        assert result['suggestions'] == [{'label': '接住', 'text': '好呀'}]
        assert result['intent_detail'] is None
        assert generation.interpretation_calls == []
        assert generation.suggestion_needs[0]['label'] == '不确定'

    def test_one_kind_failing_does_not_break_the_other(self):
        """潜台词挂了不影响推荐回复，反之亦然。"""
        service, _ = build(generation=FakeGeneration(fail_interpretation=True))
        result = service.classify(STATE, want_interpretation=True, want_suggestions=True)
        assert result['interpretation_failed'] is True
        assert result['intent_detail'] is None
        assert result['suggestions'] == [{'label': '接住', 'text': '好呀'}]
        assert result['gen_failed'] is False, '潜台词失败不该让推荐回复跟着报失败'


class TestClassifyPayloadValidation:
    def test_relationship_is_required(self):
        service, _ = build()
        with pytest.raises(InvalidRequest, match='请选择关系或场景'):
            service.classify_payload({'message': '在吗', 'context': '', 'relationship': '网友'})

    def test_message_is_required(self):
        service, _ = build()
        with pytest.raises(InvalidRequest, match='聊天内容不能为空'):
            service.classify_payload({'message': '   ', 'context': '', 'relationship': RELATIONSHIP})

    def test_non_string_field_is_rejected(self):
        service, _ = build()
        with pytest.raises(InvalidRequest, match='输入格式不正确'):
            service.classify_payload({'message': 123, 'context': '', 'relationship': RELATIONSHIP})
