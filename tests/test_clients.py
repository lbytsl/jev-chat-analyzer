"""外部客户端（`app/clients/`）的离线测试：连接复用、分项超时、单次请求的总预算。

全部用假 httpx 客户端替掉共享客户端，不连网络、不烧额度。
"""
from __future__ import annotations

import httpx
import pytest

from app.clients import http as http_client
from app.clients import jev as jev_client
from app.core.exceptions import JevAPIError, JevConfigurationError, JevConnectionError


def jev_settings(**overrides):
    base = {'typesafe_api_key': 'sk-test', 'typesafe_default_model': 'jev-test',
            'typesafe_base_url': 'https://api.typesafe.ai'}
    base.update(overrides)
    return type('Settings', (), base)()


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text='', headers=None):
        self.status_code = status_code
        self._payload = {'answers': {}} if payload is None else payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeClient:
    """按顺序返回预设结果；只会剩最后一条时它会被反复使用（方便「一直失败」的用例）。"""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'body': json, 'timeout': timeout})
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClock:
    """假时钟：让「总预算」的测试不必真的睡满 5 秒（sleep 会推进 monotonic）。"""

    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def use_fake_client(monkeypatch, fake):
    """把 Jev 客户端用的共享客户端换成桩。"""
    monkeypatch.setattr(jev_client, 'shared_client', lambda name, **_: fake)
    return fake


class TestSharedClient:
    def test_same_name_yields_the_same_client(self):
        """连接池必须跨请求复用：每次新建客户端就等于每次重做一遍 TCP + TLS 握手。

        一条消息 2 次 Jev 调用、50 条就是 100 次请求，这个乘数不能白付。
        """
        first = http_client.shared_client('test-singleton')
        second = http_client.shared_client('test-singleton')
        assert first is second

    def test_jev_borrows_the_shared_client_by_name(self, monkeypatch):
        fake = FakeClient([FakeResponse()])
        names: list[str] = []

        def fake_shared(name, **_):
            names.append(name)
            return fake

        monkeypatch.setattr(jev_client, 'shared_client', fake_shared)
        jev_client.JevClient(settings=jev_settings()).decide({'message': '在吗'}, {'intent': {}})
        assert names == ['jev']


class TestTimeouts:
    def test_request_timeout_is_split_per_phase(self, monkeypatch):
        """connect 要短（建连失败不该干等 45 秒），read 才是单次读超时。"""
        fake = use_fake_client(monkeypatch, FakeClient([FakeResponse()]))
        jev_client.JevClient(settings=jev_settings()).decide({}, {})
        timeout = fake.calls[0]['timeout']
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == http_client.CONNECT_TIMEOUT
        assert timeout.read == jev_client.TIMEOUT_SECONDS

    def test_total_budget_stops_retrying(self, monkeypatch):
        """没有总预算时最坏是「45s × 4 次 + 退避 ≈ 3 分钟」，用户那边看不出与卡死的区别。

        退避是 1.2 / 2.4 / 3.6，5 秒预算只够跑前三次尝试（假时钟，不真的等）。
        """
        clock = FakeClock()
        monkeypatch.setattr(jev_client, 'time', clock)
        fake = use_fake_client(monkeypatch, FakeClient([httpx.ConnectError('boom')]))
        client = jev_client.JevClient(settings=jev_settings(), total_budget=5.0)

        with pytest.raises(JevConnectionError):
            client.decide({}, {})

        assert 0 < len(fake.calls) < jev_client.MAX_ATTEMPTS, '预算耗尽后不该跑满所有尝试'
        assert clock.now == pytest.approx(5.0), '预算是硬上限，退避也不能把它撑破'

    def test_read_timeout_shrinks_with_the_remaining_budget(self, monkeypatch):
        """读超时按剩余预算收窄：预算只剩 1.4s 时不该还挂着 45s 等一次读。"""
        clock = FakeClock()
        monkeypatch.setattr(jev_client, 'time', clock)
        fake = use_fake_client(monkeypatch, FakeClient([httpx.ConnectError('boom')]))
        client = jev_client.JevClient(settings=jev_settings(), total_budget=5.0)

        with pytest.raises(JevConnectionError):
            client.decide({}, {})

        reads = [call['timeout'].read for call in fake.calls]
        assert reads[0] == pytest.approx(5.0), '第一次尝试的预算是整个总预算'
        assert reads[-1] < reads[0], '后续尝试的读超时按剩余预算收窄'

    def test_expired_budget_makes_no_attempt(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(jev_client, 'time', clock)
        fake = use_fake_client(monkeypatch, FakeClient([FakeResponse()]))
        client = jev_client.JevClient(settings=jev_settings(), total_budget=0.0)
        with pytest.raises(JevConnectionError):
            client.decide({}, {})
        assert fake.calls == []


class BrokenJSONResponse(FakeResponse):
    """200，但 body 不是 JSON：既不能算成功，也不该悄悄吞掉。"""

    def json(self):
        raise ValueError('Expecting value: line 1 column 1')


class TestJevRetryPolicy:
    """重试口径逐条钉住：什么该重试、什么该立刻暴露、Retry-After 怎么用。"""

    def test_missing_key_never_sends_a_request(self, monkeypatch):
        fake = use_fake_client(monkeypatch, FakeClient([FakeResponse()]))
        client = jev_client.JevClient(settings=jev_settings(typesafe_api_key=''))
        with pytest.raises(JevConfigurationError, match='未配置'):
            client.decide({}, {})
        assert fake.calls == [], '没配置就别发请求'

    def test_403_is_retried_once_then_exposed(self, monkeypatch):
        """实测偶发 403：只额外重试一次，第二次仍是 403 就立刻暴露（不无效重放整批）。"""
        monkeypatch.setattr(jev_client, 'time', FakeClock())
        fake = use_fake_client(monkeypatch, FakeClient([
            FakeResponse(403, text='forbidden'), FakeResponse(403, text='forbidden')]))

        with pytest.raises(JevAPIError) as info:
            jev_client.JevClient(settings=jev_settings()).decide({}, {})

        assert info.value.status == 403
        assert len(fake.calls) == 2

    def test_429_is_retried_and_retry_after_is_honoured(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(jev_client, 'time', clock)
        fake = use_fake_client(monkeypatch, FakeClient([
            FakeResponse(429, text='slow down', headers={'Retry-After': '5'}), FakeResponse()]))

        assert jev_client.JevClient(settings=jev_settings()).decide({}, {}) == {'answers': {}}
        assert 4.0 <= clock.slept[0] <= 5.0, '尊重上游给的 Retry-After（抖动只往下）'
        assert len(fake.calls) == 2

    def test_5xx_is_retried(self, monkeypatch):
        monkeypatch.setattr(jev_client, 'time', FakeClock())
        fake = use_fake_client(monkeypatch, FakeClient([
            FakeResponse(503, text='unavailable'), FakeResponse()]))

        assert jev_client.JevClient(settings=jev_settings()).decide({}, {}) == {'answers': {}}
        assert len(fake.calls) == 2

    def test_400_is_not_retried(self, monkeypatch):
        fake = use_fake_client(monkeypatch, FakeClient([FakeResponse(400, text='bad request')]))

        with pytest.raises(JevAPIError):
            jev_client.JevClient(settings=jev_settings()).decide({}, {})

        assert len(fake.calls) == 1

    def test_broken_json_is_not_treated_as_success(self, monkeypatch):
        fake = use_fake_client(monkeypatch, FakeClient([BrokenJSONResponse()]))

        with pytest.raises(JevAPIError):
            jev_client.JevClient(settings=jev_settings()).decide({}, {})

        assert len(fake.calls) == 1, '结构坏了不是连接问题，重试也没用'
