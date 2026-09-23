# -*- coding: utf-8 -*-
"""量两件事（都不需要标准答案）：

1. 分差 margin：一级大类 vs 二级标签 vs 意图，哪层是"确定"的、哪层在猜。
2. 大类分布：如果「平静中性」占比过高，说明系统在大量场合退成"没情绪"——
   这正是连续两次报过的问题（暧昧 4 句里 3 句「看不出情绪」）。

用法：`.venv/bin/python scripts/measure_decisive.py [每个场景取几条]`
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.services.classifier import ClassifierService  # noqa: E402

SAMPLE_PATH = BASE_DIR / '四场景对话样本_40条.txt'

raw = SAMPLE_PATH.read_text(encoding='utf-8').split('\n')
segs, cur_seg, cur_rel = [], None, None
for line in raw:
    stripped = line.strip()
    if stripped.startswith('===================='):
        cur_rel = next((x for x in ('恋爱', '暧昧', '同事', '上下级') if x in stripped), cur_rel)
        continue
    matched = re.match(r'^#\s*([LACB])(\d+)', stripped)
    if matched:
        cur_seg = []
        segs.append((cur_rel, matched.group(0), cur_seg))
        continue
    if stripped and not stripped.startswith('#') and cur_seg is not None:
        cur_seg.append(stripped)

conv = []
for rel, tag, body in segs:
    j = 0
    while j + 2 < len(body):
        if re.match(r'^\d{4}年', body[j + 1]):
            conv.append((rel, tag, body[j], body[j + 2]))
            j += 3
        else:
            j += 1
print('段落数:', len(segs), '| 消息数:', len(conv))
print('按场景:', dict(defaultdict(int, {r: sum(1 for x in conv if x[0] == r)
                                        for r in set(x[0] for x in conv)})))

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 4
classifier = ClassifierService()

# 按段跑，保留对话上下文
by_seg = defaultdict(list)
for rel, tag, who, text in conv:
    by_seg[tag].append((rel, who, text))

rows = []
for tag, lst in by_seg.items():
    rel = lst[0][0]
    for k, (_, _, text) in enumerate(lst[:LIMIT]):
        ctx = '\n'.join(who + '：' + t for _, who, t in lst[max(0, k - 5):k])
        try:
            result = classifier.classify({'message': text, 'context': ctx,
                                          'relationship': rel, 'speaker': 'other'}, skip_gen=True)
        except Exception as exc:  # noqa: BLE001 —— 单条失败不该中断整轮测量
            print('跳过', type(exc).__name__, str(exc)[:60])
            continue
        family, emotion, primary = result['emotion_family'], result['emotion'], result['primary_intent']
        fam_ranked, emo_ranked, pri_ranked = family['ranked'], emotion['ranked'], primary['ranked']
        rows.append({'rel': rel, 'seg': tag, 'text': text,
                     'fam': family['key'], 'fs': family['score'],
                     'fgap': round(fam_ranked[0]['score'] - (fam_ranked[1]['score'] if len(fam_ranked) > 1 else 0), 2),
                     'emo': emotion['key'], 'es': emotion['score'], 'disp': emotion['display'],
                     'egap': round(emo_ranked[0]['score'] - (emo_ranked[1]['score'] if len(emo_ranked) > 1 else 0), 2),
                     'coarse': emotion.get('coarse'), 'ncand': len(emotion['probabilities']),
                     'int': primary['key'], 'is': primary['score'],
                     'igap': round(pri_ranked[0]['score'] - (pri_ranked[1]['score'] if len(pri_ranked) > 1 else 0), 2)})
json.dump(rows, open('/tmp/decisive.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('有效样本:', len(rows))

n = len(rows)
if not n:
    print('没有有效样本（上游连不上或样本文件为空），先检查 .env 与网络。')
    sys.exit(1)


def stat(gapkey, label):
    gaps = sorted(x[gapkey] for x in rows)
    dec = sum(1 for x in gaps if x >= 0.15)
    weak = sum(1 for x in gaps if x < 0.05)
    print(f'{label:8s} 中位分差 {gaps[n // 2]:.2f} | 平均 {sum(gaps) / n:.2f} '
          f'| 确定(>=.15) {dec}/{n}={dec / n:.0%} | 在猜(<.05) {weak}/{n}={weak / n:.0%}')


print()
stat('fgap', '一级大类')
stat('egap', '二级标签')
stat('igap', '意图层')
print()
counting = defaultdict(int)
for x in rows:
    counting[x['fam']] += 1
print('大类分布:', dict(sorted(counting.items(), key=lambda kv: -kv[1])))
neutral = counting.get('平静中性', 0)
print(f'>>> 「平静中性」占比 {neutral}/{n} = {neutral / n:.0%}')
emotion_counting = defaultdict(int)
for x in rows:
    emotion_counting[x['emo']] += 1
print('二级标签 top8:', dict(sorted(emotion_counting.items(), key=lambda kv: -kv[1])[:8]))
print('触发 coarse:', sum(1 for x in rows if x['coarse']))
print()
print('=== 判成平静中性的样本（人工看是否真的没情绪）===')
for x in rows:
    if x['fam'] == '平静中性':
        print(f'  [{x["rel"]}] {x["text"][:34]:36s} -> {x["emo"]}({x["es"]}) 展示「{x["disp"]}」')
