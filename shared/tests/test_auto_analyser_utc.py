"""
L-5 회귀 테스트: auto_analyser UTC 타임스탬프 변환 정확성

과거 `time.mktime(struct_time) - time.timezone` 은 로컬 TZ/DST 에 따라 어긋났다.
`calendar.timegm(struct_time)` 는 struct_time 을 UTC 로 해석하여 로컬 TZ 무관하게 정확하다.
"""
import calendar
import os
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_calendar_timegm_is_utc_based():
    # 1970-01-01 00:00:00 UTC == epoch 0
    st = time.strptime("1970-01-01 00:00:00", "%Y-%m-%d %H:%M:%S")
    assert calendar.timegm(st) == 0
    # 1970-01-02 00:00:00 UTC == 86400
    st2 = time.strptime("1970-01-02 00:00:00", "%Y-%m-%d %H:%M:%S")
    assert calendar.timegm(st2) == 86400


def test_auto_analyser_uses_calendar_timegm():
    src = open(os.path.join(REPO_ROOT, "CTrade/core/auto_analyser.py"), encoding="utf-8").read()
    assert "calendar.timegm(struct_time)" in src
    assert "time.mktime(struct_time)" not in src
