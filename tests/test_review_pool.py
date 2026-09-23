"""低置信度回流池：置信度判据、触发分类、去重累加、上限与写盘失败。

回流是旁路：写盘出问题绝不能影响分析主流程（这里把这一点也钉成测试）。
"""
from __future__ import annotations

import json

from app.core.config import POOL_LIMIT
from app.services.review_pool import ReviewPool, confidence_flags, layer_confidence


def layer(label, score, second=0.0):
    return {'label': label, 'score': score,
            'ranked': [{'label': label, 'score': score}, {'label': '其他', 'score': second}]}


def classified(intent, intent_score=0.9, emotion='无情绪', emotion_score=0.9, emotion_second=0.0):
    return {'primary_intent': layer(intent, intent_score, 0.0),
            'emotion': layer(emotion, emotion_score, emotion_second)}


def generic_intent(library) -> str:
    return sorted(library.generic_intents)[0]


def plain_intent(library) -> str:
    """非泛化标签：命中它说明标签库真的覆盖到了，不该因为「泛化」进池。"""
    return next(label for label in sorted(library.intents) if label not in library.generic_intents)


def plain_emotion(library) -> str:
    return next(label for label in sorted(library.emotions) if label not in library.generic_emotions)


def generic_emotion(library) -> str:
    return sorted(library.generic_emotions)[0]


class TestConfidenceFlags:
    def test_confident_result_is_not_pool_worthy(self, library):
        flags = confidence_flags(
            classified(plain_intent(library), 0.9, plain_emotion(library), 0.9), library)
        assert flags['low'] is False and flags['pool_worthy'] is False
        assert flags['trigger'] == 'low_confidence'

    def test_generic_intent_goes_to_the_pool_even_when_confident(self, library):
        """泛化标签（中性 family）几乎不携带信息，模型却很自信——只按分数攒会全漏掉。"""
        flags = confidence_flags(
            classified(generic_intent(library), 0.99, plain_emotion(library), 0.9), library)
        assert flags['generic_top'] is True
        assert flags['trigger'] == 'generic_label' and flags['pool_worthy'] is True
        assert any('泛化标签' in reason for reason in flags['pool_reasons'])

    def test_generic_emotion_goes_to_the_pool(self, library):
        flags = confidence_flags(
            classified(plain_intent(library), 0.9, generic_emotion(library), 0.99), library)
        assert flags['trigger'] == 'emotion_default' and flags['pool_worthy'] is True
        assert any('兜底桶' in reason for reason in flags['pool_reasons'])

    def test_both_layers_generic(self, library):
        flags = confidence_flags(
            classified(generic_intent(library), 0.9, generic_emotion(library), 0.9), library)
        assert flags['trigger'] == 'both'

    def test_low_score_marks_low_and_pool_worthy(self, library):
        flags = confidence_flags(
            classified(plain_intent(library), 0.2, plain_emotion(library), 0.9), library)
        assert flags['low'] is True and flags['low_intent'] is True
        assert any('分数不足' in reason for reason in flags['reasons'])

    def test_tie_is_treated_as_uncertain_even_with_high_score(self, library):
        """前两名贴太近（< TIE_GAP）算不确定：分高不代表没歧义。"""
        flags = confidence_flags(
            classified(plain_intent(library), 0.9, plain_emotion(library), 0.9), library)
        tied = confidence_flags(
            {'primary_intent': layer(plain_intent(library), 0.9, 0.88),
             'emotion': layer(plain_emotion(library), 0.9)}, library)
        assert flags['low'] is False
        assert tied['low'] is True
        assert any('平票' in reason for reason in tied['reasons'])

    def test_emotion_above_the_gate_but_below_the_pool_line_is_still_recorded(self, library):
        """过了 UI 门控（0.35）但不够自信（< 0.45）：界面不提示，池子里留档。"""
        flags = confidence_flags(
            classified(plain_intent(library), 0.9, plain_emotion(library), 0.4, 0.1), library)
        assert flags['low_emotion'] is False and flags['low'] is False
        assert flags['pool_worthy'] is True
        assert any('情绪分数偏低' in reason for reason in flags['pool_reasons'])


class TestLayerConfidence:
    def test_missing_layer_is_zeroed_out(self):
        assert layer_confidence(None, 0.5)['score'] == 0.0
        assert layer_confidence(None, 0.5)['ok'] is False


class TestReviewPool:
    def _flags(self, library, intent=None):
        return confidence_flags(
            classified(intent or plain_intent(library), 0.9, generic_emotion(library), 0.9),
            library)

    def test_record_writes_a_pending_entry(self, tmp_path, library):
        pool = ReviewPool(path=tmp_path / 'pool.json')
        pool.record('恋爱', {'speaker': 'other', 'message': '在吗', 'context': ''},
                    self._flags(library))

        entries = json.loads(pool.path.read_text(encoding='utf-8'))['entries']
        assert len(entries) == 1
        entry = entries[0]
        assert entry['status'] == 'pending' and entry['hits'] == 1
        assert entry['relationship'] == '恋爱' and entry['message'] == '在吗'
        assert entry['reasons'], '进池必须带上原因，否则人工没法审'

    def test_same_message_only_bumps_the_counter(self, tmp_path, library):
        """同关系 + 同文本 + 同意图只累加，避免池子被重复内容淹没。"""
        pool = ReviewPool(path=tmp_path / 'pool.json')
        item = {'speaker': 'other', 'message': '在吗', 'context': ''}
        pool.record('恋爱', item, self._flags(library))
        pool.record('恋爱', item, self._flags(library))

        entries = pool.load()['entries']
        assert len(entries) == 1 and entries[0]['hits'] == 2

    def test_full_pool_only_warns(self, tmp_path, library):
        pool = ReviewPool(path=tmp_path / 'pool.json', limit=1)
        pool.record('恋爱', {'message': '第一条'}, self._flags(library))
        pool.record('恋爱', {'message': '第二条'}, self._flags(library))

        assert len(pool.load()['entries']) == 1, '满了就不再写，但也不能炸'

    def test_broken_file_is_treated_as_empty(self, tmp_path, library):
        path = tmp_path / 'pool.json'
        path.write_text('{ 这不是 JSON', encoding='utf-8')
        pool = ReviewPool(path=path)

        assert pool.load()['entries'] == []
        pool.record('恋爱', {'message': '在吗'}, self._flags(library))
        assert len(pool.load()['entries']) == 1, '坏档会被覆盖成新池，而不是永久卡住'

    def test_write_failure_does_not_raise(self, tmp_path, library):
        """写盘失败只记日志：回流是旁路，绝不能把分析主流程带崩。"""
        pool = ReviewPool(path=tmp_path)   # 指向目录 → write_text 必然失败
        pool.record('恋爱', {'message': '在吗'}, self._flags(library))   # 不抛即通过

    def test_pending_only_returns_pending(self, tmp_path, library):
        pool = ReviewPool(path=tmp_path / 'pool.json')
        pool.record('恋爱', {'message': '在吗'}, self._flags(library))
        payload = pool.load()
        payload['entries'][0]['status'] = 'reviewed'
        pool.path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

        assert pool.pending() == []
        assert pool.load()['schema'] == 1
        assert POOL_LIMIT > 0
