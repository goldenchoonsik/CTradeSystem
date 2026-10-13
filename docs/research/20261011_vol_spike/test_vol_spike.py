"""거래량 폭증 연구 엔진 테스트 — 미래 참조 금지·진입 규칙·수정주가·정지일 처리.

  .venv/bin/python -m pytest docs/research/20261011_vol_spike/test_vol_spike.py -q
실데이터 테스트는 staging 에 해당 종목이 없으면 건너뛴다. 성과(수익률 요약)는 보지 않는다.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import candidate_pool as cp  # noqa: E402
import event_engine as ee  # noqa: E402

SIGNAL_COLS = ["code", "date", "hyp", "cfg", "sig_min", "vr", "bar_value", "gap", "ret_open_to_sig", "ret15",
               "ret_prevclose_to_sig", "prev_range20", "day_value_to_sig", "prev_close"]


# ---------------------------------------------------------------- 합성 데이터
def synth_day(nd=60, seed=0, spikes=40):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-02", periods=nd)
    steps = rng.normal(0, 0.003, (nd, ee.NM))
    c = 10000 * np.exp(np.cumsum(steps.ravel())).reshape(nd, ee.NM)
    o = np.c_[c[:, :1], c[:, :-1]]
    h = np.maximum(o, c) * 1.001
    lo = np.minimum(o, c) * 0.999
    val = rng.uniform(2e7, 4e7, (nd, ee.NM))
    traded = rng.random((nd, ee.NM)) > 0.05
    for _ in range(spikes):                                   # 거래대금 폭증 + 급등/급락
        d, t = rng.integers(25, nd), rng.integers(5, ee.NM - 70)
        val[d, t] = 2e9
        traded[d, t] = True
        jump = rng.choice([1.12, 0.93])
        c[d, t:] *= jump
        o[d, t + 1:] *= jump
        h[d, t:] *= jump
        lo[d, t + 1:] *= jump
        h[d, t] = max(h[d, t], o[d, t], c[d, t])
        lo[d, t] = min(lo[d, t], o[d, t], c[d, t])
    for _ in range(spikes // 2):                              # 시가 갭 상승(+5%) + 장초반 폭증 → H1·H3 재료
        d, t = rng.integers(25, nd), rng.integers(1, 25)
        for a in (c, o, h, lo):
            a[d:] *= 1.05
        o[d, 0] = c[d - 1, -1] * 1.05
        val[d, t] = 2e9
        traded[d, t] = traded[d, 0] = True
        c[d, t:] *= 1.02
        o[d, t + 1:] *= 1.02
        h[d, t:] *= 1.02
        lo[d, t + 1:] *= 1.02
        h[d, t] = max(h[d, t], o[d, t], c[d, t])
    val[~traded] = 0.0
    mats = {"o": o, "h": h, "l": lo, "c": c, "val": val, "traded": traded}
    day_close = c[:, -1].copy()
    return ee.Day(dates, mats, day_close, np.ones(nd, bool))


def perturb(day, d0, t0, seed=1):
    """날 d0 의 분 t0 이후 봉과 d0 이후 모든 날을 무작위로 바꾼 사본."""
    rng = np.random.default_rng(seed)
    mats = {k: v.copy() for k, v in day.mats.items()}
    mask = np.zeros_like(mats["c"], bool)
    mask[d0, t0 + 1:] = True
    mask[d0 + 1:, :] = True
    scale = rng.uniform(0.5, 1.5, mats["c"].shape)
    for k in ("o", "h", "l", "c"):
        mats[k] = np.where(mask, mats[k] * scale, mats[k])
    mats["val"] = np.where(mask, rng.uniform(0, 5e9, mats["val"].shape), mats["val"])
    dc = day.day_close.copy()
    dc[d0:] *= rng.uniform(0.5, 1.5, len(dc) - d0)
    return ee.Day(day.dates, mats, dc, day.usable.copy())


def test_synthetic_produces_events():
    ev = ee.compute_events("SYN", synth_day())
    assert len(ev) > 10
    assert set(ev["hyp"]) == {"H1", "H2", "H3", "H4", "H6a", "H6b"}         # 모든 가설이 시험되도록 (변이 테스트가 놓친 공백)


def _ref(day, ev):
    """이벤트별 정답을 정의에서 직접 계산."""
    mats, out = day.mats, []
    tr = mats["traded"]
    for e in ev.itertuples():
        d = day.dates.get_loc(e.date)
        hist = mats["val"][d - ee.BASE_DAYS:d, e.sig_min]
        highs = [mats["h"][k][tr[k]].max() for k in range(d - 20, d)]
        out.append({"vr": mats["val"][d, e.sig_min] / hist.mean(), "entry_px": mats["o"][d, e.entry_min],
                    "prev_close": day.day_close[d - 1], "prev_high20": max(highs),
                    "first_open": mats["o"][d][tr[d]][0]})
    return pd.DataFrame(out, index=ev.index)


@pytest.mark.parametrize("seed", range(3))
def test_values_match_definitions(seed):
    day = synth_day(seed=seed)
    ev = ee.compute_events("SYN", day)
    ref = _ref(day, ev)
    np.testing.assert_allclose(ev["vr"], ref["vr"], rtol=1e-9)                 # 기준선 = 직전 20일(당일 제외)
    np.testing.assert_allclose(ev["entry_px"], ref["entry_px"], rtol=1e-12)     # 진입 = 진입봉 '시가'
    np.testing.assert_allclose(ev["prev_close"], ref["prev_close"], rtol=1e-12)
    np.testing.assert_allclose(ev["gap"], ref["first_open"] / ref["prev_close"] - 1, rtol=1e-9)
    h3 = ev["hyp"] == "H3"
    for e, r in zip(ev[h3].itertuples(), ref[h3].itertuples()):
        d = day.dates.get_loc(e.date)
        cl = day.mats["c"][d]
        assert cl[e.sig_min] > r.prev_high20                                   # 직전 20일 고가(당일 제외) 돌파
        assert (cl[: e.sig_min] <= r.prev_high20).all()                       # 그날 처음


@pytest.mark.parametrize("seed", range(5))
def test_no_lookahead_in_signals(seed):
    day = synth_day(seed=seed)
    base = ee.compute_events("SYN", day)
    rng = np.random.default_rng(100 + seed)
    for _ in range(4):
        d0, t0 = int(rng.integers(30, len(day.dates) - 1)), int(rng.integers(20, ee.NM - 20))
        alt = ee.compute_events("SYN", perturb(day, d0, t0, seed=seed + 7))
        dd = day.dates[d0]

        def keep(ev):
            # 미래와 무관해야 하는 신호: d0 이전 날 전부 + d0 에서 t0 이하에 난 신호
            # (진입봉이 t0 이하여야 진입가도 불변 — 진입가는 따로 비교)
            k = ev[(ev["date"] < dd) | ((ev["date"] == dd) & (ev["sig_min"] <= t0))]
            return k.sort_values(["date", "hyp", "cfg"]).reset_index(drop=True)

        a, b = keep(base), keep(alt)
        pd.testing.assert_frame_equal(a[SIGNAL_COLS], b[SIGNAL_COLS])
        ea, eb = a[a["entry_min"] <= t0], b[b["entry_min"] <= t0]
        np.testing.assert_allclose(ea["entry_px"].to_numpy(), eb["entry_px"].to_numpy())


def test_entry_after_signal():
    ev = ee.compute_events("SYN", synth_day(seed=3))
    assert (ev["entry_min"] > ev["sig_min"]).all()
    h4 = ev[ev["hyp"] == "H4"]
    assert (h4["entry_min"] >= h4["sig_min"] + 3).all()
    assert (ev["entry_min"] - ev["sig_min"] <= ee.ENTRY_WINDOW + 2).all()


def test_entry_skips_untraded_minutes():
    day = synth_day(seed=4)
    ev = ee.compute_events("SYN", day)
    d_idx = day.dates.get_indexer(ev["date"])
    assert day.mats["traded"][d_idx, ev["entry_min"].to_numpy()].all()


def test_one_event_per_config_per_day():
    ev = ee.compute_events("SYN", synth_day(seed=5))
    assert not ev.duplicated(["date", "hyp", "cfg"]).any()


def test_liquidity_filter():
    ev = ee.compute_events("SYN", synth_day(seed=6))
    assert (ev["bar_value"] >= ee.MIN_BAR_VALUE).all()


def test_halt_blocks_following_20_days():
    day = synth_day(seed=7)
    day.usable[30] = False
    ev = ee.compute_events("SYN", day)
    blocked = day.dates[30:51]
    assert not ev["date"].isin(blocked).any()
    assert ev["date"].max() > day.dates[51] or ev.empty


# ---------------------------------------------------------------- 후보 풀
def synth_panel(nd=80, ncode=60, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-02", periods=nd)
    rows = []
    for i in range(ncode):
        px = 5000 * np.exp(np.cumsum(rng.normal(0, 0.03, nd)))
        for d, p in zip(dates, px):
            rows.append({"date": d, "code": f"C{i:03d}", "market": "KOSPI" if i % 2 else "KOSDAQ", "open": p,
                         "high": p * (1 + rng.uniform(0, 0.1)), "low": p * (1 - rng.uniform(0, 0.1)), "close": p,
                         "volume": rng.integers(1, 1_000_000), "value": rng.uniform(1e8, 1e11)})
    return pd.DataFrame(rows)


def test_pool_uses_only_previous_days():
    raw = synth_panel()
    base = cp.build_pool(cp.derive(raw.copy()), 10)
    d0 = raw["date"].unique()[50]
    alt_raw = raw.copy()
    rng = np.random.default_rng(9)
    m = alt_raw["date"] >= d0                       # D 이후 값 전부 교란 (거래 여부=volume>0 은 유지: 개장 시 알려짐)
    for col in ("open", "high", "low", "close", "value"):
        alt_raw.loc[m, col] *= rng.uniform(0.3, 3.0, m.sum())
    alt_raw.loc[m, "volume"] = rng.integers(1, 1_000_000, m.sum())
    alt = cp.build_pool(cp.derive(alt_raw), 10)
    key = ["date", "market", "code"]
    a = base[base["date"] <= d0].sort_values(key).reset_index(drop=True)
    b = alt[alt["date"] <= d0].sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(a[key + ["rank"]], b[key + ["rank"]])


# ---------------------------------------------------------------- 실데이터 (staging)
def _real(code):
    day = ee.load_day(code)
    if day is None:
        pytest.skip(f"{code} staging 데이터 없음")
    return day


def test_real_adjustment_matches_daily_close():
    """병합 종목(KR모터스 5:1, 2026-07-28): 수정 기준 1분봉 15:30 종가가 수정 일봉 종가와 맞아야 한다(09-14 이전)."""
    day = _real("A000040")
    adj = ee._read_daily(os.path.join(ee.D_ADJ_DIR, "A000040_1d.csv"))["upbit_close"]
    s = pd.Series(day.day_close, index=day.dates)
    s = s[(s.index < "2026-09-14") & day.usable & s.notna()]
    rel = (s / adj.reindex(s.index) - 1).abs()
    assert rel.median() < 0.001
    assert (rel < 0.02).mean() > 0.97


def test_real_merge_day_has_no_fake_gap():
    day = _real("A000040")
    ev = ee.compute_events("A000040", day)
    assert not (ev["date"] == "2026-07-28").any()          # 직전 거래정지 → 판정 제외
    assert (ev["gap"].abs() < 0.35).all()                   # 상하한 30% 밖 갭 = 수정 실패


def test_real_vi_entry_price():
    """동화약품 2026-01-21 11:58 급락(−5.9%) 후 11:59 무체결(VI 단일가) → 12:00 체결 시가 5,830(무수정)에 진입."""
    day = _real("A000020")
    ev = ee.compute_events("A000020", day)
    e = ev[(ev["date"] == "2026-01-21") & (ev["hyp"] == "H2") & (ev["cfg"] == "x=0.05,k=10")]
    assert len(e) == 1
    d = day.dates.get_loc(pd.Timestamp("2026-01-21"))
    f = e["entry_px"].iloc[0] / 5830.0
    assert e["sig_min"].iloc[0] == ee.m("11:58") and e["entry_min"].iloc[0] == ee.m("12:00")
    assert abs(f - day.mats["c"][d, ee.m("11:58")] / 5560.0) < 1e-9     # 같은 날 같은 계수


# ---------------------------------------------------------------- 비용
def test_tick_size_table():
    import evaluate_events as evl
    px = [1999, 2000, 4995, 5000, 19990, 20000, 49950, 50000, 199900, 200000, 499500, 500000]
    assert evl.tick_size(px).tolist() == [1, 5, 5, 10, 10, 50, 50, 100, 100, 500, 500, 1000]


def test_judge_slip_floor_and_ticks():
    import evaluate_events as evl
    ev = pd.DataFrame({"entry_px": [1000.0, 100000.0]})
    s = evl.judge_slip(ev)
    assert s[0] == pytest.approx(0.003)        # 1,000원: 2틱=0.2% < 0.3%
    assert s[1] == pytest.approx(0.003)
    assert evl.judge_slip(pd.DataFrame({"entry_px": [2000.0]}))[0] == pytest.approx(0.005)   # 2,000원: 2×5원=0.5%


def test_real_raw_price_for_tick_cost():
    """병합 종목: 이벤트의 실제가 = 수정가 / 계수, 병합 전 실제가는 수정가의 1/5 (KR모터스 5:1, 2026-07-28)."""
    day = _real("A000040")
    ev = ee.compute_events("A000040", day)
    pre = ev[ev["date"] < "2026-07-28"]
    if pre.empty:
        pytest.skip("병합 전 이벤트 없음")
    np.testing.assert_allclose(pre["entry_px_raw"], pre["entry_px"] / 5.0, rtol=0.02)


def test_pool_price_filter_uses_raw_close():
    raw = synth_panel()
    raw["raw_close"] = raw["close"]
    low = raw["code"] == "C001"
    raw.loc[low, "raw_close"] = raw.loc[low, "close"] / 100     # 실제가는 1,000원 미만
    pool = cp.build_pool(cp.derive(raw), 30)
    assert "C001" not in set(pool["code"])


def test_real_special_session_close():
    """수능일(2025-11-13, 10:00~16:30): 종가는 16:30 동시호가 봉, 15:30 봉(장중)이 아니다."""
    day = _real("A000020")
    p = os.path.join(ee.M1_DIR, "A000020", "2025-11-13.csv")
    if not os.path.exists(p):
        pytest.skip("수능일 파일 없음")
    x = pd.read_csv(p)
    t = x["timestamp"].str[11:16]
    assert t.iloc[0] >= "10:00"
    d = day.dates.get_loc(pd.Timestamp("2025-11-13"))
    f = day.factor[d]
    assert day.day_close[d] == pytest.approx(x.loc[t == "16:30", "upbit_close"].iloc[-1] * f)
    assert day.mats["traded"][d, ee.m("09:01")]                     # 10:01 첫 봉이 평일 09:01 자리에
    assert day.mats["o"][d, ee.m("09:01")] == pytest.approx(x["upbit_open"].iloc[0] * f)


@pytest.mark.parametrize("seed", range(3))
def test_h6_values_match_definitions(seed):
    day = synth_day(seed=seed)
    ev = ee.compute_events("SYN", day)
    val, c, tr = day.mats["val"], day.mats["c"], day.mats["traded"]
    for e in ev[ev["hyp"].str.startswith("H6")].itertuples():
        d, t = day.dates.get_loc(e.date), e.sig_min
        prm = dict(kv.split("=") for kv in e.cfg.split(","))
        k = float(prm["k"])
        if e.hyp == "H6a":
            base = val[d - ee.BASE_DAYS:d].sum(axis=1).mean()                # 직전 20일 하루 거래대금 평균(당일 제외)
            assert val[d, : t + 1].sum() >= k * base
            assert (val[d, :t].sum() < k * base) or (c[d, :t] / e.prev_close - 1 < float(prm["x"])).any() or t == ee.m("09:05") \
                or (val[d, :t] < ee.MIN_BAR_VALUE).any()
            assert c[d, t] / e.prev_close - 1 >= float(prm["x"])
        else:
            prev5 = val[d, t - 5:t].mean()                                   # 직전 5분(신호 봉 제외)
            assert prev5 == 0 or val[d, t] >= k * prev5
            assert c[d, t] > day.mats["o"][d, t] and c[d, t] > e.prev_close
