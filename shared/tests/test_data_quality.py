# -*- coding: utf-8 -*-
"""원천 데이터 품질 감사 — 합성 데이터로 각 검사가 '그 결함만' 잡는지, 정상 데이터는 통과시키는지 검증한다.

핵심 시나리오는 실제 사고(2026-09-24): 틱이 ~10%만 기록된 날, 시가가 전일 종가로 채워진 날, 이중 계상된 날.
"""
import numpy as np
import pandas as pd
import pytest

from shared.data_library import data_quality as dq
from shared.data_library.raw_store_health import _overlay_quality, _quality_view


def _minute_bars(days, start="09:01", n=30, base=100000.0, vol=1000.0):
    """1분봉(라벨=종료 시각): 봉 라벨 t 는 [t-1분, t) 체결을 담는다."""
    rows = []
    for d in days:
        ts = pd.date_range(f"{d} {start}", periods=n, freq="1min")
        px = base + np.arange(n) * 5.0
        rows.append(pd.DataFrame({"ts": ts, "open": px, "high": px + 10, "low": px - 10, "close": px + 5,
                                  "volume": vol + np.arange(n)}))
    return pd.concat(rows, ignore_index=True)


def _ticks_from_bars(bars, per_minute=10, keep=1.0, seed=0):
    """분봉을 틱으로 분해한다. 봉 volume 이 정확히 합쳐지도록 나눈다. keep<1 이면 그 비율만 남긴다(누락 시뮬레이션)."""
    rng = np.random.default_rng(seed)
    out = []
    for _, b in bars.iterrows():
        start = b["ts"] - pd.Timedelta(minutes=1)
        secs = np.sort(rng.integers(0, 60, per_minute))
        vols = np.full(per_minute, b["volume"] / per_minute)
        prices = np.linspace(b["low"], b["high"], per_minute)
        prices[-1] = b["close"]
        prices[0] = b["low"]
        out.append(pd.DataFrame({"ts": start + pd.to_timedelta(secs, unit="s"), "open": prices, "high": prices,
                                 "low": prices, "close": prices, "volume": vols, "cvd": rng.random(per_minute)}))
    t = pd.concat(out, ignore_index=True)
    if keep < 1.0:
        t = t.sample(frac=keep, random_state=seed).sort_values("ts")
    return t.reset_index(drop=True)


DAYS = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"]


def _audit(ticks, bars, kind="minute"):
    return dq.audit_frames(ticks, bars, "end", kind)


def test_healthy_ticks_pass_every_check():
    bars = _minute_bars(DAYS)
    res = _audit(_ticks_from_bars(bars), bars)
    assert {v["status"] for v in res.values()} == {"ok"}, {d: v["reasons"] for d, v in res.items()}


def test_sparse_day_is_caught_by_volume_and_minute_checks_but_not_its_neighbours():
    """실사고: 하루만 틱이 10%로 줄었다. 파일에는 그 날짜가 '있으므로' 날짜 존재 검사는 통과하지만 감사는 잡아야 한다."""
    bars = _minute_bars(DAYS)
    ticks = _ticks_from_bars(bars)
    day = pd.Timestamp("2026-08-05").date()
    is_day = ticks["ts"].dt.date == day
    ticks = pd.concat([ticks[~is_day], ticks[is_day].sample(frac=0.1, random_state=1)]).sort_values("ts").reset_index(drop=True)
    res = _audit(ticks, bars)
    assert res["2026-08-05"]["status"] == "bad"
    assert res["2026-08-05"]["checks"]["volume_ratio"]["grade"] == "bad"
    assert res["2026-08-05"]["checks"]["minute_mismatch"]["grade"] == "bad"
    assert [d for d, v in res.items() if v["status"] != "ok" and d != "2026-08-05"] == []


def test_time_gap_is_caught_by_minute_coverage_even_when_daily_volume_is_close():
    bars = _minute_bars(DAYS)
    ticks = _ticks_from_bars(bars)
    gap = (ticks["ts"] >= "2026-08-04 09:10:00") & (ticks["ts"] < "2026-08-04 09:20:00")   # 10분 공백
    res = _audit(ticks[~gap].reset_index(drop=True), bars)
    c = res["2026-08-04"]["checks"]
    assert c["minute_coverage"]["grade"] in ("warn", "bad") and c["minute_mismatch"]["grade"] == "bad"
    assert res["2026-08-04"]["status"] == "bad"


def test_price_filled_from_previous_close_is_caught_by_price_checks():
    """09-14 유형: 틱이 거의 없고 가격이 전일 종가로 채워진 날."""
    bars = _minute_bars(DAYS)
    ticks = _ticks_from_bars(bars)
    day = ticks["ts"].dt.date == pd.Timestamp("2026-08-06").date()
    ticks.loc[day, ["open", "high", "low", "close"]] = 90000.0      # 전일 종가 등 엉뚱한 값으로 채워짐
    res = _audit(ticks, bars)
    assert res["2026-08-06"]["checks"]["high_diff"]["grade"] == "bad" or res["2026-08-06"]["checks"]["close_diff"]["grade"] == "bad"
    assert res["2026-08-06"]["status"] == "bad"


def test_double_counting_is_caught():
    bars = _minute_bars(DAYS)
    ticks = _ticks_from_bars(bars)
    dup = ticks[ticks["ts"].dt.date == pd.Timestamp("2026-08-03").date()]
    res = _audit(pd.concat([ticks, dup]).sort_values("ts", kind="stable").reset_index(drop=True), bars)
    assert res["2026-08-03"]["status"] == "bad"
    assert res["2026-08-03"]["checks"]["volume_ratio"]["value"] == pytest.approx(2.0, rel=1e-3)


def test_identical_rows_are_not_a_defect_when_volume_reconciles():
    """실측 000660: 완전 중복 행 2%인데 거래량은 상위 원천과 정확히 일치 → 같은 초에 같은 가격·수량으로 체결된 실제 체결이다.
    (이것을 결함으로 세면 정상 파일 17일이 학습에서 제외되는 오탐이 난다)"""
    bars = _minute_bars(DAYS)
    ticks = _ticks_from_bars(bars)
    day = ticks[ticks["ts"].dt.date == pd.Timestamp("2026-08-04").date()]
    # 거래량 합이 유지되도록: 중복 행을 추가하되 원본 행의 수량을 그만큼 줄인다 (실제 체결이 두 줄로 찍힌 것과 동치)
    extra = day.iloc[:3].copy()
    ticks = ticks.copy()
    ticks.loc[extra.index, "volume"] = ticks.loc[extra.index, "volume"] / 2
    extra["volume"] = extra["volume"] / 2
    res = _audit(pd.concat([ticks, extra]).sort_values("ts", kind="stable").reset_index(drop=True), bars)
    c = res["2026-08-04"]["checks"]
    assert c["duplicate_share"]["value"] > 0 and c["duplicate_share"]["grade"] == "ok"
    assert res["2026-08-04"]["status"] == "ok"


def test_duplicates_that_inflate_volume_are_a_defect():
    bars = _minute_bars(DAYS)
    ticks = _ticks_from_bars(bars)
    day = ticks[ticks["ts"].dt.date == pd.Timestamp("2026-08-04").date()]
    res = _audit(pd.concat([ticks, day.iloc[:20]]).sort_values("ts", kind="stable").reset_index(drop=True), bars)   # 300행 중 20행 이중 계상
    assert res["2026-08-04"]["checks"]["duplicate_share"]["grade"] != "ok"
    assert res["2026-08-04"]["checks"]["volume_ratio"]["grade"] != "ok"


def test_duplicates_without_a_reference_cannot_be_excused():
    bars = _minute_bars(DAYS)
    ticks = _ticks_from_bars(bars)
    res = dq.audit_frames(pd.concat([ticks, ticks.iloc[:20]]).reset_index(drop=True), None)
    assert any(v["checks"]["duplicate_share"]["grade"] != "ok" for v in res.values())


def test_a_defective_reference_is_not_blamed_on_the_finer_source():
    """실측 122630/229200/0167A0: 1분봉 파일이 전체 이중 기록(거래량 2배)이어도 틱 파일은 그것 때문에 불량이 되면 안 된다."""
    bars = _minute_bars(DAYS)
    doubled = pd.concat([bars, bars.iloc[::-1]], ignore_index=True)        # 오름차순 + 내림차순으로 두 번 기록
    res = _audit(_ticks_from_bars(bars), doubled)
    assert {v["status"] for v in res.values()} == {"ok"}, {d: v["reasons"] for d, v in res.items()}
    own = dq.audit_frames(doubled, None)                                    # 결함은 그 원천 자신의 감사에서 잡힌다
    assert {v["status"] for v in own.values()} == {"bad"}


def test_backward_timestamps_and_bad_prices_are_structural_failures():
    bars = _minute_bars(DAYS)
    ticks = _ticks_from_bars(bars)
    ticks = pd.concat([ticks.iloc[len(ticks) // 2:], ticks.iloc[:len(ticks) // 2]]).reset_index(drop=True)   # 순서 뒤섞임
    res = _audit(ticks, bars)
    assert any(v["checks"]["backward_share"]["grade"] != "ok" for v in res.values())
    ticks2 = _ticks_from_bars(bars)
    ticks2.loc[5, "close"] = 0.0
    assert any(v["checks"]["bad_price"]["grade"] == "bad" for v in _audit(ticks2, bars).values())


def test_day_missing_inside_the_range_is_bad_but_outside_the_collected_range_is_not():
    """수집 범위 안의 빈 날은 불량, 수집 시작 이전/끝 이후의 상위 원천 날짜는 '수집 안 함'일 뿐 불량이 아니다."""
    bars = _minute_bars(["2026-08-02", *DAYS, "2026-08-10"])
    ticks = _ticks_from_bars(_minute_bars(DAYS))
    ticks = ticks[ticks["ts"].dt.date != pd.Timestamp("2026-08-05").date()]
    res = _audit(ticks.reset_index(drop=True), bars)
    assert res["2026-08-05"]["status"] == "bad" and res["2026-08-05"]["rows"] == 0
    assert "2026-08-02" not in res and "2026-08-10" not in res


def test_without_a_reference_days_are_unverifiable_not_ok():
    bars = _minute_bars(DAYS)
    res = dq.audit_frames(_ticks_from_bars(bars), None)
    assert {v["status"] for v in res.values()} == {"unverifiable"}


def test_daily_reference_compares_close_only_and_price_basis_mismatch_is_only_a_warning():
    """일봉은 정규장 밖 체결·수정주가가 섞인다 — 거래량/고저는 비교하지 않고, 종가 기준 차이는 경고로만 둔다."""
    bars = _minute_bars(DAYS)
    daily = bars.groupby(bars["ts"].dt.strftime("%Y-%m-%d")).agg(high=("high", "max"), low=("low", "min"),
                                                                    close=("close", "last"), volume=("volume", "sum")).reset_index()
    daily["ts"] = pd.to_datetime(daily["index"] if "index" in daily else daily["ts"])
    daily["volume"] *= 2.0                              # 시간외 체결이 더해진 일봉
    ok = dq.audit_frames(bars, daily, "end", "daily")
    assert {v["status"] for v in ok.values()} == {"ok"}
    adjusted = daily.assign(close=daily["close"] * 0.97)   # 수정주가 -3%
    res = dq.audit_frames(bars, adjusted, "end", "daily")
    assert {v["status"] for v in res.values()} == {"warn"}
    assert all("volume_ratio" not in v["checks"] for v in res.values())


def test_tick_to_bar_mapping_follows_the_creon_convention():
    """분봉 라벨 t = [t-1분, t). 09:00:00 시가 동시호가는 09:01 봉, 15:30:00 종가 동시호가는 (15:31 봉이 없으므로) 15:30 봉."""
    ref = pd.DatetimeIndex(pd.to_datetime(["2026-08-03 09:01:00", "2026-08-03 09:02:00", "2026-08-03 15:30:00"]))
    ts = pd.Series(pd.to_datetime(["2026-08-03 09:00:00", "2026-08-03 09:00:59", "2026-08-03 09:01:00", "2026-08-03 15:30:00"]))
    got = dq._bar_index_of_records(ts, ref, "end")
    assert list(got) == [ref[0], ref[0], ref[1], ref[2]]


def test_policy_change_invalidates_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(dq, "_CACHE_PATH", str(tmp_path / "c.json"))
    dq.DataQualityAuditor._mem, dq.DataQualityAuditor._loaded = {}, False
    bars = _minute_bars(DAYS)
    f, r = tmp_path / "t.csv", tmp_path / "m.csv"
    _ticks_from_bars(bars).rename(columns={"ts": "timestamp"}).to_csv(f, index=False)
    bars.rename(columns={"ts": "timestamp"}).to_csv(r, index=False)
    e1 = dq.DataQualityAuditor.audit_pair(str(f), str(r), log_fn=lambda m: None)
    assert dq.DataQualityAuditor.peek(str(f), str(r)) is not None
    assert dq.DataQualityAuditor.peek(str(f), str(r), dq.QualityPolicy(volume_ratio_dev=(0.5, 0.9))) is None
    # 원본이 바뀌면 캐시 무효
    _ticks_from_bars(bars, keep=0.5).rename(columns={"ts": "timestamp"}).to_csv(f, index=False)
    assert dq.DataQualityAuditor.peek(str(f), str(r)) is None
    assert e1["summary"]["bad"] == 0


# ── 소비자: 학습 게이트 ────────────────────────────────────────────────────────
def _frame(days):
    return pd.DataFrame({"timestamp": pd.concat([pd.Series(pd.date_range(f"{d} 09:00", periods=6, freq="10s")) for d in days],
                                                   ignore_index=True), "x": 1.0})


def test_gate_drops_only_bad_days_and_reports_reasons(monkeypatch):
    monkeypatch.setattr(dq, "unusable_days", lambda sym, sec, statuses=frozenset({"bad"}), **k: {"2026-08-04": ["[TICK] 거래량 합 0.1"]})
    logs = []
    df, rep = dq.apply_quality_gate(_frame(DAYS), "069500", 10.0, log_fn=logs.append)
    assert sorted(df["timestamp"].dt.strftime("%Y-%m-%d").unique()) == ["2026-08-03", "2026-08-05", "2026-08-06"]
    assert list(rep["excluded_days"]) == ["2026-08-04"] and rep["dropped_rows"] == 6
    assert any("2026-08-04" in m and "거래량" in m for m in logs)


def test_gate_disabled_or_failing_audit_never_blocks_training(monkeypatch):
    df0 = _frame(DAYS)
    out, rep = dq.apply_quality_gate(df0, "069500", 10.0, log_fn=lambda m: None, enabled=False)
    assert len(out) == len(df0) and not rep["excluded_days"]
    monkeypatch.setattr(dq, "unusable_days", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    logs = []
    out, rep = dq.apply_quality_gate(df0, "069500", 10.0, log_fn=logs.append)
    assert len(out) == len(df0) and "boom" in rep["error"] and any("감사 실패" in m for m in logs)   # 조용히 넘기지 않는다


def test_gate_warns_loudly_when_a_large_share_is_excluded(monkeypatch):
    monkeypatch.setattr(dq, "unusable_days", lambda *a, **k: {d: ["x"] for d in DAYS[:2]})
    logs = []
    dq.apply_quality_gate(_frame(DAYS), "069500", 10.0, log_fn=logs.append)
    assert any("원천 수집 상태를 먼저 점검" in m for m in logs)


def test_source_granularity_by_interval():
    assert dq.source_labels_for_interval(10) == ["TICK", "1M"]
    assert dq.source_labels_for_interval(60) == ["1M"]
    assert dq.source_labels_for_interval(900) == ["1M"]
    assert dq.source_labels_for_interval(86400) == ["1D"]


# ── 화면 연동: 날짜가 있어도 품질이 나쁘면 '정상 수집'으로 세지 않는다 ─────────────────────────
def test_slots_show_quality_over_presence_without_touching_the_cached_slots():
    slots = [{"date": "2026-08-03", "status": "found"}, {"date": "2026-08-04", "status": "found"},
             {"date": "2026-08-05", "status": "missing"}, {"date": "2026-08-06", "status": "holiday"}]
    days = {"2026-08-04": {"status": "bad", "reasons": ["거래량 합 0.1"]}, "2026-08-03": {"status": "warn", "reasons": ["중복"]},
            "2026-08-05": {"status": "bad", "reasons": []}}
    out = _overlay_quality(slots, days)
    assert [s["status"] for s in out] == ["suspect", "degraded", "missing", "holiday"]   # missing 은 그대로(이미 결측)
    assert out[1]["reasons"] == ["거래량 합 0.1"]
    assert [s["status"] for s in slots] == ["found", "found", "missing", "holiday"]      # 원본 불변


def test_quality_view_distinguishes_unchecked_from_healthy():
    assert _quality_view(None, [])["quality_checked"] is False and _quality_view(None, [])["quality_bad_count"] is None
    entry = {"days": {"2026-08-04": {"status": "bad", "reasons": ["r"]}},
             "summary": {"bad_days": ["2026-08-04"], "warn_days": []}}
    v = _quality_view(entry, [{"date": "2026-08-04", "status": "found"}])
    assert v["quality_checked"] and v["quality_bad_count"] == 1 and v["slots"][0]["status"] == "degraded"
    # 표시 창(최근 N일) 밖의 불량일은 세지 않는다
    assert _quality_view(entry, [{"date": "2026-09-01", "status": "found"}])["quality_bad_count"] == 0


# ── 실데이터 회귀 (DataVault 가 있을 때만): 오늘 사고가 다시 '정상'으로 보이면 실패 ─────────────────
@pytest.mark.real_vault
def test_real_069500_regression_known_bad_and_good_days():
    import os
    try:
        paths = dq.DataQualityAuditor.granularity_paths("069500")
    except Exception:
        pytest.skip("DataVault 없음")
    if "TICK" not in paths or "1M" not in paths:
        pytest.skip("069500 틱/1분봉 원천 없음")
    audit = dq.DataQualityAuditor.audit_symbol("069500", log_fn=lambda m: None)["TICK"]["days"]
    if "2026-08-19" not in audit or "2026-08-05" not in audit:
        pytest.skip("해당 기간 데이터 없음")
    assert audit["2026-08-19"]["status"] == "bad"       # 틱이 ~10% 만 기록된 날
    assert audit["2026-08-05"]["status"] == "ok"
    if "2026-09-14" in audit:
        # 2026-09-25 전체 재수집으로 온전한 틱을 되찾은 날 (이전: 틱 91개, 전일 종가로 채워짐)
        assert audit["2026-09-14"]["status"] == "ok"


# ── 날짜 원장(수집 엔진의 <파일>.days.json) 연동 ────────────────────────────────────
def test_audit_flags_days_changed_outside_the_collection_engine(tmp_path, monkeypatch):
    """엔진이 봉인한 날짜의 내용이 엔진을 거치지 않고 바뀌면(편집기 저장·외부 스크립트·손상) 그 날짜는 불량이다."""
    from CAiModelBaker.core import raw_day_store as rds
    monkeypatch.setattr(dq, "_CACHE_PATH", str(tmp_path / "c.json"))
    dq.DataQualityAuditor._mem, dq.DataQualityAuditor._loaded = {}, False
    bars = _minute_bars(DAYS)
    ref = tmp_path / "m.csv"
    bars.rename(columns={"ts": "timestamp"}).to_csv(ref, index=False)
    ticks = _ticks_from_bars(bars).rename(columns={"ts": "timestamp"})
    ticks["timestamp"] = ticks["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    raw = tmp_path / "raw.csv"
    ticks.to_csv(tmp_path / "in.tmp", index=False)
    rds.merge_file(str(tmp_path / "in.tmp"), str(raw), log=lambda m: None)
    a1 = dq.DataQualityAuditor.audit_pair(str(raw), str(ref), log_fn=lambda m: None)
    assert a1["summary"]["bad"] == 0
    text = raw.read_text().replace(".0,", ".5,", 1)                 # 첫 날짜의 값 하나를 몰래 고친다
    raw.write_text(text)
    a2 = dq.DataQualityAuditor.audit_pair(str(raw), str(ref), log_fn=lambda m: None)
    changed = [d for d, v in a2["days"].items() if "ledger_digest" in v["checks"]]
    assert len(changed) == 1 and a2["days"][changed[0]]["status"] == "bad"
    assert a2["summary"]["bad"] == 1 and a2["summary"]["bad_days"] == changed
