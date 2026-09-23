"""命令行入口：`python -m app serve | check | pool`。

替代旧版的 `python3 server.py [--check|--pool]`：

- serve  起 uvicorn（等价于旧版无参数运行）
- check  跑 data/cases.json 夹具回归，结果写 var/regression/test-results.json
- pool   查看低置信度回流池里待人工审的样本
"""
from __future__ import annotations

import argparse
import json
import sys

from app.core.config import CASES_PATH, HOST, PORT, REGRESSION_DIR, VERSION
from app.core.logging import get_logger
from app.services.classifier import ClassifierService
from app.services.review_pool import ReviewPool

logger = get_logger('cli')


def serve(host: str = HOST, port: int = PORT, reload: bool = False) -> None:
    import uvicorn

    print('恋爱职场大侦探 {} 启动中 → http://{}:{}   （Ctrl+C 停止）'.format(VERSION, host, port))
    print('接口文档：http://{}:{}/docs'.format(host, port))
    uvicorn.run('app.main:app', host=host, port=port, reload=reload, log_level='info')


def check_case(case: dict, classifier: ClassifierService) -> dict:
    """判一条夹具，逐项报告结果。

    二期断言基于「标签 + 相对差距」，不依赖固定百分比：
      top1      —— 排第一的意图标签（判别力最强，优先靠它表达意图）
      emotion   —— 情绪标签
      min_score —— top1 分数下限（粗护栏，±0.05 波动，只当护栏）
      max_others—— 除 top1 外所有意图分数上限（负断言：不该被带起的意图必须压住）
      max_scores—— 按意图标签分别设上限（负断言，0.1 量级最可靠）
      margin_min/max —— top1 与第二名差距（表达确定 or 该犹豫）
      rationale —— 人工依据，缺即判失败
    """
    expect = case.get('expect') or {}
    result = classifier.classify_payload(case['input'])
    primary = result['primary_intent']
    emotion = result.get('emotion') or {}
    intent_ranked = primary.get('ranked', [])
    top1 = {'key': primary['key'], 'label': primary['label'], 'score': primary['score']}
    second = intent_ranked[1] if len(intent_ranked) > 1 else {'key': None, 'score': 0.0}
    margin = round(top1['score'] - second['score'], 4)
    others = [item for item in intent_ranked if item['label'] != expect.get('top1')]
    worst = max(others, key=lambda item: item['score']) if others else None
    checks = []

    def add(name, ok, **extra):
        checks.append(dict({'name': name, 'pass': bool(ok)}, **extra))

    if 'top1' in expect:
        add('top1', top1['key'] == expect['top1'], expected=expect['top1'], actual=top1['key'])
    if 'emotion' in expect:
        add('emotion', emotion.get('key') == expect['emotion'], expected=expect['emotion'],
            actual=emotion.get('key'))
    if 'min_score' in expect:
        add('min_score', top1['score'] >= expect['min_score'], limit=expect['min_score'],
            actual=round(top1['score'], 3))
    if 'max_others' in expect and worst is not None:
        add('max_others', worst['score'] <= expect['max_others'], limit=expect['max_others'],
            actual=round(worst['score'], 3), actual_key=worst['key'])
    for capped, limit in sorted((expect.get('max_scores') or {}).items()):
        actual = next((item['score'] for item in intent_ranked if item['label'] == capped), None)
        add('max_scores.' + capped, actual is not None and actual <= limit, limit=limit,
            actual=None if actual is None else round(actual, 3))
    if 'margin_min' in expect:
        add('margin_min', margin >= expect['margin_min'], limit=expect['margin_min'], actual=margin)
    if 'margin_max' in expect:
        add('margin_max', margin <= expect['margin_max'], limit=expect['margin_max'], actual=margin)
    add('rationale', bool(expect.get('rationale')), actual=expect.get('rationale', '')[:40])
    return {'id': case['id'], 'passed': all(item['pass'] for item in checks), 'margin': margin,
            'top1': {'key': top1['key'], 'label': top1['label'], 'score': round(top1['score'], 3)},
            'intent_ranking': [{'key': item['label'], 'label': item['label'],
                                'score': round(item['score'], 3)} for item in intent_ranked],
            'primary_intent': primary, 'emotion': emotion, 'checks': checks,
            'model': result['model'], 'elapsed_ms': result['elapsed_ms'], 'usage': result['usage'],
            'version': result['version'], 'input': result['input']}


def run_check() -> int:
    raw = json.loads(CASES_PATH.read_text(encoding='utf-8'))
    cases = raw['cases'] if isinstance(raw, dict) else raw
    classifier = ClassifierService()
    rows = []
    for case in cases:
        try:
            row = check_case(case, classifier)
        except Exception as exc:  # noqa: BLE001 —— 单条异常不该终止整轮回归
            rows.append({'id': case['id'], 'passed': False,
                         'error': type(exc).__name__ + ': ' + str(exc)})
            print(case['id'], 'ERROR', type(exc).__name__, str(exc), flush=True)
            continue
        rows.append(row)
        failed = ','.join(item['name'] for item in row['checks'] if not item['pass'])
        ranking = ' '.join(item['label'] + ' ' + format(item['score'], '.2f')
                           for item in row['intent_ranking'][:3])
        print(case['id'], 'pass' if row['passed'] else 'FAIL ' + failed,
              'margin=' + format(row['margin'], '.2f'), '|', ranking, flush=True)
    tally = {}
    for row in rows:
        for item in row.get('checks', []):
            bucket = tally.setdefault(item['name'].split('.')[0], [0, 0])
            bucket[0 if item['pass'] else 1] += 1
    if tally:
        print('', flush=True)
        for name in tally:
            ok, bad = tally[name]
            print(format(name, '-<11'), str(ok) + '/' + str(ok + bad), flush=True)
    passed_total = sum(1 for row in rows if row.get('passed') is True)
    print('', flush=True)
    print('cases', str(passed_total) + '/' + str(len(rows)), 'passed', flush=True)
    REGRESSION_DIR.mkdir(parents=True, exist_ok=True)
    destination = REGRESSION_DIR / 'test-results.json'
    destination.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print('结果写入', destination, flush=True)
    return 0 if rows and passed_total == len(rows) else 1


def show_pool() -> int:
    pool_service = ReviewPool()
    pool = pool_service.load()
    pending = pool_service.pending(pool)
    print('回流池 ' + str(pool_service.path))
    print('共 ' + str(len(pool['entries'])) + ' 条，待审 ' + str(len(pending)) + ' 条', flush=True)
    for item in sorted(pending, key=lambda x: -int(x.get('hits', 1)))[:30]:
        print('  [' + str(item.get('trigger')) + ' | ' + str(item.get('relationship')) + ' x'
              + str(item.get('hits', 1)) + '] ' + str(item.get('message'))[:40] + ' → '
              + str(item['intent']['label']) + ' ' + format(item['intent']['score'], '.2f')
              + '（gap ' + format(item['intent']['gap'], '.2f') + '）| '
              + '；'.join(item.get('reasons', [])), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python -m app', description='恋爱职场大侦探 本地服务与工具')
    sub = parser.add_subparsers(dest='command')
    serve_parser = sub.add_parser('serve', help='启动本地服务（默认）')
    serve_parser.add_argument('--host', default=HOST)
    serve_parser.add_argument('--port', type=int, default=PORT)
    serve_parser.add_argument('--reload', action='store_true', help='改代码自动重启（开发用）')
    sub.add_parser('check', help='跑夹具回归（data/cases.json）')
    sub.add_parser('pool', help='查看低置信度回流池')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == 'check':
        return run_check()
    if args.command == 'pool':
        return show_pool()
    serve(getattr(args, 'host', HOST), getattr(args, 'port', PORT), getattr(args, 'reload', False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
