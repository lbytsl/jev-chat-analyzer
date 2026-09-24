"""标签库：意图 / 情绪的定义、候选过滤与展示层叫法。

标签库是「判定的锚点」，分三层读：

1. 种子文件 `data/intents_seed.json`：59 个意图 + 42 个情绪标签 + 8 个情绪大类；
2. 场景过滤：每个关系只给它「该场景出现过的标签」，天然杜绝跨场景借标签；
3. 展示层 `display_names`：同一个归一化标签在不同关系下换叫法（模型完全不感知展示层，
   所以换叫法不会重新引入锚点漂移）。

情绪是三级结构（2026-09-23 定稿）：
    一级 family（8 个大类，全场景通用）→ 二级标签（归一化名 + 一份定义，全场景唯一）
    → 展示层 display_names（按关系换词）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import INTENTS_SEED_PATH

# 二期关系词汇：与前端下拉、intents_seed.json 的 scenarios 完全一致。
RELATIONSHIPS: tuple[str, ...] = ('暧昧', '恋爱', '上下级', '同事')

# 意图层和情绪层是分开判的，会判出互相打脸的组合：一句话被判成「打情骂俏」（定义里明写
# "没有真怒气，是在拉近距离"），情绪却落在「生气了」（带真火气+放弃）。用户看到就是
# "在调情 + 在发火"，说不通。这类话的负面措辞其实是撒娇，真情绪落在赌气/失落上，
# 所以意图命中下面这些「亲昵」意图、而情绪大类判到硬负面时，把第二级候选换成柔软的两类。
WARM_INTENTS: frozenset[str] = frozenset(
    {'打情骂俏', '要贴贴', '求关注', '想你了', '整点浪漫', '认错求和', '报备安抚', '主动关心'})
HARD_NEGATIVE_FAMILIES: frozenset[str] = frozenset({'生气', '冷淡抽离'})
SOFT_NEGATIVE_FAMILIES: tuple[str, ...] = ('有怨气', '委屈难过')


@dataclass(frozen=True, slots=True)
class LabelLibrary:
    """内存中的标签库。不可变，进程内共享一份。"""

    intents: dict[str, dict]                     # label -> {definition, family, scenarios, ...}
    emotions: dict[str, dict]
    families: dict[str, dict]                    # family -> {definition}
    intent_candidates: dict[str, dict[str, str]]  # relationship -> {label: definition}
    emotion_candidates: dict[str, dict[str, str]]
    intent_family_definitions: dict[str, str]
    intent_taxonomy: dict[str, dict]
    emotion_taxonomy: dict[str, dict]
    relation_directions: dict[str, str]
    response_needs: dict[str, str]
    communication_styles: dict[str, str]
    schema_version: str
    label_version: str

    @classmethod
    def load(cls, path: Path | str) -> LabelLibrary:
        seed = json.loads(Path(path).read_text(encoding='utf-8'))
        intents = seed['intents']
        emotions = seed['emotions']
        library = cls(
            intents=intents,
            emotions=emotions,
            families=seed['emotion_families'],
            intent_candidates={
                rel: {label: d['definition'] for label, d in intents.items() if rel in d['scenarios']}
                for rel in RELATIONSHIPS
            },
            emotion_candidates={
                rel: {label: _emotion_definition(d, rel) for label, d in emotions.items() if rel in d['scenarios']}
                for rel in RELATIONSHIPS
            },
            intent_family_definitions=seed.get('intent_family_definitions') or {},
            intent_taxonomy=seed.get('intent_taxonomy') or {},
            emotion_taxonomy=seed.get('emotion_taxonomy') or {},
            relation_directions=seed.get('relation_directions') or {},
            response_needs=seed.get('response_needs') or {},
            communication_styles=seed.get('communication_styles') or {},
            schema_version=str(seed.get('schema_version') or '1.0'),
            label_version=str(seed.get('label_version') or ''),
        )
        library._validate_taxonomy()
        return library

    def _validate_taxonomy(self) -> None:
        """启动时尽早发现标签元数据漂移，别等真实请求回来后才报「无效标签」。"""
        missing_intents = set(self.intents) - set(self.intent_taxonomy)
        missing_emotions = set(self.emotions) - set(self.emotion_taxonomy)
        if missing_intents or missing_emotions:
            raise ValueError('标签规范元数据不完整：intent={} emotion={}'.format(
                sorted(missing_intents), sorted(missing_emotions)))
        for name, taxonomy in (('intent', self.intent_taxonomy), ('emotion', self.emotion_taxonomy)):
            keys = [str(item.get('key') or '') for item in taxonomy.values()]
            labels = [str(item.get('model_label') or '') for item in taxonomy.values()]
            if '' in keys or len(keys) != len(set(keys)):
                raise ValueError('{} taxonomy 的 key 为空或重复'.format(name))
            if '' in labels or len(labels) != len(set(labels)):
                raise ValueError('{} taxonomy 的 model_label 为空或重复'.format(name))

    # ---------- 派生集合 ----------
    @property
    def family_order(self) -> list[str]:
        return list(self.families)

    @property
    def intent_family_order(self) -> list[str]:
        return list(self.intent_family_definitions)

    @property
    def generic_intents(self) -> frozenset[str]:
        """`接住话了`/`陈述事实` 这类 family=中性 的泛化标签是库的兜底位。

        它们几乎不携带信息，命中它们往往说明「没有更合适的标签」，而不是「这句话真的
        只想接话」。实测这类样本模型反而很自信，只按置信度攒样本会漏掉全部「库缺标签」的
        情况——所以泛化标签命中一律进回流池。
        """
        return frozenset(label for label, d in self.intents.items() if d.get('family') == '中性')

    @property
    def generic_emotions(self) -> frozenset[str]:
        """情绪层的同类兜底桶：family=平静中性的标签（无情绪 / 稳住了）。

        比意图层的兜底更危险——实测 120 条语料 55% 落进这里，且其中 94% 模型分差很大、
        非常确定，置信度门控完全抓不到。所以一律进池，与意图层泛化标签同等待遇。
        """
        return frozenset(label for label, d in self.emotions.items() if d.get('family') == '平静中性')

    # ---------- 查询 ----------
    def intent_definition(self, label: str) -> str:
        return self.intents[label]['definition']

    def intent_candidates_for(self, relationship: str, families=None) -> dict[str, str]:
        """按场景与意图大类取旧标签候选；旧标签仍是兼容层里的领域主键。"""
        candidates = self.intent_candidates[relationship]
        if families is None:
            return dict(candidates)
        wanted = set(families)
        return {label: definition for label, definition in candidates.items()
                if self.intents[label].get('family') in wanted}

    def intent_model_criteria(self, relationship: str, families=None) -> dict[str, str]:
        """给 Jev 的规范意图名 → 定义；产品化旧名不再直接充当 choice key。"""
        return {self.intent_model_label(label): definition
                for label, definition in self.intent_candidates_for(relationship, families).items()}

    def emotion_model_criteria(self, relationship: str, families=None) -> dict[str, str]:
        """给 Jev 的规范情绪名 → 定义；显示名仍按场景在结果组装时决定。"""
        return {self.emotion_model_label(label): definition
                for label, definition in self.emotion_candidates_for(relationship, families).items()}

    def intent_model_label(self, legacy_label: str) -> str:
        return self.intent_taxonomy[legacy_label]['model_label']

    def emotion_model_label(self, legacy_label: str) -> str:
        return self.emotion_taxonomy[legacy_label]['model_label']

    def intent_key(self, legacy_label: str) -> str:
        return self.intent_taxonomy[legacy_label]['key']

    def emotion_key(self, legacy_label: str) -> str:
        return self.emotion_taxonomy[legacy_label]['key']

    def resolve_intent_label(self, answer: str) -> str | None:
        """稳定 ID / 规范名 / 旧名或别名 → 兼容层旧标签。"""
        return self._resolve_label(answer, self.intents, self.intent_taxonomy)

    def resolve_emotion_label(self, answer: str) -> str | None:
        return self._resolve_label(answer, self.emotions, self.emotion_taxonomy)

    @staticmethod
    def _resolve_label(answer: str, entries: dict[str, dict], taxonomy: dict[str, dict]) -> str | None:
        if answer in entries:
            return answer
        for legacy, item in taxonomy.items():
            aliases = item.get('aliases') or ()
            if answer == item['key'] or answer == item['model_label'] or answer in aliases:
                return legacy
        return None

    def intent_display_name(self, label: str, relationship: str) -> str:
        """意图展示名默认沿用旧产品词，也允许标签版本按场景覆盖。"""
        item = self.intent_taxonomy[label]
        return (item.get('display_names') or {}).get(relationship) or label

    def normalize_intent_probabilities(self, probabilities: dict) -> dict[str, float]:
        return self._normalize_probabilities(probabilities, self.resolve_intent_label)

    def normalize_emotion_probabilities(self, probabilities: dict) -> dict[str, float]:
        return self._normalize_probabilities(probabilities, self.resolve_emotion_label)

    @staticmethod
    def _normalize_probabilities(probabilities: dict, resolver) -> dict[str, float]:
        normalized = {}
        for raw_label, raw_score in probabilities.items():
            label = resolver(raw_label)
            if label is not None:
                normalized[label] = float(raw_score)
        return normalized

    def emotion_definition(self, label: str, relationship: str) -> str:
        """取标签在指定场景下的定义；没有场景分化时回落到 definition。"""
        return _emotion_definition(self.emotions[label], relationship)

    def emotion_candidates_for(self, relationship: str, families=None) -> dict[str, str]:
        """某关系下的情绪候选；families 非空时只取这些大类下的标签（两级路由的第二级）。

        v008：第二级始终额外并入该场景的中性兜底标签（无情绪 / 稳住了）。
        否则模型被锁进某个大类里，就算看不出情绪也只能硬挑一个沾边的——那正是要避免的瞎猜。
        """
        out = {}
        for label, d in self.emotions.items():
            if relationship not in d['scenarios']:
                continue
            if families is not None and d.get('family') not in families:
                continue
            out[label] = _emotion_definition(d, relationship)
        if families is not None:
            for label, d in self.emotions.items():
                if relationship in d['scenarios'] and d.get('family') == '平静中性' and label not in out:
                    out[label] = _emotion_definition(d, relationship)
        return out

    def family_display(self, family: str, relationship: str) -> str:
        """一级大类退到展示层时的叫法。

        大类本身是内部归类词（如「平静中性」），直接给用户看不是人话，得换成场景里的说法。
        其余七类（开心 / 生气 / 有怨气 …）本身就是日常用词，原样返回即可。
        """
        if family == '平静中性':
            return '看不出情绪' if relationship in ('暧昧', '恋爱') else '就事论事'
        return family

    def display_name(self, label: str, relationship: str) -> str:
        """展示层叫法：同一个归一化标签在不同关系下换词给用户看。"""
        entry = self.emotions.get(label) or {}
        return (entry.get('display_names') or {}).get(relationship) or label


def _emotion_definition(entry: dict, relationship: str) -> str:
    return (entry.get('definitions') or {}).get(relationship) or entry['definition']


@lru_cache(maxsize=1)
def get_label_library() -> LabelLibrary:
    """进程内单例标签库。测试可 `get_label_library.cache_clear()` 后指向临时文件。"""
    return LabelLibrary.load(INTENTS_SEED_PATH)
