"""流水线测试：解读对象圈定、复用旧结果、生成层开关、回流池落盘、断连取消。全部用假上游。"""
from __future__ import annotations

import json
import threading

import pytest

from app.core.config import get_settings
from app.core.exceptions import InvalidRequest, JevConnectionError
from app.core.executors import shared_pool
from app.services.pipeline import PipelineService, build_context
from app.services.review_pool import ReviewPool
from tests.conftest import FakeClassifier, FakeGeneration

TRANSCRIPT = '我：在忙吗\n她：刚开完会\n我：那晚点说'


def test_build_context_keeps_exactly_five_prior_messages():
    messages = [{'index': index, 'label': '她', 'text': '消息{}'.format(index),
                 'speaker': 'other', 'timestamp': None}
                for index in range(1, 8)]

    context = build_context(messages, messages[-1])

    assert context.splitlines() == ['她：消息2', '她：消息3', '她：消息4', '她：消息5', '她：消息6']


def build(tmp_path, classifier=None, generation=None, **kwargs):
    classifier = classifier or FakeClassifier(**kwargs)
    pool = ReviewPool(path=tmp_path / 'pool.json')
    service = PipelineService(classifier=classifier, generation=generation or FakeGeneration(), pool=pool)
    return service, classifier, pool


class CancellingClassifier(FakeClassifier):
    """第一次分类就把 cancel 置位，模拟「客户端在分析刚开始时断开」。

    刻意在 classify **入口**置位（而不是分类完之后）：这样「第一条 message 事件到达时
    cancel 必然已置位」，测试不依赖线程调度顺序。
    """

    def __init__(self, cancel, **kwargs):
        super().__init__(**kwargs)
        self._cancel = cancel

    def classify(self, state, **kwargs):
        self._cancel.set()
        return super().classify(state, **kwargs)


class CancellingGeneration(FakeGeneration):
    """同上，作用在生成层。"""

    def __init__(self, cancel, **kwargs):
        super().__init__(**kwargs)
        self._cancel = cancel

    def generate_interpretation(self, *args, **kwargs):
        self._cancel.set()
        return super().generate_interpretation(*args, **kwargs)


def wait_for_jev_pool() -> None:
    """等共享池把「已经提交」的任务跑完，再数调用次数。

    取消是协作式的：已进池的那几条会跑完（不强杀线程），只是不再提交新的。
    """
    workers = get_settings().jev_max_workers
    shared_pool('jev', workers).submit(lambda: None).result(timeout=5)


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


class TestCancellation:
    """客户端断开后不再把剩下的消息跑完：每条 2 次 Jev 调用，那是真金白银。"""

    def test_cancel_stops_submitting_the_rest(self, tmp_path):
        cancel = threading.Event()
        service, classifier, _ = build(tmp_path, classifier=CancellingClassifier(cancel))
        transcript = '\n'.join(f'她：第{i}条' for i in range(12))

        events = list(service.analyze_stream({'transcript': transcript, 'relationship': '恋爱'},
                                            cancel=cancel))
        wait_for_jev_pool()

        # 只跑满了第一轮窗口：12 条里剩下的 8 条压根没进池。
        workers = get_settings().jev_max_workers
        assert workers < 12, '这个用例需要「窗口小于消息数」，别把并发度调到 12 以上'
        assert len(classifier.calls) == workers
        assert not any(event['type'] == 'done' for event in events), '取消不发 done，路由据此不落库'

    def test_without_cancel_the_whole_batch_runs(self, tmp_path):
        """不传 cancel（脚本 / 夹具走的非流式路径）行为不变。"""
        service, _, _ = build(tmp_path)
        events = list(service.analyze_stream({'transcript': TRANSCRIPT, 'relationship': '恋爱'}))
        assert any(event['type'] == 'done' for event in events)

    def test_cancel_skips_the_done_event_for_generation(self, tmp_path):
        """补跑生成被取消时也不发 done：路由就不会去合并一个半截结果。"""
        cancel = threading.Event()
        service, _, _ = build(tmp_path)
        analyzed = service.analyze({'transcript': TRANSCRIPT, 'relationship': '恋爱',
                                    'read_labels': ['我', '她']})
        service._generation = CancellingGeneration(cancel)

        events = list(service.interpret_stream({'prev': analyzed}, cancel=cancel))
        assert not any(event['type'] == 'done' for event in events)


class TestStreamingFailureAccounting:
    """流式路径里「补跑后仍失败」的那几条要如实计数，不能让整条流崩掉。

    回归用例：`analyze_stream` 曾经把失败事件的 `index`（int）直接交给 `_envelope`，
    而 `_envelope` 是按 `item['index']` 取的。只要「一部分成功、一部分两轮都失败」，
    收尾就会 TypeError —— 前端看到的是「分析没有正常结束，请重试」，而且不会落库。
    整批全失败反而正常（那条路径会先抛连接错误）。
    """

    def test_partial_failure_still_emits_done(self, tmp_path, monkeypatch):
        # 补跑之间会 sleep(2)；测试里换成空操作。
        monkeypatch.setattr('app.services.pipeline.time.sleep', lambda *_: None)
        transcript = '\n'.join(f'她：第{i}条' for i in range(4))
        # 前 5 次调用失败：第一轮 4 条全断连，补跑那一轮里再挂 1 条。
        service, classifier, _ = build(tmp_path, classifier=FakeClassifier(fail_times=5))

        events = list(service.analyze_stream({'transcript': transcript, 'relationship': '恋爱'}))

        done = [event for event in events if event['type'] == 'done']
        assert len(done) == 1, '部分失败不该让整条流没有收尾'
        data = done[0]['data']
        assert data['count'] == 3 and data['failed_count'] == 1
        assert len(data['failed_indexes']) == 1
        assert isinstance(data['failed_indexes'][0], int), 'failed_indexes 是序号列表'
        assert data['failed_indexes'][0] in [item['index'] for item in data['messages']]
        # 两轮各 4 条；失败的那条正好是全局最后一条时，才会再为 reply_target 补一次。
        assert len(classifier.calls) in (8, 9)


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
