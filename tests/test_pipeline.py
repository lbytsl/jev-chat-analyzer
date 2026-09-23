"""流水线测试：解读对象圈定、复用旧结果、生成层开关、回流池落盘。全部用假上游。"""
from __future__ import annotations

import json

import pytest

from app.core.exceptions import InvalidRequest, JevConnectionError
from app.services.pipeline import PipelineService
from app.services.review_pool import ReviewPool
from tests.conftest import FakeClassifier, FakeGeneration

TRANSCRIPT = '我：在忙吗\n她：刚开完会\n我：那晚点说'


def build(tmp_path, classifier=None, generation=None, **kwargs):
    classifier = classifier or FakeClassifier(**kwargs)
    pool = ReviewPool(path=tmp_path / 'pool.json')
    service = PipelineService(classifier=classifier, generation=generation or FakeGeneration(), pool=pool)
    return service, classifier, pool


class TestAnalyze:
    def test_default_targets_are_the_other_side(self, tmp_path):
        service, classifier, _ = build(tmp_path)
        result = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})

        assert result['count'] == 1 and result['count_other'] == 1 and result['count_me'] == 0
        # 解读对象只有「她」；globals 最后一条（我发的）只为了 reply_target 顺带跑一次。
        assert classifier.calls[0]['state']['message'] == '刚开完会'
        assert [m['index'] for m in result['messages']] == [1, 2, 3]
        assert result['read_labels'] == ['她'] and result['me_label'] == '我'
        assert result['other_labels'] == ['她']
        assert result['failed_count'] == 0 and result['failed_indexes'] == []

    def test_reply_target_anchors_global_last_message(self, tmp_path):
        """默认只解读对方，但推荐回复必须锚定全局最后一条（可能是「我」发的）。"""
        service, _, _ = build(tmp_path)
        result = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})
        assert result['reply_target']['index'] == 3
        assert result['reply_target']['message'] == '那晚点说'

    def test_read_labels_can_include_me(self, tmp_path):
        service, classifier, _ = build(tmp_path)
        result = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱',
                                  'read_labels': ['我']})
        assert result['count_me'] == 2 and result['count_other'] == 0
        assert result['include_me'] is True

    def test_include_me_legacy_flag(self, tmp_path):
        service, _, _ = build(tmp_path)
        result = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱', 'include_me': True})
        assert sorted(result['read_labels']) == ['她', '我']

    def test_generation_flag_only_applies_to_last_target(self, tmp_path):
        service, classifier, _ = build(tmp_path)
        service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱',
                         'read_labels': ['我', '她'], 'gen_suggestions': True})
        by_message = {(c['state']['message'], c['want_suggestions']): c['want_interpretation']
                      for c in classifier.calls}
        # 只勾「推荐回复」：最后一条跑建议、不跑潜台词；其余消息两个都不跑。
        assert by_message[('那晚点说', True)] is False, '最后一条只跑推荐回复'
        assert all(want_interp is False for want_interp in by_message.values())
        assert not any(c['want_suggestions'] for c in classifier.calls
                       if c['state']['message'] != '那晚点说')

    def test_both_generation_kinds_are_requested_independently(self, tmp_path):
        service, classifier, _ = build(tmp_path)
        result = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱',
                                  'read_labels': ['我', '她'], 'gen_interpretation': True,
                                  'gen_suggestions': True})
        by_message = {c['state']['message']: c for c in classifier.calls}
        assert by_message['在忙吗']['want_interpretation'] is True
        assert by_message['在忙吗']['want_suggestions'] is False
        assert by_message['那晚点说']['want_interpretation'] is True
        assert by_message['那晚点说']['want_suggestions'] is True
        assert result['gen_interpretation'] is True and result['gen_suggestions'] is True

    def test_low_confidence_generic_labels_go_to_review_pool(self, tmp_path):
        service, _, pool = build(tmp_path, label='陈述事实', emotion='无情绪')
        service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})
        entries = json.loads(pool.path.read_text(encoding='utf-8'))['entries']
        # 解读对象 + 为 reply_target 顺带跑的那条都会进池：命中泛化意图与情绪兜底桶 → trigger=both。
        assert {item['message'] for item in entries} == {'刚开完会', '那晚点说'}
        assert all(item['trigger'] == 'both' and item['status'] == 'pending' for item in entries)
        assert all(item['hits'] == 1 and item['reasons'] for item in entries)

    def test_connection_failure_after_retry_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr('app.services.pipeline.time.sleep', lambda *_: None)
        service, _, _ = build(tmp_path, classifier=FakeClassifier(fail_times=99))
        with pytest.raises(JevConnectionError, match='连接不上'):
            service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})

    def test_validation_errors(self, tmp_path):
        service, _, _ = build(tmp_path)
        with pytest.raises(InvalidRequest, match='请粘贴聊天记录'):
            service.analyze({'relationship': '恋爱'})
        with pytest.raises(InvalidRequest, match='请选择关系或场景'):
            service.analyze({'transcript': TRANSCRIPT, 'relationship': '网友'})
        with pytest.raises(InvalidRequest, match='没有识别到说话人'):
            service.analyze({'transcript': '没有标记的一段话', 'relationship': '恋爱'})
        with pytest.raises(InvalidRequest, match='不在这段记录里'):
            service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱', 'read_labels': ['张三']})

    def test_too_many_targets(self, tmp_path):
        service, _, _ = build(tmp_path)
        transcript = '\n'.join('她：消息{}'.format(i) for i in range(51))
        with pytest.raises(InvalidRequest, match='最多分析'):
            service.analyze({'transcript': transcript, 'relationship': '恋爱'})


class TestAppend:
    def test_old_messages_are_reused_not_reclassified(self, tmp_path):
        service, classifier, _ = build(tmp_path)
        first = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})
        calls_after_first = len(classifier.calls)

        extended = TRANSCRIPT + '\n她：那明天聊'
        second = service.append({'transcript': extended, 'relationship': '恋爱', 'prev': first,
                                 'old_count': len(first['messages'])})

        assert [c['state']['message'] for c in classifier.calls[calls_after_first:]] == ['那明天聊']
        assert second['analyses'][0] is first['analyses'][0], '旧结果必须原样复用（含已生成的 AI 内容）'
        assert second['count'] == 2 and second['messages'][-1]['index'] == 4
        assert second['reply_target']['index'] == 4

    def test_prev_is_required(self, tmp_path):
        service, _, _ = build(tmp_path)
        with pytest.raises(InvalidRequest, match='缺少上一次分析结果'):
            service.append({'transcript': TRANSCRIPT, 'relationship': '恋爱'})


class TestInterpret:
    """补跑潜台词：默认覆盖全部已有分析，只产出 intent_detail。"""

    def test_prev_is_required(self, tmp_path):
        service, _, _ = build(tmp_path)
        with pytest.raises(InvalidRequest, match='缺少上一次分析结果'):
            service.interpret({'prev': {'relationship': '恋爱', 'analyses': []}})

    def test_generates_for_every_analysis(self, tmp_path):
        service, _, _ = build(tmp_path)
        analyzed = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})
        generation = FakeGeneration()
        service._generation = generation

        result = service.interpret({'prev': analyzed})
        assert result['kind'] == 'interpretation'
        assert set(result['augmentations']) == {str(a['index']) for a in analyzed['analyses']}
        augmentation = result['augmentations'][str(analyzed['analyses'][0]['index'])]
        assert augmentation['intent_detail'] == '嘴上嫌弃实际在撒娇'
        # 只带潜台词字段：不带 suggestions，前端按「字段是否存在」合并就不会误清另一类
        assert 'suggestions' not in augmentation
        assert augmentation['gen_failed'] is False
        assert result['failed_indexes'] == []
        assert generation.suggestions_calls == []

    def test_failure_is_marked_per_message(self, tmp_path):
        service, _, _ = build(tmp_path)
        analyzed = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})
        service._generation = FakeGeneration(fail_interpretation=True)

        result = service.interpret({'prev': analyzed})
        assert result['failed_indexes'] == [a['index'] for a in analyzed['analyses']]
        assert all(item['gen_failed'] for item in result['augmentations'].values())
        assert all('intent_detail' not in item for item in result['augmentations'].values())


class TestSuggest:
    """补跑推荐回复：默认只跑全局最后一条，可指定单条。"""

    def test_defaults_to_global_last_message(self, tmp_path):
        service, _, _ = build(tmp_path)
        analyzed = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})
        generation = FakeGeneration()
        service._generation = generation

        result = service.suggest({'prev': analyzed})
        last_index = analyzed['messages'][-1]['index']
        assert result['kind'] == 'suggestions'
        assert list(result['augmentations']) == [str(last_index)]
        assert generation.interpretation_calls == []

    def test_indexes_target_any_message(self, tmp_path):
        """指定序号后不再受「只跑最后一条」限制：给中间那条单独生成回复建议。"""
        service, _, _ = build(tmp_path)
        analyzed = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱',
                                    'read_labels': ['我', '她']})
        service._generation = FakeGeneration()

        first = analyzed['analyses'][0]['index']
        result = service.suggest({'prev': analyzed, 'indexes': [first]})
        assert list(result['augmentations']) == [str(first)]
        assert result['augmentations'][str(first)]['suggestions'] == [{'label': '接住', 'text': '好呀'}]

    def test_empty_indexes_is_rejected(self, tmp_path):
        service, _, _ = build(tmp_path)
        analyzed = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})
        with pytest.raises(InvalidRequest, match='序号'):
            service.suggest({'prev': analyzed, 'indexes': []})

    def test_last_message_outside_analyses_still_gets_suggestions(self, tmp_path):
        """只解读对方时，全局最后一条（我发的）不在 analyses 里，仍要能给它生成建议。"""
        service, _, _ = build(tmp_path)
        analyzed = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱'})
        assert analyzed['read_labels'] == ['她']
        assert analyzed['analyses'][0]['index'] == 2
        service._generation = FakeGeneration()

        result = service.suggest({'prev': analyzed})
        assert list(result['augmentations']) == ['3']
