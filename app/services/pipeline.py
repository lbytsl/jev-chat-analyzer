"""整段流水线：整段分析 / 追加新消息 / 补跑生成层。

四个用例共用同一套「解析 → 圈定解读对象 → 逐条分类」的骨架，区别只在：

- `analyze`   整段重跑，可按开关顺带跑生成层；
- `append`    复用已有结果，只对新消息跑 Jev（按需求「追加不自动跑 DeepSeek」）；
- `interpret` 完全不跑 Jev，只对已有结果补跑潜台词；
- `suggest`   完全不跑 Jev，只对已有结果补跑推荐回复。

潜台词与推荐回复是两个独立用例（独立端点、独立提示词、独立失败标记），
不再由一次调用同时产出。

批量并发压在 4（JEV_MAX_WORKERS）：Jev 有过偶发 403 的历史，一次放太多反而会触发限流
把整批拖垮；单条断连仍走末尾补跑，不影响其余消息。

注意：本类不保存请求级状态（原单文件版把 messages 挂在闭包里，这里改为逐层传参），
所以同一个实例可以安全地被并发请求共用。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from queue import Queue
from typing import Callable, Iterator

from app.core.config import (
    MAX_TARGETS,
    MAX_TRANSCRIPT_CHARS,
    VERSION,
    get_settings,
)
from app.core.exceptions import (
    InvalidRequest,
    JevConnectionError,
)
from app.core.executors import shared_pool
from app.core.logging import get_logger
from app.domain.labels import RELATIONSHIPS, LabelLibrary, get_label_library
from app.domain.transcript import parse_transcript
from app.services.classifier import ClassifierService
from app.services.generation import GenerationService
from app.services.review_pool import ReviewPool, confidence_flags

logger = get_logger('pipeline')

NO_SPEAKER_HINT = ('没有识别到说话人。可以整段粘微信里复制出来的记录（名字 ⏎ 时间 ⏎ 内容），'
                   '也可以自己标注：「我：…」「她：…」，或用真实名字「林潇：…」「周屿：…」。')
RETRY_PAUSE_SECONDS = 2
# 共享线程池的名字（进程级复用，见 core/executors.py）：分类与生成各一个。
JEV_POOL = 'jev'
GEN_POOL = 'gen'


def _cancelled(cancel: threading.Event | None) -> bool:
    """客户端是否已经走了（断开 SSE / 关页面）。None 表示这次调用不关心取消。"""
    return cancel is not None and cancel.is_set()


# 说话人在展示层叫「我 / 对方」，在内部与上游协议里是 me / other。转换只在这里定义一次，
# 不要各自写 `'我' if speaker == 'me' else '对方'`——两套写法漂移过一次，排查很费劲。
SPEAKER_ME = '我'
SPEAKER_OTHER = '对方'


def speaker_label(speaker: object) -> str:
    """内部码（me / other）→ 展示标签（我 / 对方）。"""
    return SPEAKER_ME if speaker == 'me' else SPEAKER_OTHER


def speaker_code(label: object) -> str:
    """展示标签 → 内部码（从既有分析结果反推说话人时用）。"""
    return 'me' if label == SPEAKER_ME else 'other'


def _message_by_index(messages: list[dict], index: int) -> dict:
    """按序号取消息。

    失败事件只带序号，而收尾（`_finish_analyze` → `_envelope`）要的是消息本身——
    序号都是自己发出去的，取不到说明流水线自身串了，直接抛比悄悄少一条更好查。
    """
    for message in messages:
        if message['index'] == index:
            return message
    raise JevConnectionError('内部错误：失败的序号 {} 不在本次消息里'.format(index))


def _ordered_events(events: Iterator[dict], indexes: list[int],
                    terminal_types: set[str]) -> Iterator[dict]:
    """并发任务照常运行，只把逐句预览和结果按聊天顺序交给界面。"""
    remaining = iter(indexes)
    current = next(remaining, None)
    known = set(indexes)
    buffered: dict[int, list[dict]] = {}
    for event in events:
        index = event.get('index')
        if current is None or index not in known:
            yield event
            continue
        buffered.setdefault(index, []).append(event)
        while current is not None and current in buffered:
            ready = buffered.pop(current)
            yield from ready
            if any(item['type'] in terminal_types for item in ready):
                current = next(remaining, None)
            else:
                break


def build_context(messages: list[dict], message: dict) -> str:
    """当前消息之前最近 5 条的上下文；带时间戳时额外标注当前消息时间。"""
    # index 从 1 起；当前消息在列表中的位置是 index - 1，所以五条前文的起点是 index - 6。
    prior = messages[max(0, message['index'] - 6):message['index'] - 1]
    context_lines = []
    for item in prior:
        if not item['label']:
            continue
        stamp = (' [' + item['timestamp'] + ']') if item.get('timestamp') else ''
        context_lines.append(item['label'] + stamp + '：' + item['text'])
    current_stamp = ('当前消息时间：' + message['timestamp'] + '\n') if message.get('timestamp') else ''
    return current_stamp + '\n'.join(context_lines)


class PipelineService:
    def __init__(self,
                 classifier: ClassifierService | None = None,
                 generation: GenerationService | None = None,
                 library: LabelLibrary | None = None,
                 pool: ReviewPool | None = None):
        self._library = library or get_label_library()
        self._classifier = classifier or ClassifierService(library=self._library)
        self._generation = generation or GenerationService()
        self._pool = pool or ReviewPool()

    # ---------- 公共骨架 ----------
    def _parse(self, transcript: str,
               me_label: str | None = None) -> tuple[list[dict], list[dict], str | None]:
        """解析 + 说话人校验，返回 (messages, speakers, me_label)。"""
        messages, speakers, me_label = parse_transcript(transcript, me_label)
        speakers = [s for s in speakers if s['count']]
        if not speakers:
            raise InvalidRequest(NO_SPEAKER_HINT)
        if me_label and not any(s['label'] == me_label for s in speakers):
            raise InvalidRequest('「' + str(me_label) + '」不在这段记录里。可选的说话人：'
                                 + '、'.join(s['label'] for s in speakers))
        return messages, speakers, me_label

    @staticmethod
    def _assert_labels_exist(read_labels: list[str], speakers: list[dict]) -> None:
        for label in read_labels:
            if not any(s['label'] == label for s in speakers):
                raise InvalidRequest('要解读的「' + str(label) + '」不在这段记录里。可选的说话人：'
                                     + '、'.join(s['label'] for s in speakers))

    def _classify_one(self, messages: list[dict], message: dict, relationship: str,
                      want_interpretation: bool = False, want_suggestions: bool = False,
                      record_pool: bool = False,
                      on_generation: Callable[[dict], None] | None = None) -> dict:
        """分类单条消息，包装成前端需要的形状（原 `work` / `jev_only`）。

        `on_generation` 给了就走流式生成（潜台词逐字 / 建议逐条推给前端），不给就一次性拿。
        """
        context = build_context(messages, message)
        result = self._classifier.classify(
            {'message': message['text'], 'context': context,
             'relationship': relationship, 'speaker': message['speaker']},
            want_interpretation=want_interpretation, want_suggestions=want_suggestions,
            on_generation=on_generation)
        # 置信度旁路：低分或平票的样本攒进回流池，供人工审后决定补哪些标签。
        flags = confidence_flags(result, self._library)
        result['low_confidence'] = flags['low']
        result['confidence_flags'] = flags
        if record_pool and flags['pool_worthy']:
            self._pool.record(relationship, {'message': message['text'], 'context': context,
                                             'speaker': message['speaker'],
                                             'model': result.get('model'),
                                             'prompt_version': result.get('prompt_version'),
                                             'label_version': result.get('label_version')}, flags)
        return {'index': message['index'], 'speaker': speaker_label(message['speaker']),
                'label': message['label'], 'message': message['text'], 'context': context, 'result': result}

    def _run_targets(self, messages: list[dict], targets: list[dict], relationship: str,
                     gen_flags: Callable[[dict], tuple],
                     record_pool: bool = False) -> tuple[list[dict], list[dict]]:
        """并发分类 + 断连补跑一轮；返回 (results, failed)，结果未排序。

        `gen_flags(message) -> (要不要潜台词, 要不要推荐回复)`，由调用方按勾选与消息位置决定。

        非流式入口（脚本 / 夹具用）：**把流式实现的收成结果**，不再维护第二套并发 + 补跑逻辑
        （原来这里和 `_run_targets_streaming` 是两份近似实现，改一处忘一处）。没有客户端断开
        的概念，所以不传 cancel。
        """
        results, failed = [], []
        for event in self._run_targets_streaming(messages, targets, relationship, gen_flags,
                                                record_pool=record_pool):
            if event['type'] == 'message':
                results.append(event['item'])
            elif event['type'] == 'failed':
                # 失败事件只带序号；这里还原成消息本身，和 _envelope 的口径一致。
                failed.append(_message_by_index(messages, event['index']))
        return results, failed

    @staticmethod
    def _reply_target(messages: list[dict], results: list[dict],
                      fallback: Callable[[dict], dict] | None) -> dict | None:
        """推荐回复始终锚定「全局最后一条消息」，而不是过滤后 targets 的最后一条。

        这样标题（不知道怎么回复 / 还想说点什么）和内容才不会错位。
        v008 起 reply_target 不再依赖 gen_suggestions，始终返回，否则前端会在没生成建议时
        错误回退到 analyses 最后一条（可能是对方的），导致「我」发的最后一句标题错位。
        """
        if not messages:
            return None
        last_global = messages[-1]
        last_item = next((r for r in results if r['index'] == last_global['index']), None)
        if last_item is None and fallback is not None:
            try:
                last_item = fallback(last_global)
            except JevConnectionError:
                last_item = None
        return last_item

    @staticmethod
    def _envelope(relationship: str, messages: list[dict], analyses: list[dict],
                  speakers: list[dict], me_label: str | None, read_labels: list[str],
                  others: list[dict], selves: list[dict], failed: list[dict],
                  gen_interpretation: bool, gen_suggestions: bool,
                  reply_target: dict | None) -> dict:
        other_labels = [s['label'] for s in speakers if s['role'] == 'other']
        return {'version': VERSION, 'relationship': relationship, 'messages': messages,
                'analyses': analyses,
                'count': len(analyses), 'count_other': len(others), 'count_me': len(selves),
                'failed_count': len(failed), 'failed_indexes': [item['index'] for item in failed],
                'include_me': bool(me_label) and me_label in read_labels,
                'speakers': speakers, 'me_label': me_label,
                'other_label': other_labels[0] if other_labels else None,
                'other_labels': other_labels, 'read_labels': list(read_labels),
                'gen_interpretation': gen_interpretation, 'gen_suggestions': gen_suggestions,
                'reply_target': reply_target}

    # ---------- 用例 1：整段分析 ----------
    def _analyze_setup(self, data: dict) -> dict:
        """整段分析的公共准备：校验 → 解析 → 圈定解读对象 → 生成开关。

        `analyze` 与 `analyze_stream` 原本各自抄了一遍（连注释都不完全一样），改一处忘一处。
        返回 dict 而不是长元组：调用方按名字取，将来加字段不用改所有解包点。
        """
        if not isinstance(data, dict) or not isinstance(data.get('transcript'), str):
            raise InvalidRequest('请粘贴聊天记录。')
        relationship = data.get('relationship')
        if relationship not in RELATIONSHIPS:
            raise InvalidRequest('请选择关系或场景。')
        transcript = data['transcript'].strip()
        if not transcript or len(transcript) > MAX_TRANSCRIPT_CHARS:
            raise InvalidRequest('聊天记录不能为空，且需控制在 {} 字以内。'.format(MAX_TRANSCRIPT_CHARS))

        messages, speakers, me_label = self._parse(transcript, data.get('me_label') or None)
        read_labels = self._resolve_read_labels(data, speakers, me_label)
        self._assert_labels_exist(read_labels, speakers)
        targets, others, selves = self._select_targets(messages, read_labels)

        # 由前端两个勾选框控制：默认仅 Jev 分类；勾选「生成潜台词」才给每条解读对象调一次潜台词；
        # 勾选「生成推荐回复」只对最后一个解读对象调一次（两条是各自独立的调用）。
        gen_interpretation = bool(data.get('gen_interpretation'))
        gen_suggestions = bool(data.get('gen_suggestions'))
        last_target_index = targets[-1]['index']

        def gen_flags(message):
            # 推荐回复始终针对「最后一个解读对象」，而不是「targets 里除最后一条之外的其它消息」：
            # 这样即使解读对象没勾选「我」，最后一条由我发出时标题和内容也能一致。
            return (gen_interpretation,
                    bool(gen_suggestions and message['index'] == last_target_index))

        return {'relationship': relationship, 'messages': messages, 'speakers': speakers,
                'me_label': me_label, 'read_labels': read_labels, 'targets': targets,
                'others': others, 'selves': selves,
                'gen_interpretation': gen_interpretation, 'gen_suggestions': gen_suggestions,
                'gen_flags': gen_flags}

    def _finish_analyze(self, setup: dict, results: list[dict], failed: list[dict]) -> dict:
        """收尾：整批失败即报连接错误 → 排序 → 定位 reply_target → 组装 envelope。

        `failed` 必须是**消息列表**（不是序号列表）：`_envelope` 按 `item['index']` 取序号，
        两条路径给的东西必须同型——曾经流式传的是序号，一遇「部分失败」就 TypeError，
        前端只看到「分析没有正常结束」而且不会落库。
        """
        messages = setup['messages']
        if not results and failed:
            # 整批都连不上：直接报连接失败，比回一堆空结果诚实。
            raise JevConnectionError('Jev 上游暂时连接不上')
        results.sort(key=lambda item: item['index'])
        gen_suggestions = setup['gen_suggestions']
        # 全局最后一条可能不在解读对象里（例如只解读对方、最后一条是我发的）：它不进 analyses，
        # 但要作为 reply_target 存在，好让底部回复面板有锚点。它只跑推荐回复，不给潜台词。
        reply_target = self._reply_target(
            messages, results,
            fallback=lambda msg: self._classify_one(messages, msg, setup['relationship'],
                                                    want_interpretation=False,
                                                    want_suggestions=gen_suggestions,
                                                    record_pool=True))
        return self._envelope(setup['relationship'], messages, results, setup['speakers'],
                              setup['me_label'], setup['read_labels'], setup['others'],
                              setup['selves'], failed, setup['gen_interpretation'],
                              gen_suggestions, reply_target)

    def analyze(self, data: dict) -> dict:
        """非流式整段分析（脚本 / 夹具用）：跑完流式实现再取收尾结果。"""
        setup = self._analyze_setup(data)
        results, failed = self._run_targets(setup['messages'], setup['targets'],
                                            setup['relationship'], setup['gen_flags'],
                                            record_pool=True)
        return self._finish_analyze(setup, results, failed)

    def analyze_stream(self, data: dict,
                       cancel: threading.Event | None = None) -> Iterator[dict]:
        """整段分析的流式实现：并发计算，逐句预览与结果按聊天顺序推出去。

        事件序列：`start` →（`delta` / `item` / `reset` / `message` / `retrying` / `failed`）*
        → `done`。校验失败会直接抛（生成器还没 yield 过），由路由转成 `error` 事件。

        为什么值得做：Jev 分类每条要 2 次调用（实测 13-15s/条），而它的私有协议不支持流式，
        所以「等整批」才是主要体感；把每条结果尽早送出去，比逐字打字更管用。

        `cancel` 置位（客户端把页面关了 / 切走了）时停止提交剩下的消息，也不落库、不发 `done`
        ——半截结果没人要，为它白写一条会话更糟。
        """
        setup = self._analyze_setup(data)
        yield {'type': 'start', 'relationship': setup['relationship'],
               'messages': setup['messages'], 'speakers': setup['speakers'],
               'me_label': setup['me_label'], 'read_labels': list(setup['read_labels']),
               'total': len(setup['targets']),
               'gen_interpretation': setup['gen_interpretation'],
               'gen_suggestions': setup['gen_suggestions']}

        messages = setup['messages']
        results, failed = [], []
        raw_events = self._run_targets_streaming(messages, setup['targets'],
                                                 setup['relationship'], setup['gen_flags'],
                                                 cancel=cancel)
        indexes = [item['index'] for item in setup['targets']]
        for event in _ordered_events(raw_events, indexes, {'message', 'failed'}):
            if event['type'] == 'message':
                results.append(event['item'])
            elif event['type'] == 'failed':
                failed.append(_message_by_index(messages, event['index']))
            yield event
        if _cancelled(cancel):
            # 客户端已经走了：不落库、不发 done（半截结果没人看）。
            logger.info('整段分析被取消，已完成的 %s 条不落库', len(results))
            return
        yield {'type': 'done', 'data': self._finish_analyze(setup, results, failed)}

    def _run_targets_streaming(self, messages: list[dict], targets: list[dict], relationship: str,
                               gen_flags: Callable[[dict], tuple],
                               cancel: threading.Event | None = None,
                               record_pool: bool = True) -> Iterator[dict]:
        """并发分类 + 断连补跑，按完成顺序 yield 事件（生成层预览事件原样透传）。

        - `{'type': 'message', 'item': {...}}` 这一条算完了；
        - `{'type': 'retrying', 'index': i}` 这条断连了，稍后补跑（前端保持「生成中」）；
        - `{'type': 'failed', 'index': i}` 补跑后仍失败。

        业务错误（上游 401/400、返回结构不兼容等）不走事件：原地抛出，由 sse_response
        统一转成 `error` 事件——事件计数必须守恒，见 work() 里的注释。
        """
        workers = max(1, get_settings().jev_max_workers)
        queue: Queue = Queue()
        pool = shared_pool(JEV_POOL, workers)

        def work(message):
            want_interpretation, want_suggestions = gen_flags(message)
            index = message['index']

            def emit(event):
                queue.put({**event, 'index': index})

            try:
                item = self._classify_one(messages, message, relationship,
                                          want_interpretation=want_interpretation,
                                          want_suggestions=want_suggestions,
                                          record_pool=record_pool,
                                          on_generation=emit)
            except JevConnectionError:
                queue.put({'type': 'failed', 'index': index})
            except Exception as exc:  # noqa: BLE001 —— 主循环靠事件计数，绝不能有任务静默消失
                # 业务错误（401 / 400 / 返回结构不兼容…）：必须把异常交回主循环重新抛出。
                # 否则这个任务既不产出 message 也不产出 failed，主循环会一直等一个永远不会
                # 到来的事件——请求永久挂住：前端一直「生成中」，也不会有 done，自然不落库。
                queue.put({'type': 'error', 'index': index, 'exc': exc})
            else:
                queue.put({'type': 'message', 'item': item, 'index': index})

        def drain(batch, final: bool):
            """跑一批，按完成顺序 yield 事件；返回其中失败的消息（供上层补跑）。

            刻意「边完成边补位」而不是一次性把整批 submit 进去：一次提交完的话，客户端断开
            时剩下几十条早就排好队了，取消就没意义（照样全跑完）。窗口大小 = workers。
            """
            trouble = []
            pending = deque(batch)
            in_flight = 0
            while pending or in_flight:
                if _cancelled(cancel):
                    logger.info('客户端已断开，剩下的 %s 条不再提交', len(pending))
                    return trouble
                while pending and in_flight < workers:
                    pool.submit(work, pending.popleft())
                    in_flight += 1
                event = queue.get()
                if event['type'] == 'error':
                    # 交给上层：sse_response 会把它转成一条 error 事件（HTTP 早已是 200）
                    raise event['exc']
                if event['type'] == 'failed':
                    in_flight -= 1
                    trouble.append(next(m for m in batch if m['index'] == event['index']))
                    if not final:
                        yield {'type': 'retrying', 'index': event['index']}
                        continue
                elif event['type'] == 'message':
                    in_flight -= 1
                yield event
            return trouble

        if not targets:
            return
        trouble = yield from drain(list(targets), final=False)
        if trouble and not _cancelled(cancel):
            time.sleep(RETRY_PAUSE_SECONDS)
            yield from drain(trouble, final=True)

    @staticmethod
    def _resolve_read_labels(data: dict, speakers: list[dict],
                             me_label: str | None) -> list[str]:
        """解读对象来自前端两步选择：read_labels 是被勾选的说话人列表（可含「我」）。

        旧契约 include_me=true 退化为「全部对方 + 我」，保证夹具与旧脚本仍能跑。
        """
        raw_read = data.get('read_labels')
        if raw_read is not None:
            if not isinstance(raw_read, list):
                raise InvalidRequest('read_labels 必须是说话人名字的列表。')
            return raw_read
        read_labels = [s['label'] for s in speakers if s['role'] == 'other']
        if data.get('include_me') is True and me_label:
            read_labels.append(me_label)
        if not read_labels:
            # 只有一个说话人时（前端没给出 read_labels），能解读的就是他本人。
            read_labels = [s['label'] for s in speakers]
        return read_labels

    @staticmethod
    def _select_targets(messages: list[dict],
                        read_labels: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
        targets_all = [m for m in messages if m['label'] in read_labels]
        if not targets_all:
            raise InvalidRequest('没有选中要解读的消息。请在第 2 步勾选至少一个说话人。')
        if len(targets_all) > MAX_TARGETS:
            raise InvalidRequest('第一版最多分析 {} 条消息，当前选中 {} 条。'
                                 .format(MAX_TARGETS, len(targets_all)))
        others = [m for m in targets_all if m['speaker'] == 'other']
        selves = [m for m in targets_all if m['speaker'] == 'me']
        return sorted(targets_all, key=lambda item: item['index']), others, selves

    # ---------- 用例 2：补跑生成层（潜台词 / 推荐回复 各自独立） ----------
    def interpret(self, data: dict) -> dict:
        """只补跑潜台词：默认覆盖全部已有分析结果，可用 indexes 指定几条。"""
        return self._augment(data, kind='interpretation')

    def interpret_stream(self, data: dict,
                         cancel: threading.Event | None = None) -> Iterator[dict]:
        """潜台词补跑的流式版本（`/interpret-chat/stream` 用）。"""
        return self._augment_stream(data, kind='interpretation', cancel=cancel)

    def suggest(self, data: dict) -> dict:
        """只补跑推荐回复：默认只跑全局最后一条，可用 indexes 指定几条。

        指定 indexes 时不再受「只跑最后一条」的限制——给中间某条单独生成回复建议是合法用法
        （右键菜单就是这么用的）。
        """
        return self._augment(data, kind='suggestions')

    def suggest_stream(self, data: dict,
                       cancel: threading.Event | None = None) -> Iterator[dict]:
        """推荐回复补跑的流式版本（`/suggest-chat/stream` 用）。"""
        return self._augment_stream(data, kind='suggestions', cancel=cancel)

    def _augment(self, data: dict, kind: str) -> dict:
        """非流式入口：把流式实现跑到底，取最后一个 `done` 事件。

        两条路径共用同一份实现，避免「流式和一次性拿」的结果不一致。
        """
        done = None
        for event in self._augment_stream(data, kind=kind):
            if event['type'] == 'done':
                done = event
        if done is None:  # 理论上不可达：生成器一定会以 done 收尾
            raise InvalidRequest('生成任务异常结束，请重试。')
        return {key: value for key, value in done.items() if key != 'type'}

    def _augment_stream(self, data: dict, kind: str,
                        cancel: threading.Event | None = None) -> Iterator[dict]:
        """补跑生成层的流式实现，yield 的事件序列：

        `start` →（`delta` / `item` / `reset` / `result`）* → `done`。

        - `start` 带上这次要跑哪些序号，前端可以先把这些条标成「生成中」；
        - `delta` / `item` 是预览（潜台词逐字、建议逐条），最终结果**只**认 `done`；
        - `result` 只表示这一句已生成完，方便界面及时撤掉加载动画；
        - `done` 里是完整的 augmentations / failed_indexes，路由用它去合并会话。

        `cancel` 置位（客户端断开）时停止提交剩下的任务，也不发 `done`——路由就不会去
        合并一个半截结果。
        """
        prev, relationship = self._augment_prev(data)
        analyses = prev['analyses']
        messages = prev.get('messages') or []
        wanted = self._augment_indexes(data)
        is_interpretation = kind == 'interpretation'
        # 推荐回复必须锚定全局最后一条消息（与整段分析保持一致）。
        if messages:
            last_index = int(messages[-1].get('index', 0))
        else:
            last_index = max(int(a.get('index', 0)) for a in analyses) if analyses else 0

        jobs = []
        for analysis in analyses:
            index = int(analysis.get('index', 0))
            if wanted is not None:
                if index not in wanted:
                    continue
            elif not is_interpretation and index != last_index:
                continue
            job = self._augment_job(index, analysis)
            if job:
                jobs.append(job)

        # 全局最后一条不在 analyses 里（比如解读对象没勾选「我」）：推荐回复仍要能给它生成，
        # 结果按同一 index 回传，由前端合并回 reply_target。
        # 单句模式（wanted）下只有明确点名这条时才做，别顺手多生成一条。
        if (not is_interpretation and messages and (wanted is None or last_index in wanted)
                and not any(int(a.get('index', 0)) == last_index for a in analyses)):
            job = self._augment_job(last_index, prev.get('reply_target') or {})
            if job:
                jobs.append(job)

        jobs.sort(key=lambda job: job[0])
        yield {'type': 'start', 'kind': kind, 'relationship': relationship,
               'indexes': [job[0] for job in jobs], 'last_index': last_index}
        augmentations, failed_indexes = {}, []
        raw_events = self._run_generation_jobs(jobs, relationship, kind, cancel=cancel)
        for event in _ordered_events(raw_events, [job[0] for job in jobs], {'result'}):
            if event['type'] != 'result':
                yield event
                continue
            index = event['index']
            augmentations[str(index)] = event['augmentation']
            if event['augmentation'].get('gen_failed'):
                failed_indexes.append(index)
            yield {'type': 'result', 'index': index, 'kind': kind}
        if _cancelled(cancel):
            logger.info('%s 补跑被取消，%s 条结果不合并', kind, len(augmentations))
            return
        yield {'type': 'done', 'kind': kind, 'version': VERSION, 'relationship': relationship,
               'augmentations': augmentations, 'failed_indexes': failed_indexes,
               'last_index': last_index}

    def _run_generation_jobs(self, jobs: list[tuple], relationship: str, kind: str,
                             cancel: threading.Event | None = None) -> Iterator[dict]:
        """并发跑生成任务，按事件真实到达顺序 yield。

        - 预览事件：`{'type': 'delta'|'item'|'reset', 'index': i, ...}`
        - 结果事件：`{'type': 'result', 'index': i, 'augmentation': {...}}`（成功或失败都有）

        工作线程只往队列里塞事件，主线程按到达顺序取——SSE 才能边生成边推，而不是等整批跑完。
        与分类一样按滑动窗口提交：`cancel` 置位时剩下的任务不再进池。
        """
        if not jobs:
            return
        runner = self._generate_interpretation if kind == 'interpretation' else self._generate_suggestions
        workers = max(1, get_settings().gen_max_workers)
        queue: Queue = Queue()
        pool = shared_pool(GEN_POOL, workers)

        def work(job):
            index = job[0]

            def partial(event_index, event):
                # 生成层的回调签名是 partial(index, event)：事件里始终带上消息序号，
                # 前端才能把预览落到对应的那条消息上。
                queue.put({**event, 'index': event_index})

            try:
                _, augmentation = runner(relationship, job, partial=partial)
            except Exception as exc:  # 兜底：工作线程绝不能静默退出，否则主循环会一直等
                logger.warning('生成任务异常：%s', exc)
                augmentation = {'gen_failed': True, 'gen_error': str(exc)}
            queue.put({'type': 'result', 'index': index, 'augmentation': augmentation})

        pending = deque(jobs)
        in_flight = 0
        while pending or in_flight:
            if _cancelled(cancel):
                logger.info('客户端已断开，生成层剩下的 %s 条不再提交', len(pending))
                return
            while pending and in_flight < workers:
                pool.submit(work, pending.popleft())
                in_flight += 1
            event = queue.get()
            if event['type'] == 'result':
                in_flight -= 1
            yield event

    @staticmethod
    def _augment_prev(data: dict) -> tuple[dict, str]:
        """补跑类请求的公共校验：必须有上一次的分析结果，且关系类型有效。"""
        if not isinstance(data, dict):
            raise InvalidRequest('请求格式不正确。')
        prev = data.get('prev')
        if not isinstance(prev, dict) or not isinstance(prev.get('analyses'), list) or not prev.get('analyses'):
            raise InvalidRequest('缺少上一次分析结果，请先做一次仅Jev分析。')
        relationship = prev.get('relationship')
        if relationship not in RELATIONSHIPS:
            raise InvalidRequest('上一次分析的关系类型无效，请重新仅Jev分析。')
        return prev, relationship

    @staticmethod
    def _augment_indexes(data: dict) -> set[int] | None:
        """指定序号 = 单条模式；不传 = 默认范围（潜台词全体 / 推荐回复最后一条）。"""
        if data.get('indexes') is None:
            return None
        wanted = {int(item) for item in (data.get('indexes') or [])}
        if not wanted:
            raise InvalidRequest('请指定要生成的消息序号。')
        return wanted

    @staticmethod
    def _augment_job(index: int, analysis: dict) -> tuple | None:
        """够跑生成层的条件：这条之前拿到过 Jev 结果（连接失败被排除的跳过，可重跑 Jev 补）。"""
        result = analysis.get('result') or {}
        intent_result = result.get('primary_intent')
        emotion_result = result.get('emotion')
        if not isinstance(intent_result, dict) or not isinstance(emotion_result, dict):
            return None
        return (index, analysis.get('context', ''), analysis.get('message', ''),
                speaker_code(analysis.get('speaker')), intent_result, emotion_result,
                result.get('response_need') or {})

    def _generate_interpretation(self, relationship: str, job: tuple,
                                 partial: Callable[..., None] | None = None) -> tuple[int, dict]:
        """潜台词单条调用，失败只标记这一条，不影响其他条。

        `partial` 是流式预览回调（`partial(index, event)`），只有走流式端点时才有。
        """
        index, context, message, speaker, intent_result, emotion_result, _response_need = job
        on_event = (lambda event: partial(index, event)) if partial else None
        outcome = self._generation.run_interpretation(
            relationship, context, message, speaker, intent_result, emotion_result,
            on_event=on_event)
        if not outcome.ok:
            # 补跑路径的失败标记一律用 gen_failed 作为**传输字段**：会话层的
            # merge_augmentations 会按 kind 把它翻成 interpretation_failed / gen_failed
            # （两边的语义不同，别在这里直接写 interpretation_failed，那样合并不认）。
            return index, {'gen_failed': True, 'gen_error': outcome.error}
        # 只带潜台词字段：前端按「字段是否存在」合并，不会误清另一边的结果。
        return index, dict(outcome.content.to_dict(), gen_failed=False, gen_error=None)

    def _generate_suggestions(self, relationship: str, job: tuple,
                              partial: Callable[..., None] | None = None) -> tuple[int, dict]:
        """推荐回复单条调用，失败只标记这一条，不影响其他条。"""
        index, context, message, speaker, intent_result, emotion_result, response_need = job
        on_event = (lambda event: partial(index, event)) if partial else None
        outcome = self._generation.run_suggestions(
            relationship, context, message, speaker, intent_result, emotion_result,
            on_event=on_event, response_need_result=response_need)
        if not outcome.ok:
            return index, {'gen_failed': True, 'gen_error': outcome.error}
        return index, {'suggestions': outcome.content.suggestions,
                       'gen_failed': False, 'gen_error': None}

    # ---------- 用例 3：追加新消息 ----------
    def append(self, data: dict) -> dict:
        """在已有分析结果上追加新消息：只对新消息跑 Jev 分类，旧结果原样保留。

        前端把整段「已分析文本 + 新增尾部」拼好后作为 transcript 传过来（旧文本在前、新消息在后），
        同时回传上一次的完整结果 prev。后端重解析整段，对「序号在 old_count 之内且 prev 已有分析」的
        消息直接复用旧结果（含已生成的 AI 解读/回复），只对超出 old_count 的新消息跑 Jev；
        按需求「追加不自动跑 DeepSeek」，所以新消息只做意图/情绪分类，不调大模型。
        """
        if not isinstance(data, dict):
            raise InvalidRequest('请求格式不正确。')
        prev = data.get('prev')
        if not isinstance(prev, dict) or not isinstance(prev.get('messages'), list) or not prev.get('messages'):
            raise InvalidRequest('缺少上一次分析结果，请先做一次仅Jev分析。')
        relationship = prev.get('relationship')
        if relationship not in RELATIONSHIPS:
            raise InvalidRequest('上一次分析的关系类型无效，请重新仅Jev分析。')
        transcript = data.get('transcript')
        if not isinstance(transcript, str) or not transcript.strip():
            raise InvalidRequest('请粘贴新增的聊天消息。')
        if len(transcript) > MAX_TRANSCRIPT_CHARS:
            raise InvalidRequest('聊天记录需控制在 {} 字以内。'.format(MAX_TRANSCRIPT_CHARS))

        old_count = int(data.get('old_count') or len(prev['messages']))
        read_labels = prev.get('read_labels') or []
        # 重解析整段：前缀（旧消息）与上次完全一致 → 序号 1..old_count 不变；新尾部追加在后。
        messages, speakers, me_label = self._parse(transcript, prev.get('me_label'))
        self._assert_labels_exist(read_labels, speakers)
        targets, others, selves = self._select_targets(messages, read_labels)
        prev_map = {int(a['index']): a for a in (prev.get('analyses') or [])
                    if isinstance(a.get('index'), int)}
        gen_interpretation = bool(prev.get('gen_interpretation'))
        gen_suggestions = bool(prev.get('gen_suggestions'))

        analyses, failed = [], []
        for message in targets:
            idx = message['index']
            # 旧消息且已有分析 → 直接复用（保留其中可能已生成的 AI 解读/回复），不重跑、不重算。
            if idx <= old_count and idx in prev_map:
                analyses.append(prev_map[idx])
                continue
            try:
                analyses.append(self._classify_one(messages, message, relationship))
            except JevConnectionError:
                failed.append(message)
        if failed:
            time.sleep(RETRY_PAUSE_SECONDS)
            retry_failed = []
            for message in failed:
                try:
                    analyses.append(self._classify_one(messages, message, relationship))
                except JevConnectionError:
                    retry_failed.append(message)
            failed = retry_failed
        analyses.sort(key=lambda item: item['index'])

        # 推荐回复始终锚定「全局最后一条消息」，并补做 Jev 分类（不调 AI）：旧消息复用、新消息现算。
        reply_target = None
        if messages:
            last_global = messages[-1]
            item = next((a for a in analyses if a['index'] == last_global['index']), None)
            if item is None and last_global['index'] <= old_count and last_global['index'] in prev_map:
                item = prev_map[last_global['index']]
            if item is None:
                try:
                    item = self._classify_one(messages, last_global, relationship)
                except JevConnectionError:
                    item = None
            reply_target = item
        return self._envelope(relationship, messages, analyses, speakers, me_label, read_labels,
                              others, selves, failed, gen_interpretation, gen_suggestions,
                              reply_target)
