"""global_macro 원장 — 임시 폴더·가짜 데이터로만 검사(실제 DataVault·네트워크 안 씀)."""
import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from shared.data_library.global_macro import analytics, catalog, dashboard, panel, sources, store


@pytest.fixture
def stage(tmp_path, monkeypatch):
    root = tmp_path / "staging" / "global_macro"
    root.mkdir(parents=True)
    monkeypatch.setattr(store, "stage", lambda: str(root))
    return root


# ---------------------------------------------------------------- 규약
def test_catalog_is_valid():
    assert catalog.validate() == []


def test_label_series_never_used_as_signal(stage):
    """사후 판정(lag=None)은 월 PIT 패널에 들어가지 않는다."""
    lab = [s["key"] for s in catalog.SERIES if s["lag_days"] is None]
    assert lab == ["us_rec"]
    store.write_series("us_rec", [("2020-03-31", 1.0), ("2020-04-30", 1.0)])
    store.write_series("us_spx", [("2020-03-31", 100.0), ("2020-04-30", 110.0)])
    panel.build(start="2020-01-31")
    pit = pd.read_parquet(stage / "derived" / "monthly_pit.parquet")
    assert "us_rec" not in pit.columns and "us_spx" in pit.columns
    assert "us_rec" in pd.read_parquet(stage / "derived" / "labels.parquet").columns


# ---------------------------------------------------------------- 파서
def test_parse_fred_skips_missing_and_moves_month_to_end():
    rows = sources.parse_fred_csv("observation_date,X\n2024-01-01,1.5\n2024-02-01,.\n2024-03-01,2\n")
    assert rows == [("2024-01-01", 1.5), ("2024-03-01", 2.0)]
    assert sources.month_start_to_end(rows) == [("2024-01-31", 1.5), ("2024-03-31", 2.0)]
    with pytest.raises(ValueError):
        sources.parse_fred_csv("<html>maintenance</html>")


def test_parse_yahoo_uses_adjclose_and_local_date():
    raw = json.dumps({"chart": {"result": [{"meta": {"gmtoffset": -14400}, "timestamp": [1704205800, 1704292200, 1704378600],
                                            "indicators": {"quote": [{"close": [10.0, None, 12.0]}], "adjclose": [{"adjclose": [9.5, None, 11.5]}]}}]}}).encode()
    assert sources.parse_yahoo_chart(raw) == [("2024-01-02", 9.5), ("2024-01-04", 11.5)]


def test_parse_ecos_daily_and_monthly():
    raw = json.dumps({"StatisticSearch": {"row": [{"TIME": "20240102", "DATA_VALUE": "1300.5"}, {"TIME": "202401", "DATA_VALUE": ""},
                                                  {"TIME": "202402", "DATA_VALUE": "3.5"}]}}).encode()
    assert sources.parse_ecos(raw) == [("2024-01-02", 1300.5), ("2024-02-01", 3.5)]
    with pytest.raises(ValueError):
        sources.parse_ecos(b'{"RESULT": {"CODE": "INFO-200"}}')


def test_creon_splice_refused_without_overlap(monkeypatch):
    """겹치는 날이 부족하면 Yahoo 로 잇지 않는다(검증 안 되는 이어 붙이기 금지)."""
    monkeypatch.setattr(sources, "read_creon_index", lambda code: [("2026-10-12", 100.0)])
    monkeypatch.setattr(sources, "fetch_yahoo_sym", lambda sym, key: [("2026-10-13", 101.0), ("2026-10-14", 102.0)])
    rows, info = sources.fetch_creon_index({"key": "kr_k200", "sid": "U180", "extend_with": "^KS200"})
    assert rows == [("2026-10-12", 100.0)] and info["extended_to"] is None and "skipped" in info


def test_creon_splice_when_overlap_matches(monkeypatch):
    days = pd.bdate_range("2015-01-01", periods=300)
    cre = [(str(d.date()), 100.0 + i) for i, d in enumerate(days)]
    yah = cre[-260:] + [("2030-01-02", 999.0)]
    monkeypatch.setattr(sources, "read_creon_index", lambda code: list(cre))
    monkeypatch.setattr(sources, "fetch_yahoo_sym", lambda sym, key: yah)
    rows, info = sources.fetch_creon_index({"key": "kr_kospi", "sid": "U001", "extend_with": "^KS11"})
    assert rows[-1] == ("2030-01-02", 999.0) and info["overlap_days"] == 260 and info["max_rel_diff"] == 0


# ---------------------------------------------------------------- 발표 시차
def test_point_in_time_blocks_lookahead():
    """3월 값(기간 끝 3-31, 시차 20일)은 3월 말엔 안 보이고 4월 말에 보인다."""
    s = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-31"]))
    at = pd.to_datetime(["2024-03-31", "2024-04-30"])
    v, per = panel.point_in_time(s, 20, at)
    assert list(v) == [2.0, 3.0]
    assert list(per) == list(pd.to_datetime(["2024-02-29", "2024-03-31"]))
    v0, _ = panel.point_in_time(s, 0, pd.to_datetime(["2024-03-31"]))
    assert list(v0) == [3.0]


def test_panel_build_writes_pit_and_freshness(stage):
    store.write_series("w_cli_g7", [(f"2024-{m:02d}-28", float(m)) for m in range(1, 7)])
    store.write_series("us_spx", [("2024-06-28", 5000.0)])
    q = panel.build(start="2024-01-31")
    pit = pd.read_parquet(stage / "derived" / "monthly_pit.parquet")
    assert pit.loc["2024-06-30", "w_cli_g7"] == 5.0          # 6월 값은 시차 20일 → 7월에야
    assert pit.loc["2024-07-31", "w_cli_g7"] == 6.0
    assert q["series"]["w_cli_g7"]["stale"] is True and q["series"]["us_ndx"] == {"missing": True}


# ---------------------------------------------------------------- 약세장
def test_bear_market_confirmed_by_20pct_both_ways():
    path = [100, 120, 130, 120, 104, 100, 110, 125, 128, 115, 112, 140]   # 130→100(−23%) 약세, 100→125(+25%) 반등, 128→112(−12.5%)는 아님
    s = pd.Series(path, index=pd.bdate_range("2020-01-01", periods=len(path)), dtype=float)
    b = analytics.bear_markets(s)
    assert len(b) == 1
    assert (b[0]["peak"], b[0]["trough"], b[0]["confirm"]) == ("2020-01-03", "2020-01-08", "2020-01-07")
    assert b[0]["depth"] == pytest.approx(100 / 130 - 1) and b[0]["trough_confirmed"] and b[0]["recovered"] == "2020-01-16"


def test_bear_market_open_episode():
    s = pd.Series([100, 90, 79, 75], index=pd.bdate_range("2020-01-01", periods=4), dtype=float)
    b = analytics.bear_markets(s)
    assert len(b) == 1 and b[0]["trough_confirmed"] is False and b[0]["recovered"] is None


# ---------------------------------------------------------------- 연결도
def test_spillover_detects_direction():
    rng = np.random.default_rng(0)
    n = 2000
    x = rng.standard_normal(n)
    y = np.r_[0, 0.9 * x[:-1]] + 0.3 * rng.standard_normal(n)      # x 가 다음 기에 y 를 움직임
    z = rng.standard_normal(n)
    r = analytics.spillover(pd.DataFrame({"X": x, "Y": y, "Z": z}, index=pd.date_range("2000-01-07", periods=n, freq="W-FRI")))
    t = np.array(r["table"])
    assert np.allclose(t.sum(axis=1), 100)
    assert t[1, 0] > 60                 # Y 변동의 대부분이 X 에서
    assert t[0, 1] < 5 and t[2, 0] < 5 and t[0, 2] < 5
    assert r["net"][0] > 50 and r["net"][1] < -50


def test_weekly_sync_uses_thursday_for_west_friday_for_asia():
    idx = pd.bdate_range("2024-01-01", "2024-01-12")
    daily = pd.DataFrame({k: np.arange(len(idx), dtype=float) + 1 for k in catalog.EQUITY.values()}, index=idx)
    w = dashboard.weekly_synced(daily)
    fri = pd.Timestamp("2024-01-12")
    assert w.loc[fri, "KR"] == daily.loc["2024-01-12", "kr_kospi"]      # 아시아 = 금요일
    assert w.loc[fri, "US"] == daily.loc["2024-01-11", "us_spx"]        # 미국 = 목요일(아시아 금요일 마감 전)


def test_clean_removes_nan_for_browser_json():
    out = dashboard._clean({"a": float("nan"), "b": [1.0, np.float64("inf"), np.int64(3)]})
    assert out == {"a": None, "b": [1.0, None, 3]}
    json.dumps(out, allow_nan=False)


# ---------------------------------------------------------------- 업종 원천 파서
def test_kenfrench_sections_parse_monthly_and_annual():
    from shared.data_library.global_macro import industry
    text = ("This file was created ...\n\n  Average Value Weighted Returns -- Monthly\n,Agric,Food \n192607,   2.37,  -99.99\n192608,   1.00,   2.50\n\n"
            "  Average Value Weighted Returns -- Annual\n,Agric,Food\n  1927,  10.0,  20.0\n\n  Sum of BE / Sum of ME\n,Agric,Food\n  1926,  0.50,  0.80\n")
    sec = industry.kf_sections(text)
    m = industry._monthly_index(sec["Average Value Weighted Returns -- Monthly"]) / 100
    assert list(m.columns) == ["Agric", "Food"] and m.index[0] == pd.Timestamp("1926-07-31")
    assert m.loc["1926-07-31", "Agric"] == pytest.approx(0.0237) and np.isnan(m.loc["1926-07-31", "Food"])
    assert industry._annual_index(sec["Sum of BE / Sum of ME"]).loc[1926, "Food"] == 0.8
    assert "Average Value Weighted Returns -- Annual" in sec


def test_parse_multpl_table():
    html = "<tr><td>Oct 21, 2026</td>\n<td>\n&#x2002;\n41.08\n</td></tr><tr><td>Oct 1, 2026</td><td>&#x2002; 40.81</td></tr>"
    assert sources.parse_multpl(html) == [("2026-10-01", 40.81), ("2026-10-21", 41.08)]
    with pytest.raises(ValueError):
        sources.parse_multpl("<html></html>")


def test_merged_signals_combines_stage_registries(stage):
    (stage / "signals").mkdir()
    (stage / "signals" / "registry_stage2.json").write_text(json.dumps({"stage": 2, "periods": ["~1999", "2000~"], "source": "s2",
                                                                       "signals": [{"id": "A5", "verdict": "실전 후보"}], "combos": [{"id": "E-A"}]}))
    (stage / "signals" / "registry_stage3.json").write_text(json.dumps({"stage": 3, "periods": ["~2009", "2010~"], "source": "s3",
                                                                       "signals": [{"id": "X4", "verdict": "작게 실험"}], "combos": []}))
    m = dashboard.merged_signals()
    assert [s["id"] for s in m["signals"]] == ["A5", "X4"]
    assert m["signals"][0]["periods"] == ["~1999", "2000~"] and m["signals"][1]["stage"] == 3
    assert [s["stage"] for s in m["sources"]] == [2, 3] and m["combos"][0]["stage"] == 2


# ---------------------------------------------------------------- 무역 연결
def test_parse_tiva_scales_unit_mult_and_skips_empty():
    from shared.data_library.global_macro import trade
    text = ("DATAFLOW,MEASURE,REF_AREA,ACTIVITY,COUNTERPART_AREA,UNIT_MEASURE,FREQ,TIME_PERIOD,OBS_VALUE,UNIT_MULT\n"
            "x,FFD_DVA,KOR,_T,CHN,USD,A,2020,1.5,6\nx,FFD_DVA,KOR,_T,USA,USD,A,2020,,6\n")
    df = trade.parse_tiva_csv(text)
    assert len(df) == 1 and df.iloc[0].value == 1.5e6 and df.iloc[0].partner == "CHN" and df.iloc[0].year == 2020
    with pytest.raises(ValueError):
        trade.parse_tiva_csv("DATAFLOW,MEASURE,REF_AREA,ACTIVITY,COUNTERPART_AREA,UNIT_MEASURE,FREQ,TIME_PERIOD,OBS_VALUE,UNIT_MULT\n")


def test_weighted_cli_uses_prior_year_weights_and_renormalizes(stage):
    from shared.data_library.global_macro import trade_signal as ts
    (stage / "derived" / "trade").mkdir(parents=True)
    idx = pd.date_range("2018-01-31", "2021-12-31", freq="ME")
    cli = pd.DataFrame({"CHN": np.linspace(100, 104, len(idx)), "USA": np.linspace(100, 98, len(idx)), "JPN": np.nan}, index=idx)
    cli.to_parquet(stage / "derived" / "trade" / "partner_cli.parquet")
    w = pd.DataFrame({"CHN": [0.6, 0.2], "USA": [0.2, 0.6], "JPN": [0.2, 0.2]}, index=[2019, 2020])
    at = pd.DatetimeIndex([pd.Timestamp("2020-12-31"), pd.Timestamp("2021-12-31")])
    out = ts.weighted_cli(w, at)
    ch = ts.cli_change(at)
    # 2020년 말 = 2019년 가중치, 일본(선행지수 없음) 빠지고 0.6:0.2 → 0.75:0.25
    assert out.loc["2020-12-31", "total"] == pytest.approx(0.75 * ch.loc["2020-12-31", "CHN"] + 0.25 * ch.loc["2020-12-31", "USA"])
    assert out.loc["2021-12-31", "total"] == pytest.approx(0.25 * ch.loc["2021-12-31", "CHN"] + 0.75 * ch.loc["2021-12-31", "USA"])


def test_kr_gross_weights_merge_hong_kong_into_china(stage):
    from shared.data_library.global_macro import trade_signal as ts
    (stage / "derived" / "trade").mkdir(parents=True)
    idx = pd.date_range("2020-01-31", "2020-12-31", freq="ME")
    pd.DataFrame({"CN": 3.0, "HK": 1.0, "US": 4.0, "VN": 10.0}, index=idx).to_parquet(stage / "derived" / "trade" / "kr_exp.parquet")
    w = ts.weights_kr_gross()
    assert w.loc[2020, "CHN"] == pytest.approx(0.5) and w.loc[2020, "USA"] == pytest.approx(0.5)   # 베트남(선행지수 없음) 제외


# ---------------------------------------------------------------- 관계 판독
def test_us_imports_parse_census_rows():
    from shared.data_library.global_macro import us_imports
    raw = json.dumps([["CTY_CODE", "GEN_VAL_MO", "time", "COMM_LVL", "I_COMMODITY", "CTY_CODE"],
                      ["5800", "277975533", "2013-01", "HS4", "8542", "5800"], ["-", "2307480686", "2013-01", "HS4", "8542", "-"]]).encode()
    rows = us_imports.parse(raw, "8542")
    assert rows[0] == {"month": pd.Timestamp("2013-01-31"), "partner": "5800", "cmd": "8542", "value": 277975533.0}
    assert rows[1]["partner"] == "-"
    with pytest.raises(RuntimeError):
        us_imports.parse(b"[]", "8542")


def test_export_links_find_lead_in_rolling_window(stage):
    from shared.data_library.global_macro import relations
    (stage / "derived" / "trade").mkdir(parents=True)
    idx = pd.date_range("1990-01-31", "2005-12-31", freq="ME")
    rng = np.random.default_rng(1)
    cli = pd.Series(100 + np.cumsum(rng.standard_normal(len(idx))), index=idx)
    x = (cli - cli.shift(6)).fillna(0)
    lvl = np.exp((x.shift(4).fillna(0) * 0.05).cumsum())          # 수출 수준이 4개월 늦게 경기 변화를 따름
    pd.DataFrame({"USA": cli}).to_parquet(stage / "derived" / "trade" / "partner_cli.parquet")
    pd.DataFrame({"US": lvl * 1000}, index=idx).to_parquet(stage / "derived" / "trade" / "kr_exp.parquet")
    links = relations.export_links(window=60)
    assert "미국" in links and len(links["미국"]) > 50
    last = links["미국"][-1]
    assert last[1] > 0.3 and 0 <= last[2] <= 9


def test_events_sorted_and_missing_file_seeds_from_package(stage):
    from shared.data_library.global_macro import relations
    # 운영본이 없으면 패키지 시드(git 원장 사본)를 복사해 쓴다 — 시드는 날짜순·필수 키 보유
    seeded = relations.events()
    assert len(seeded) >= 10 and (stage / "reference" / "events.json").exists()
    assert [e["date"] for e in seeded] == sorted(e["date"] for e in seeded)
    assert all({"date", "title"} <= set(e) for e in seeded)
    (stage / "reference" / "events.json").unlink()
    (stage / "reference" / "events.json").write_text(json.dumps([{"date": "2022-08-16", "title": "b"}, {"date": "2001-12-11", "title": "a"}]))
    assert [e["title"] for e in relations.events()] == ["a", "b"]


# ---------------------------------------------------------------- 판독 일지
@pytest.fixture
def jprices(stage, monkeypatch):
    from shared.data_library.global_macro import journal
    days = pd.bdate_range("2000-01-03", "2024-12-31")
    a = pd.Series(np.linspace(100, 300, len(days)), index=days)            # 꾸준히 오름
    b = pd.Series(np.linspace(100, 600, len(days)), index=days)            # 더 빨리 오름
    monkeypatch.setattr(journal, "price", lambda k: {"idx:kospi": a, "idx:spx": b}.get(k))
    return journal, a, b


def test_journal_validates_inputs(jprices):
    j, _, _ = jprices
    with pytest.raises(ValueError):
        j.create("idx:kospi", "sideways", 6, 0.7, "근거가 충분히 깁니다")
    with pytest.raises(ValueError):
        j.create("idx:kospi", "outperform", 6, 0.7, "근거가 충분히 깁니다")            # 벤치마크 없음
    with pytest.raises(ValueError):
        j.create("idx:kospi", "up", 5, 0.7, "근거가 충분히 깁니다")                    # 기간
    with pytest.raises(ValueError):
        j.create("idx:kospi", "up", 6, 0.99, "근거가 충분히 깁니다")                   # 확신도
    with pytest.raises(ValueError):
        j.create("idx:kospi", "up", 6, 0.7, "짧음")
    with pytest.raises(ValueError):
        j.create("idx:없음", "up", 6, 0.7, "근거가 충분히 깁니다")


def test_journal_scores_from_next_trading_day(jprices):
    j, a, _ = jprices
    e = j.create("idx:kospi", "up", 3, 0.7, "미국 승용차 점유 확대를 읽음", now=datetime(2024, 1, 10, 21, 0))
    sc = j.score(e, today=pd.Timestamp("2024-12-31"))
    assert sc["start_date"] == "2024-01-11"                                  # 기록 당일(1-10) 종가가 아니라 다음 거래일
    assert sc["state"] == "채점" and sc["end_date"] == "2024-04-11" and sc["hit"] is True
    assert sc["ret"] == pytest.approx(a["2024-04-11"] / a["2024-01-11"] - 1)
    assert sc["brier"] == pytest.approx(0.09) and 0 < sc["base_rate"] <= 1
    late = j.create("idx:kospi", "up", 12, 0.6, "아직 만기가 안 된 기록입니다", now=datetime(2024, 6, 3, 9, 0))
    assert j.score(late, today=pd.Timestamp("2024-12-31"))["state"] == "진행 중"
    fresh = j.create("idx:kospi", "up", 3, 0.6, "다음 거래일 종가가 아직 없음", now=datetime(2024, 12, 31, 9, 0))
    assert j.score(fresh)["state"] == "대기"


def test_journal_relative_view_uses_benchmark(jprices):
    j, a, b = jprices
    e = j.create("idx:kospi", "outperform", 6, 0.8, "코스피가 S&P보다 강할 것으로 읽음", benchmark="idx:spx", now=datetime(2023, 3, 1, 10))
    sc = j.score(e, today=pd.Timestamp("2024-12-31"))
    assert sc["ret_rel"] == pytest.approx(sc["ret"] - sc["ret_bench"]) and sc["ret_rel"] < 0 and sc["hit"] is False


def test_journal_append_only_and_no_withdraw_after_scoring(jprices):
    j, _, _ = jprices
    old = j.create("idx:kospi", "down", 3, 0.6, "하락을 읽었으나 틀릴 기록", now=datetime(2023, 1, 5, 10))
    with pytest.raises(ValueError):
        j.withdraw(old["id"], "숨기고 싶음")                                     # 만기 지나 채점됨 → 철회 금지
    j.note(old["id"], "사후 메모는 가능")
    live = j.create("idx:kospi", "up", 12, 0.7, "진행 중이라 철회 가능", now=datetime(2024, 11, 1, 10))
    j.withdraw(live["id"], "판단 근거가 바뀜")
    with pytest.raises(ValueError):
        j.withdraw(live["id"], "두 번")
    lst = j.listing(today=pd.Timestamp("2024-12-31"))
    by = {r["id"]: r for r in lst["entries"]}
    assert by[old["id"]]["notes"][0]["text"] == "사후 메모는 가능" and by[old["id"]]["score"]["hit"] is False
    assert by[live["id"]]["score"]["state"] == "철회" and lst["summary"]["n_withdrawn"] == 1
    assert lst["summary"]["n_scored"] == 1 and lst["summary"]["calibration"]["60~70%"] == {"n": 1, "hit": 0.0}
    raw = open(j._path(), encoding="utf-8").read().splitlines()
    assert [json.loads(x)["type"] for x in raw] == ["create", "note", "create", "withdraw"]


# ---------------------------------------------------------------- 판독 엔진(미래참조 차단)
class _StubData:
    def __init__(self, y, x):
        self.y, self.x = y, x


def test_reading_link_ignores_data_after_t_minus_1():
    from shared.data_library.global_macro import reading as R
    idx = pd.date_range("2000-01-31", "2012-12-31", freq="ME")
    rng = np.random.default_rng(3)
    x = pd.Series(rng.standard_normal(len(idx)), index=idx)
    y = 0.8 * x.shift(6) + 0.3 * pd.Series(rng.standard_normal(len(idx)), index=idx)
    t = pd.Timestamp("2010-06-30")
    a = R.link(_StubData(pd.DataFrame({"미국": y}), pd.DataFrame({"미국": x})), "미국", t)
    y2, x2 = y.copy(), x.copy()
    y2[y2.index >= t] = 999.0                       # t 이후(같은 달 포함) 값을 망가뜨려도
    x2[x2.index >= t] = -999.0
    b = R.link(_StubData(pd.DataFrame({"미국": y2}), pd.DataFrame({"미국": x2})), "미국", t)
    assert a == b                                   # t−1 까지만 쓰므로 같아야 한다
    assert a["k"] == 6 and a["state"] == "강함"


def test_reading_demand_uses_known_lead_or_latest():
    from shared.data_library.global_macro import reading as R
    idx = pd.date_range("2000-01-31", "2012-12-31", freq="ME")
    x = pd.Series(np.sin(np.arange(len(idx)) / 6.0), index=idx)
    y = 0.5 * x.shift(8)
    D = _StubData(pd.DataFrame({"미국": y}), pd.DataFrame({"미국": x}))
    t = pd.Timestamp("2010-06-30")
    lk = R.link(D, "미국", t)
    d = R.demand(D, "미국", t, lk)
    assert lk["k"] == 8                              # 선행 8개월 ≥ 7 → 목표월(t+6)의 원인 값(t−2)이 이미 공개됨
    assert d["x_month"] == "2010-04-30" and d["call"] in ("가속", "감속")
    assert d["yhat"] == pytest.approx(lk["alpha"] + lk["beta"] * x["2010-04-30"])
