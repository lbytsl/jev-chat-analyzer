"""说话人识别的「前后端一致性」契约。

`frontend/src/utils/transcript.js` 与 `app/domain/transcript.py` 是刻意保留的双份实现
（前端要实时显示 chips，不能每敲一个字就往后端跑一趟）。为了不让两边悄悄分叉，规则用例
集中在 `data/transcript_cases.json`：这个测试跑后端那一份，前端 `pnpm run check:transcript`
跑同一份文件。改任何一侧的规则都要同时改用例，否则两边会有一边红。

历史上真分叉过两处，都是靠这份用例钉住的：
- 前端不跳 `#` 文档标题行、后端跳 → 标题后紧跟时间行时前端多认出一个说话人，
  用户勾中它就会拿到 400「识别不到这个人」；
- 前端默认「我是谁」用「自称『我』最多」的启发式、后端取首个非对方代称 →
  界面预选的人与落库判定的人不一致。
"""
from __future__ import annotations

import json

import pytest

from app.core.config import DATA_DIR
from app.domain.transcript import parse_transcript

CASES_PATH = DATA_DIR / 'transcript_cases.json'


def load_cases() -> list[dict]:
    with CASES_PATH.open(encoding='utf-8') as handle:
        return json.load(handle)['cases']


@pytest.mark.parametrize('case', load_cases(), ids=lambda case: case['name'])
def test_backend_matches_shared_cases(case):
    """后端解析结果必须与共享用例一致（前端由 `pnpm run check:transcript` 跑同一份）。"""
    _messages, speakers, me_label = parse_transcript(case['transcript'])
    labels = [speaker['label'] for speaker in speakers]
    assert labels == case['labels'], '说话人列表与共享用例不一致'
    # 没有可信说话人时「我是谁」没有意义：前端 usePeople 也是这么处理的（people 为空 → me 为 None）。
    assert (me_label if speakers else None) == case['me_label']


def test_shared_cases_cover_the_known_divergences():
    """用例集合必须一直覆盖曾经真分叉过的那两类输入，别在整理用例时把它们删了。"""
    names = {case['name'] for case in load_cases()}
    assert 'markdown_heading_then_time' in names
    assert 'only_other_pronouns' in names
