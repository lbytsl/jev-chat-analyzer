"""重试退避口径（core/retry.py）：线性增长、尊重 Retry-After、封顶、只往下抖。"""
from __future__ import annotations

import pytest

from app.core import retry
from app.core.retry import BASE_DELAY_SECONDS, MAX_DELAY_SECONDS


def test_grows_linearly_and_is_capped(monkeypatch):
    monkeypatch.setattr(retry, 'JITTER_RATIO', 0)
    assert retry.backoff_delay(0) == pytest.approx(BASE_DELAY_SECONDS)
    assert retry.backoff_delay(2) == pytest.approx(BASE_DELAY_SECONDS * 3)
    assert retry.backoff_delay(50) == pytest.approx(MAX_DELAY_SECONDS), '再多次失败也不能无限等'


def test_retry_after_wins_when_larger(monkeypatch):
    monkeypatch.setattr(retry, 'JITTER_RATIO', 0)
    assert retry.backoff_delay(0, 5.0) == pytest.approx(5.0), '上游说等多久就等多久'
    assert retry.backoff_delay(3, 0.1) == pytest.approx(BASE_DELAY_SECONDS * 4), '太小就按自己的来'
    assert retry.backoff_delay(0, 999) == pytest.approx(MAX_DELAY_SECONDS), '再大也要封顶'


def test_jitter_only_shrinks():
    """只往下抖：实际等待不会超过上限（否则封顶就形同虚设）。"""
    for attempt in range(6):
        without_jitter = min(BASE_DELAY_SECONDS * (attempt + 1), MAX_DELAY_SECONDS)
        assert 0 < retry.backoff_delay(attempt) <= without_jitter


def test_parse_retry_after_accepts_sane_values_only():
    """畸形的响应头不能把重试链路打断，一律当作「没给」。"""
    assert retry.parse_retry_after('3') == 3.0
    assert retry.parse_retry_after(' 2.5 ') == 2.5
    assert retry.parse_retry_after('0') == 0.0
    assert retry.parse_retry_after(None) is None
    assert retry.parse_retry_after('') is None
    assert retry.parse_retry_after('Wed, 21 Oct 2026 07:28:00 GMT') is None
    assert retry.parse_retry_after('-1') is None
