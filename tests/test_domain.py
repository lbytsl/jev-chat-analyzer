"""领域层测试：聊天记录解析、标签库、提示词结构。全部离线，不连上游。"""
from __future__ import annotations

from app.domain.labels import RELATIONSHIPS
from app.domain.prompts import (
    build_classification_questions,
    build_interpretation_messages,
    build_stage1_questions,
    build_stage2_questions,
    build_suggestions_messages,
)
from app.domain.transcript import parse_transcript

WECHAT_STYLE = '\n'.join([
    '尹少华',
    '2026年09月20日 15:51',
    '吃饭了吗',
    '我',
    '2026年09月20日 15:52',
    '吃了，跟同事',
])


class TestParseTranscript:
    def test_label_style(self):
        messages, speakers, me_label = parse_transcript('我：今晚一起吃饭吗？\n林潇：行，几点？')
        assert me_label == '我'
        assert [(m['index'], m['label'], m['speaker'], m['text']) for m in messages] == [
            (1, '我', 'me', '今晚一起吃饭吗？'),
            (2, '林潇', 'other', '行，几点？'),
        ]
        assert [(s['label'], s['role'], s['count']) for s in speakers] == [('我', 'me', 1), ('林潇', 'other', 1)]

    def test_wechat_style_keeps_timestamp(self):
        messages, speakers, me_label = parse_transcript(WECHAT_STYLE)
        assert me_label == '我'
        assert [m['text'] for m in messages] == ['吃饭了吗', '吃了，跟同事']
        assert messages[0]['timestamp'] == '2026年09月20日 15:51'
        assert [s['role'] for s in speakers] == ['other', 'me']

    def test_mixed_style(self):
        text = '林潇：在忙吗\n' + WECHAT_STYLE
        messages, _, _ = parse_transcript(text)
        assert [m['text'] for m in messages] == ['在忙吗', '吃饭了吗', '吃了，跟同事']

    def test_without_speaker_marker_returns_empty(self):
        """整段没有任何说话人标记时不伪造消息，交给调用方提示用户补标记。"""
        messages, speakers, me_label = parse_transcript('吃饭了吗\n吃了\n')
        assert messages == [] and speakers == [] and me_label is None

    def test_pronoun_only_transcript_has_no_me(self):
        messages, speakers, me_label = parse_transcript('她：在吗\n对方：在')
        assert me_label is None
        assert all(m['speaker'] == 'other' for m in messages)

    def test_title_lines_are_ignored(self):
        messages, _, _ = parse_transcript('## 聊天记录\n我：在吗\n她：在')
        assert [m['text'] for m in messages] == ['在吗', '在']

    def test_blank_messages_are_dropped(self):
        messages, _, _ = parse_transcript('我：\n她：在')
        assert [m['text'] for m in messages] == ['在']
        assert messages[0]['index'] == 1


class TestLabelLibrary:
    def test_candidates_are_scoped_by_relationship(self, library):
        for relationship in RELATIONSHIPS:
            for label in library.intent_candidates[relationship]:
                assert relationship in library.intents[label]['scenarios']
            for label in library.emotion_candidates[relationship]:
                assert relationship in library.emotions[label]['scenarios']

    def test_second_level_always_keeps_neutral_fallback(self, library):
        """第二级被锁进某个大类时，仍要并入该场景的中性兜底，避免硬挑一个沾边的情绪。"""
        relationship = '恋爱'
        candidates = library.emotion_candidates_for(relationship, ['开心'])
        neutral = [label for label, entry in library.emotions.items()
                   if entry.get('family') == '平静中性' and relationship in entry['scenarios']]
        assert neutral and all(label in candidates for label in neutral)

    def test_family_display_is_relationship_specific(self, library):
        assert library.family_display('平静中性', '暧昧') == '看不出情绪'
        assert library.family_display('平静中性', '同事') == '就事论事'
        assert library.family_display('开心', '同事') == '开心'

    def test_display_name_prefers_scenario_variant(self, library):
        for label, entry in library.emotions.items():
            variants = entry.get('display_names') or {}
            assert library.display_name(label, '恋爱') == variants.get('恋爱', label)

    def test_generic_sets_are_not_empty(self, library):
        assert library.generic_intents, '中性意图兜底集合为空，回流池会失效'
        assert library.generic_emotions, '情绪兜底桶为空，回流池会失效'

    def test_emotion_definition_prefers_scenario_variant(self, library):
        for label, entry in library.emotions.items():
            variants = entry.get('definitions') or {}
            for relationship, definition in variants.items():
                assert library.emotion_definition(label, relationship) == definition

    def test_taxonomy_round_trips_model_labels(self, library):
        for legacy in library.intents:
            assert library.resolve_intent_label(library.intent_model_label(legacy)) == legacy
            assert library.resolve_intent_label(library.intent_key(legacy)) == legacy
            assert library.intent_key(legacy).startswith('intent.')
        for legacy in library.emotions:
            assert library.resolve_emotion_label(library.emotion_model_label(legacy)) == legacy
            assert library.resolve_emotion_label(library.emotion_key(legacy)) == legacy
            assert library.emotion_key(legacy).startswith('emotion.')



PLACEHOLDERS = ('{relationship}', '{count}', '{speaker_role}', '{context}', '{message}',
                '{intent_label}', '{intent_def}', '{intent_score}', '{emotion_label}',
                '{emotion_def}', '{emotion_score}')


def assert_no_placeholders(*texts):
    """提示词里不该留下没被替换的占位符（JSON 示例里的大括号是正常的）。"""
    for text in texts:
        for token in PLACEHOLDERS:
            assert token not in text, token


class TestPrompts:
    def test_first_level_asks_coarse_routes_and_dimensions(self, library):
        questions = build_stage1_questions(library, '恋爱')
        assert set(questions) == {
            'intent_family', 'emotion_family', 'relation_direction', 'response_need'}
        assert questions['intent_family']['type'] == 'choice'
        assert set(questions['emotion_family']['criteria']) == set(library.family_order)

    def test_intent_criteria_carry_family_prefix(self, library):
        questions = build_stage2_questions(
            library, '恋爱', ['关系'], ['开心'])
        for label, criteria in questions['primary_intent']['criteria'].items():
            legacy = library.resolve_intent_label(label)
            assert criteria.startswith('[' + library.intents[legacy]['family'] + ']')

    def test_boundary_notes_are_appended_when_label_exists(self, library):
        questions = build_stage2_questions(
            library, '恋爱', library.intent_family_order, ['开心'])
        criteria = questions['primary_intent']['criteria']
        ordinary = library.intent_model_label('接住话了')
        assert '吃了，跟同事' in criteria[ordinary]

    def test_second_level_only_offers_selected_families(self, library):
        families = ['开心', '心动']
        questions = build_stage2_questions(library, '恋爱', ['关系'], families)
        assert set(questions) == {'primary_intent', 'emotion', 'communication_style'}
        allowed = set(library.emotion_model_criteria('恋爱', families))
        assert set(questions['emotion']['criteria']) <= allowed
        assert '开心' in questions['emotion']['instructions']
        intent_allowed = set(library.intent_model_criteria('恋爱', ['关系']))
        assert set(questions['primary_intent']['criteria']) == intent_allowed

    def test_warm_hint_added_for_approaching_direction(self, library):
        questions = build_stage2_questions(
            library, '恋爱', ['关系'], ['生气'], relation_direction='靠近')
        assert '不要仅凭带刺字面判成真发火' in questions['emotion']['instructions']

    def test_stage_premise_is_relationship_specific(self, library):
        for relationship in RELATIONSHIPS:
            questions = build_classification_questions(library, relationship)
            assert '不构成本条消息的证据' in questions['emotion_family']['instructions']

    def test_interpretation_messages_keep_placeholders_filled(self):
        system, user = build_interpretation_messages(
            '恋爱', '我：在吗', '在的', 'other',
            {'label': '接住话了', 'definition': '兜底', 'score': 0.5},
            {'label': '无情绪', 'definition': '中性', 'score': 0.6})
        assert_no_placeholders(system, user)
        assert '恋爱' in system and '恋爱' in user
        assert '（无前文）' not in user
        assert '接住话了' in user
        # 只问潜台词，不提建议
        assert 'intent_detail' in system and 'suggestions' not in system
        # 空串只能留给纯事务性应答：模型曾把「嗯嗯是的」这类回应句也判空，界面上就会「什么都没生成」
        assert '留空是极少数例外' in system and '只要这句话是在回应对方' in system

    def test_suggestions_messages_keep_placeholders_filled(self):
        system, user = build_suggestions_messages(
            '恋爱', '我：在吗', '在的', 'other',
            {'label': '接住话了', 'definition': '兜底', 'score': 0.5},
            {'label': '无情绪', 'definition': '中性', 'score': 0.6}, count=4,
            response_need_result={'label': '需要解释', 'definition': '期待说明原因'})
        assert_no_placeholders(system, user)
        assert '恋爱' in system and '恋爱' in user
        assert '（无前文）' not in user
        assert '接住话了' in user
        assert '期待回应：需要解释（期待说明原因）' in user
        # 只问建议，不提潜台词；条数来自配置
        assert '4 条 suggestions' in user
        assert '数组长度必须是 4' in system
        assert 'intent_detail' not in system
