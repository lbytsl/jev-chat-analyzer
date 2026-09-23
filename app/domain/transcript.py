"""聊天记录解析：把用户粘贴的整段文本切成带说话人的消息序列。

支持两种格式，可以混用：

1. 标签式       `我：今晚一起吃饭吗？` / `林潇：……`
2. 微信复制式   `名字 ⏎ 2026年09月20日 15:51 ⏎ 消息正文`

若整段没有任何说话人标记，则不伪造消息，返回空列表由调用方提示用户补标记——
不再逐行伪造成多条「对方」消息（那样会得到「识别 20 条，全是对方」的假结果）。
"""
from __future__ import annotations

import re

LABEL_RE = re.compile(r'^([^：:\n]{1,12}?)\s*[:：]\s*(.+)$')
ME_WORDS = ('我', '我方', '自己', '本人')
OTHER_WORDS = ('她', '他', '对方', 'TA', 'ta', 'Ta', 'tA', '对方昵称')

# 微信电脑版复制出来的时间行：2026年09月20日 15:51 / 2026-09-20 15:51 / 昨天 15:51 / 15:51
_TS_DATE = r'(?:\d{4}年)?\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2}|星期[一二三四五六日天]|昨天|今天|前天'
TS_RE = re.compile(r'^(?:' + _TS_DATE + r')?[ 　]*(?:上午|下午|凌晨|中午|晚上)?[ 　]*\d{1,2}:\d{2}(?::\d{2})?$')
# 名字行：短、不含冒号、不是时间行、不带句读（消息正文通常带标点，借此区分）。
# 姓名可能包含英文句号，例如微信昵称「Mr.Wang」；它后面必须紧跟时间行，
# 因此允许句号不会把普通带句号的正文误识别成说话人。
NAME_PUNCT_RE = re.compile(r'[。！？，、；：,!?;]')


def _is_name_line(line: str) -> bool:
    return bool(line) and len(line) <= 24 and ':' not in line and '：' not in line \
        and not TS_RE.match(line) and not NAME_PUNCT_RE.search(line)


def _scan_blocks(lines: list[str]):
    """把聊天记录切成 [起始行, 说话人, 正文起始行, 首行剩余正文, 时间] 的块。"""
    blocks, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith('#'):          # 跳过文档标题行（## 聊天记录 …）
            i += 1
            continue
        following = lines[i + 1].strip() if i + 1 < n else ''
        if following and TS_RE.match(following) and _is_name_line(line):
            blocks.append([i, line, i + 2, None, following])
            i += 2
            continue
        match = LABEL_RE.match(line)
        if match and not TS_RE.match(line):
            blocks.append([i, match.group(1).strip(), i + 1, match.group(2).strip(), None])
            i += 1
            continue
        i += 1
    return blocks


def parse_transcript(text: str, me_label: str | None = None):
    """解析聊天记录。

    标签可以是 我/她/对方，也可以是真实名字。返回值：

        messages  按出现顺序、只保留有正文的消息，带 index（从 1 起）与 speaker（me/other）
        speakers  按标签首次出现顺序排列，含 role 与 count
        me_label  判定出的「我」的标签，可能为 None
    """
    lines = [raw.strip() for raw in text.splitlines()]
    blocks = _scan_blocks(lines)
    candidates: list[str] = []
    for block in blocks:
        if block[1] not in candidates:
            candidates.append(block[1])
    known = [label for label in candidates if label in ME_WORDS or label in OTHER_WORDS]
    labelled = bool(known) or len(candidates) >= 2

    messages = []
    if labelled:
        for position, (_, label, content_start, head, timestamp) in enumerate(blocks):
            end = blocks[position + 1][0] if position + 1 < len(blocks) else len(lines)
            body = ([head] if head else []) + [line for line in lines[content_start:end]
                   if line and not TS_RE.match(line) and not line.startswith('#')]   # 过滤夹在中间的时间行与标题行
            messages.append({'label': label, 'text': '\n'.join(body).strip(), 'timestamp': timestamp})

    if me_label is None:
        for label in candidates:
            if label in ME_WORDS:
                me_label = label
                break
    if me_label is None and candidates:
        # 没有「我」这类词时，在「非对方代称」的名字里取第一个当默认「我」。
        # 若整段只有「她：」「对方：」这类代称，则不指定「我」，全部按对方处理。
        unnamed = [label for label in candidates if label not in OTHER_WORDS]
        me_label = unnamed[0] if unnamed else None
    for item in messages:
        item['speaker'] = 'me' if item['label'] is not None and item['label'] == me_label else 'other'
    speakers = [{'label': label, 'role': 'me' if label == me_label else 'other',
                 'count': sum(1 for item in messages if item['label'] == label)} for label in candidates]
    ordered = [dict(item, index=i + 1) for i, item in enumerate(messages) if item['text']]
    return ordered, speakers, me_label
