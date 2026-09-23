"""命令行入口（`python -m app …`）：参数解析与单条夹具判定。

`run_check` / `serve` 会真连上游或起服务，不在这里跑；这里覆盖「不碰网络」的那部分。
"""
from __future__ import annotations

from app.cli import build_parser, check_case, main
from app.services.review_pool import ReviewPool


def canned_result(intent='接住话了', emotion='无情绪', score=0.9):
    return {
        'version': 'v008', 'model': 'fake-model', 'elapsed_ms': 1,
        'primary_intent': {'key': intent, 'label': intent, 'score': score,
                           'ranked': [{'key': intent, 'label': intent, 'score': score},
                                      {'key': '其他', 'label': '其他', 'score': 0.1}]},
        'emotion': {'key': emotion, 'label': emotion, 'score': 0.8, 'ranked': []},
        'usage': {}, 'input': {},
    }


class StubClassifier:
    """check_case 只用到 classify_payload（打上游的部分不在这里测）。"""

    def __init__(self, **overrides):
        self.overrides = overrides

    def classify_payload(self, payload):  # noqa: ARG002 - 签名与真实服务对齐
        return canned_result(**self.overrides)


class TestParser:
    def test_commands_and_serve_flags(self):
        parser = build_parser()
        assert parser.parse_args([]).command is None, '不带子命令 = 默认 serve'
        assert parser.parse_args(['check']).command == 'check'
        assert parser.parse_args(['pool']).command == 'pool'
        serve = parser.parse_args(['serve', '--no-reload', '--port', '9000'])
        assert serve.no_reload is True and serve.port == 9000


class TestCheckCase:
    def test_positive_assertions_pass(self):
        case = {'id': 'ok', 'input': {}, 'expect': {
            'top1': '接住话了', 'emotion': '无情绪', 'min_score': 0.8,
            'margin_min': 0.5, 'rationale': '明显是在接话'}}

        row = check_case(case, StubClassifier())

        assert row['passed'] is True and row['id'] == 'ok'
        assert row['top1']['key'] == '接住话了' and row['margin'] >= 0.5
        assert 'intent_ranking' in row and row['model'] == 'fake-model'

    def test_negative_assertions_are_reported_by_name(self):
        case = {'id': 'neg', 'input': {}, 'expect': {
            'top1': '接住话了', 'max_others': 0.05, 'max_scores': {'其他': 0.05},
            'rationale': '不该被带起别的意图'}}

        row = check_case(case, StubClassifier())

        failed = {item['name'] for item in row['checks'] if not item['pass']}
        assert row['passed'] is False
        assert failed == {'max_others', 'max_scores.其他'}

    def test_wrong_top1_and_missing_rationale_both_fail(self):
        case = {'id': 'bad', 'input': {}, 'expect': {'top1': '另一种标签'}}

        row = check_case(case, StubClassifier())

        failed = {item['name'] for item in row['checks'] if not item['pass']}
        assert failed == {'top1', 'rationale'}


def test_main_pool_command_prints_the_summary(tmp_path, monkeypatch, capsys):
    # 指向临时池：这条命令本身不碰网络，但默认路径会读 var/ 下的真实产物。
    monkeypatch.setattr('app.cli.ReviewPool', lambda: ReviewPool(path=tmp_path / 'pool.json'))

    assert main(['pool']) == 0

    printed = capsys.readouterr().out
    assert '回流池' in printed and '待审 0 条' in printed
