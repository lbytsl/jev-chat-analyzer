"""低置信度回流池与置信度判据（2026-09-23 埋点）。

Jev 是封闭选择题：标签库没覆盖的表达不会「拒绝作答」，只会被硬塞进定义最宽的泛化标签
（恋爱场景里就是「接住话了」）。这类样本攒下来是后续扩库唯一的可靠依据——
补哪些标签由人拍板，代码只负责把可疑样本收集好，绝不自动改标签库。

回流是旁路：写盘失败只记日志不抛出，绝不能影响分析主流程。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from app.core.config import (
    EMOTION_MIN,
    EMOTION_POOL_MIN,
    INTENT_MIN,
    POOL_LIMIT,
    POOL_PATH,
    TIE_GAP,
)
from app.core.logging import get_logger
from app.domain.labels import LabelLibrary

logger = get_logger('pool')

POOL_SCHEMA = 1
POOL_NOTE = '低置信度回流池：人工审后决定补哪些标签；审完把 status 改成 reviewed 或 dismissed。'


def layer_confidence(layer: dict | None, minimum: float) -> dict:
    """单层置信度：绝对分数 + 前两名差距。只看分数会漏掉「看着达标、其实平票」的情况。"""
    layer = layer or {}
    ranked = layer.get('ranked') or []
    score = round(float(layer.get('score') or 0), 3)
    second = round(float(ranked[1]['score'] or 0), 3) if len(ranked) > 1 else 0.0
    gap = round(score - second, 3)
    return {'label': layer.get('label'), 'score': score, 'gap': gap,
            'top': [{'label': item['label'], 'score': item['score']} for item in ranked[:3]],
            'ok': score >= minimum and gap >= TIE_GAP}


def confidence_flags(result: dict, library: LabelLibrary) -> dict:
    """判定这条结果是否值得人工看一眼，并给出原因（供回流池与前端提示共用）。"""
    result = result or {}
    intent = layer_confidence(result.get('primary_intent'), INTENT_MIN)
    emotion = layer_confidence(result.get('emotion'), EMOTION_MIN)
    reasons = []
    if not intent['ok']:
        reasons.append('意图' + ('分数不足' if intent['score'] < INTENT_MIN else '前两名平票')
                       + '（' + str(intent['label']) + '）')
    if not emotion['ok']:
        reasons.append('情绪' + ('分数不足' if emotion['score'] < EMOTION_MIN else '前两名平票')
                       + '（' + str(emotion['label']) + '）')
    generic_top = str(intent['label']) in library.generic_intents
    # 情绪落进「平静中性」兜底桶：这类样本模型反而很自信，只按置信度攒会全漏掉。
    generic_emotion = str(emotion['label']) in library.generic_emotions
    pool_reasons = list(reasons)
    if generic_top:
        pool_reasons.append('命中泛化标签「' + str(intent['label']) + '」：置信度再高也值得人看一眼，'
                            '多半是库里缺标签')
    if generic_emotion:
        pool_reasons.append('情绪落进兜底桶「' + str(emotion['label']) + '」：情绪库把没覆盖到的情绪都吸进这一类，'
                            '模型通常还很确定，置信度门控抓不到')
    if emotion['ok'] and emotion['score'] < EMOTION_POOL_MIN:
        pool_reasons.append('情绪分数偏低（' + str(emotion['label']) + ' ' + str(emotion['score'])
                            + '）：过了门控但不够自信，多半是情绪候选集缺这一格')
    if generic_emotion and generic_top:
        trigger = 'both'
    elif generic_top:
        trigger = 'generic_label'
    elif generic_emotion:
        trigger = 'emotion_default'
    else:
        trigger = 'low_confidence'
    return {'intent': intent, 'emotion': emotion,
            'low_intent': not intent['ok'], 'low_emotion': not emotion['ok'],
            'low': bool(reasons), 'reasons': reasons,
            'generic_top': generic_top, 'generic_emotion': generic_emotion, 'trigger': trigger,
            'pool_worthy': bool(pool_reasons), 'pool_reasons': pool_reasons}


class ReviewPool:
    """JSON 文件实现。同一进程内并发写用线程锁保护（本地单进程服务足够）。"""

    def __init__(self, path: Path | None = None, limit: int = POOL_LIMIT):
        self.path = Path(path or POOL_PATH)
        self.limit = limit
        self._lock = threading.Lock()

    def load(self) -> dict:
        pool = {'schema': POOL_SCHEMA, 'note': POOL_NOTE, 'entries': []}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(loaded, dict) and isinstance(loaded.get('entries'), list):
                    pool = loaded
            except json.JSONDecodeError:
                pass
        return pool

    def pending(self, pool: dict | None = None) -> list[dict]:
        entries = (pool or self.load())['entries']
        return [item for item in entries if item.get('status') == 'pending']

    def record(self, relationship: str, item: dict, flags: dict) -> None:
        """同关系 + 同文本 + 同意图只累加次数，避免池子被重复内容淹没。"""
        key = relationship + '|' + item.get('message', '') + '|' + str(flags['intent']['label'])
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            with self._lock:
                pool = self.load()
                hit = next((e for e in pool['entries']
                            if e.get('key') == key and e.get('status') == 'pending'), None)
                if hit:
                    hit['hits'] = int(hit.get('hits', 1)) + 1
                    hit['last_seen'] = stamp
                else:
                    if len(pool['entries']) >= self.limit:
                        logger.warning('回流池已满（%s 条），先人工清理再继续攒。', self.limit)
                        return
                    pool['entries'].append({
                        'key': key, 'relationship': relationship,
                        'speaker': item.get('speaker'), 'message': item.get('message'),
                        'context': item.get('context'),
                        'intent': flags['intent'], 'emotion': flags['emotion'],
                        'trigger': flags.get('trigger'), 'reasons': flags.get('pool_reasons', []),
                        'hits': 1,
                        'first_seen': stamp, 'last_seen': stamp,
                        'status': 'pending', 'review': None})
                self.path.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as exc:
            logger.warning('回流池写入失败：%s', exc)
