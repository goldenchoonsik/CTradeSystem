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


def test_eu_imports_parse_comext_csv_maps_ext_total_to_dash():
    from shared.data_library.global_macro import eu_imports
    raw = ("DATAFLOW,LAST UPDATE,freq,reporter,partner,product,flow,indicators,TIME_PERIOD,OBS_VALUE\n"
           "ESTAT:DS-045409(1.0),16/10/26 11:00:00,M,EU27_2020,KR,8542,1,VALUE_IN_EUROS,2024-01,295399974\n"
           "ESTAT:DS-045409(1.0),16/10/26 11:00:00,M,EU27_2020,EXT_EU27_2020,8542,1,VALUE_IN_EUROS,2024-01,3521642446\n"
           "ESTAT:DS-045409(1.0),16/10/26 11:00:00,M,EU27_2020,CN,8471,1,VALUE_IN_EUROS,2024-01,1\n").encode()
    rows = eu_imports.parse(raw, "8542")
    assert rows[0] == {"month": pd.Timestamp("2024-01-31"), "partner": "KR", "cmd": "8542", "value": 295399974.0}
    assert rows[1]["partner"] == "-" and len(rows) == 2                      # 역외 합계 → '-', 다른 품목 행은 버림
    assert "EXT_EU27_2020" in eu_imports._query("72") and ".72.1." in eu_imports._query("72")
    with pytest.raises(RuntimeError):
        eu_imports.parse(b"oops", "8542")


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


# ---------------------------------------------------------------- R5-D 수요 충격 단독 변형·전진 확인
def test_reading_sectors_demand_only_variant_ignores_us_share():
    from shared.data_library.global_macro import reading as R
    t = pd.Timestamp("2010-06-30")
    secs = list(R.SECTOR_HS)
    expo = {2009: {s: {"미국": 0.5 + 0.05 * i, "유럽": 0.3} for i, s in enumerate(secs)}}
    dem = {"미국": {"delta": 0.10}, "유럽": {"delta": -0.05}}
    hit = "U013"                                                              # 미국 점유 자료가 있는 업종 하나만 잠식
    comp = {cmd: {"kr": {"delta": -3.0 if cmd in R.SECTOR_US[hit] else 0.0}, "us_imports_12m": 1e9} for s in secs for cmd in R.SECTOR_US.get(s, [])}
    out = R.sectors(None, t, dem, comp, expo)
    df = pd.DataFrame(out).T
    zD = (df.D.astype(float) - df.D.astype(float).mean()) / df.D.astype(float).std()
    assert np.allclose(df.score_D.astype(float), zD)                          # 수요 충격만 = z(D)
    assert all(v["reading_D"] == ("우호" if v["score_D"] > 1e-12 else "비우호" if v["score_D"] < -1e-12 else "중립") for v in out.values())
    assert out[hit]["score"] < out[hit]["score_D"]                            # 미국 점유 잠식은 합성 점수만 깎는다
    assert (df.score != df.score_D).any()


def _fwd_hist(ts, n_sec=5):
    from shared.data_library.global_macro import reading as R
    secs = list(R.SECTOR_HS)[:n_sec]
    return [{"t": str(t.date()), "demand": {"미국": {"call": "가속", "delta": 0.1}}, "total": {"call": "가속"},
             "sectors": {s: {"name": s, "score": float(i), "score_D": float(-i), "D": -i / 10, "C": None, "reading": "우호", "reading_D": "비우호"} for i, s in enumerate(secs)}} for t in ts]


def test_forward_log_is_append_only_and_only_after_freeze(stage):
    from shared.data_library.global_macro import reading as R, reading_board as B
    ts = [R.FORWARD_FROM - pd.offsets.MonthEnd(1), R.FORWARD_FROM, R.FORWARD_FROM + pd.offsets.MonthEnd(1)]
    assert B.forward_log_append(_fwd_hist(ts)) == 2                            # 고정일 이전 판독은 기록하지 않는다
    assert [e["t"] for e in B.forward_log_read()] == [str(ts[1].date()), str(ts[2].date())]
    changed = _fwd_hist(ts)
    for r in changed:
        for v in r["sectors"].values():
            v["score_D"] = 999.0                                                # 재계산 값이 달라져도
    assert B.forward_log_append(changed) == 0                                  # 이미 있는 달은 덮어쓰지 않는다
    assert all(v["score_D"] != 999.0 for e in B.forward_log_read() for v in e["sectors"].values())


def test_forward_status_waits_then_judges(stage):
    from shared.data_library.global_macro import reading as R, reading_eval as E
    empty = pd.DataFrame(columns=E.SECTOR_COLS)
    f = E.forward(empty, pd.DataFrame(columns=["composite", "D"]), n_readings=3)
    assert f["status"] == "대기" and f["n_readings"] == 3 and f["n_months"] == 0 and f["first_scorable"] == "2027-05-31"
    secs = list(R.SECTOR_HS)[:5]
    def _case(n, icD, icC, spread_sign):
        months = pd.date_range(R.FORWARD_FROM, periods=n, freq="ME")
        rows = []
        for t in months:
            for i, s in enumerate(secs):
                rows.append({"t": t, "s": s, "score": float(i), "score_D": float(i), "D": i, "C": None,
                             "out": spread_sign * 0.01 * i, "hit": True, "hit_D": True, "has_C": False})
        ic = pd.DataFrame({"composite": icC, "D": icD}, index=months)
        return E.forward(pd.DataFrame(rows, columns=E.SECTOR_COLS), ic, n_readings=n)
    assert _case(10, 0.1, 0.05, 1)["status"] == "확인 중 10/24"
    assert _case(24, 0.1, 0.05, 1)["status"] == "확정"                         # IC>0, 합성 이상, 상위−하위 > 0
    assert _case(24, -0.1, 0.05, 1)["status"] == "기각"
    assert _case(24, 0.1, 0.2, 1)["status"] == "보류 24/36"                    # 합성보다 못하면 보류
    assert _case(36, 0.1, 0.2, 1)["status"] == "확정"                          # 최종: IC>0 & 차이>0
    assert _case(36, 0.1, 0.2, -1)["status"] == "기각"


def test_reading_ext_sector_ce_and_forward_track_ce():
    from shared.data_library.global_macro import reading as R, reading_ext as X, reading_eval as E
    secs = list(R.SECTOR_HS)
    sectors = {s: {"name": s, "score": 0.0, "score_D": 0.0, "D": 0.0, "C": None} for s in secs}
    comp_eu = {cmd: {"kr": {"delta": 1.0 if s == "U013" else -1.0, "state": "확대"}, "us_imports_12m": 10.0} for s in secs for cmd in R.SECTOR_US.get(s, [])}
    ce = X.sector_ce(sectors, comp_eu)
    assert ce["U013"]["reading_CE"] == "우호" and ce["U015"]["reading_CE"] == "비우호"
    assert ce["U014"]["score_CE"] is None and ce["U014"]["reading_CE"] is None       # EU 품목 대응 없는 업종
    # 전진 트랙 CE: score_CE 가 있는 달만 센다
    months = pd.date_range(R.FORWARD_FROM, periods=5, freq="ME")
    rows = []
    for j, t in enumerate(months):
        for i, s in enumerate(secs[:5]):
            rows.append({"t": t, "s": s, "score": float(i), "score_D": float(i), "score_CE": float(i) if j < 3 else None, "D": i, "C": None,
                         "out": 0.01 * i, "hit": True, "hit_D": True, "hit_CE": True if j < 3 else np.nan, "has_C": False})
    ic = pd.DataFrame({"composite": 0.1, "D": 0.1, "CE": [0.2, 0.2, 0.2, np.nan, np.nan]}, index=months)
    f = E.forward(pd.DataFrame(rows, columns=E.SECTOR_COLS), ic, n_readings=5, track="CE")
    assert f["track"] == "CE" and f["n_months"] == 3 and f["status"] == "확인 중 3/24" and f["ic_D"] == pytest.approx(0.2)
    assert E.forward(pd.DataFrame(rows, columns=E.SECTOR_COLS), ic, n_readings=5, track="D")["n_months"] == 5


# ---------------- 종목 단계(L3)
def test_ksic_mapping_manufacturing_only():
    from shared.data_library.global_macro import kr_ksic
    assert kr_ksic.ksic2("264") == "C26" and kr_ksic.KSIC2_TO_SECTOR["C26"] == "U013"      # 삼성전자 → 전기·전자
    assert kr_ksic.ksic2("20111") == "C20" and kr_ksic.KSIC2_TO_SECTOR["C20"] == "U008"
    assert kr_ksic.ksic2("641") is None and kr_ksic.ksic2("") is None and kr_ksic.ksic2(None) is None   # 금융·빈값
    uni = pd.DataFrame({"code": ["A1", "A2"], "name": ["a", "b"], "market": ["KOSPI", "KOSDAQ"]})
    t = kr_ksic.build_table([{"stock_code": "A1", "corp_code": "c1", "induty_code": "301"}, {"stock_code": "A2", "corp_code": "c2", "induty_code": "582"}], uni)
    d = t.set_index("stock_code").sector
    assert d["A1"] == "U015" and pd.isna(d["A2"])


def test_sector_beta_uses_only_data_up_to_t():
    from shared.data_library.global_macro import stocks
    idx = pd.date_range("2015-01-31", periods=60, freq="ME")
    rng = np.random.default_rng(7)
    mk = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 60))), index=idx)
    sec_ex = rng.normal(0, 0.05, 60)
    sec = mk * np.exp(np.cumsum(sec_ex))
    hi = mk * np.exp(np.cumsum(2.0 * sec_ex + rng.normal(0, 0.01, 60)))        # 업종 베타 2
    lo = mk * np.exp(np.cumsum(0.5 * sec_ex + rng.normal(0, 0.01, 60)))        # 0.5
    sm = pd.DataFrame({"HI": hi, "LO": lo})
    t = idx[40]
    b = stocks.sector_beta(sm, sec, mk, t)
    assert abs(b["HI"] - 2.0) < 0.3 and abs(b["LO"] - 0.5) < 0.3
    sm2 = sm.copy(); sm2.loc[sm2.index > t] = 1e9                                 # t 이후를 망가뜨려도 같다
    assert stocks.sector_beta(sm2, sec, mk, t).equals(b)
    assert stocks.sector_beta(sm.iloc[:10], sec, mk, idx[9]).isna().all()        # 관측 부족 → NaN


# ---------------------------------------------------------------- 관계 원장(reference/relations_ledger.json)
def _tables_now() -> dict:
    from shared.data_library.global_macro import eu_imports, kr_industry, kr_ksic, relations, trade, trade_signal, us_imports
    from shared.data_library.global_macro import reading as R
    return {
        "trade.PARTNERS": {k: list(v) for k, v in trade.PARTNERS.items()}, "trade.EXTRA_TIVA": trade.EXTRA_TIVA, "trade.EXTRA_CLI": trade.EXTRA_CLI,
        "reading.PARTNERS": {k: [list(v[0]), v[1]] for k, v in R.PARTNERS.items()}, "reading.SECTOR_HS": R.SECTOR_HS, "reading.SECTOR_US": R.SECTOR_US,
        "kr_industry.SECTORS": {k: list(v) for k, v in kr_industry.SECTORS.items()}, "kr_ksic.KSIC2_TO_SECTOR": kr_ksic.KSIC2_TO_SECTOR,
        "us_imports.PARTNERS": us_imports.PARTNERS, "us_imports.PRODUCTS": us_imports.PRODUCTS, "eu_imports.PARTNERS": eu_imports.PARTNERS,
        "relations.LINK_PAIRS": [list(x) for x in relations.LINK_PAIRS], "relations.SHARE_GROUPS": relations.SHARE_GROUPS,
        "trade_signal.CLI_OF": trade_signal.CLI_OF,
    }


def test_ledger_reproduces_tables_frozen_on_20261022():
    """옛 결과 = 새 결과: 2026-10-22 코드 리터럴에서 뜬 스냅샷과 원장 접근자가 만든 표가 값·순서까지 같다.
    (사전등록 결과들이 그 표로 계산됐으므로, 원장을 바꾸면 이 fixture 도 날짜·이유와 함께 갱신해야 한다)"""
    import os
    fp = os.path.join(os.path.dirname(__file__), "fixtures", "global_macro_tables_20261022.json")
    with open(fp, encoding="utf-8") as f:
        frozen = json.load(f)
    now = _tables_now()
    assert set(now) == set(frozen)
    for k in frozen:
        assert now[k] == frozen[k], k
        if isinstance(frozen[k], dict):
            assert list(now[k]) == list(frozen[k]), f"{k} 순서"


def test_ledger_file_validates_and_has_history_fields():
    from shared.data_library.global_macro import ledger
    L = ledger.load()
    assert ledger.validate(L) == []
    assert all(e["valid_from"] and "valid_to" in e for e in L["edges"])
    s = ledger.summary()
    assert s["node_counts"]["kr_sector"] == 20 and s["edge_counts"]["country_tiva"] == 27 and s["edge_counts"]["sector_isic"] == 7 and len(s["sha256"]) == 64
    g = ledger.graph_view()
    assert g["nodes"]["kr_sector"]["U013"] == "전기·전자" and g["edge_types"]["group_member"]["from"] == "group" and len(g["edges"]) == 188


def test_ledger_validator_catches_broken_references_and_history():
    import copy
    from shared.data_library.global_macro import ledger
    L = copy.deepcopy(ledger.load())
    L["edges"].append({"type": "sector_hs", "from": "U013", "to": "99", "mode": "exclude", "valid_from": "2026-10-22", "valid_to": None})   # 갈 곳 없는 제외
    L["edges"].append({"type": "sector_kf49", "from": "U999", "to": "Chips", "valid_from": "2026-10-22", "valid_to": None})                 # 없는 업종
    L["edges"].append({"type": "country_cli", "from": "CN", "to": "X", "valid_from": "2026-10-22", "valid_to": "2026-10-01"})               # 닫힌 날짜 역순·reason 없음
    L["edges"].append({"type": "nope", "from": "CN", "to": "X", "valid_from": "2026-10-22", "valid_to": None})                              # 카탈로그에 없는 종류
    errs = ledger.validate(L)
    assert any("갈 곳 없음" in e for e in errs) and any("U999" in e for e in errs) and any("valid_from 이전" in e for e in errs)
    assert any("reason 필수" in e for e in errs) and any("카탈로그에 없음" in e for e in errs)


def test_ledger_as_of_returns_mapping_valid_at_that_date(monkeypatch):
    """대응을 바꿀 땐 옛 엣지를 닫고 새 엣지를 연다 — as_of 로 그 시점 표를 다시 만들 수 있어야 한다."""
    import copy
    from shared.data_library.global_macro import ledger
    L = copy.deepcopy(ledger.load())
    for e in L["edges"]:
        if e["type"] == "sector_kf49" and e["from"] == "U014":
            e["valid_to"], e["reason"] = "2027-01-01", "시험: 의료정밀을 LabEq 로"
    L["edges"].append({"type": "sector_kf49", "from": "U014", "to": "LabEq", "valid_from": "2027-01-01", "valid_to": None, "reason": "시험"})
    assert ledger.validate(L) == []
    monkeypatch.setattr(ledger, "load", lambda: L)
    assert ledger.kr_sectors(as_of="2026-12-31")["U014"][1] == "MedEq"
    assert ledger.kr_sectors(as_of="2027-01-01")["U014"][1] == "LabEq"
    assert ledger.kr_sectors()["U014"][1] == "LabEq"          # 기본 = 지금 유효한 것


def test_no_hardcoded_mapping_tables_outside_ledger():
    """래칫: 대응표(상대국·그룹·업종·품목·수입 시장)는 원장에만 — 모듈에 dict/list 리터럴로 적으면 실패."""
    import glob
    import os
    import re
    from shared.data_library import global_macro
    names = r"(PARTNERS|SECTORS|SECTOR_HS|SECTOR_US|PRODUCTS|KSIC2_TO_SECTOR|LINK_PAIRS|SHARE_GROUPS|EXTRA_TIVA|EXTRA_CLI)"
    literal_assign = re.compile(r"^" + names + r"\s*(:[^=\n]*)?=\s*[\{\[\(][\s\(]*[\"']", re.M)   # 괄호 뒤 바로 문자열 상수 = 리터럴(원장 읽는 컴프리헨션은 통과)
    sector_key = re.compile(r'^\s*"U0\d\d"\s*:', re.M)
    ecos_tuple = re.compile(r'^\s*"[A-Z]{2}"\s*:\s*\("[^"]+",\s*"[A-Z]{3}"', re.M)
    bad = []
    for fp in sorted(glob.glob(os.path.join(os.path.dirname(global_macro.__file__), "*.py"))):
        if os.path.basename(fp) == "ledger.py":
            continue
        src = open(fp, encoding="utf-8").read()
        for rx, what in ((literal_assign, "대응표 리터럴 대입"), (sector_key, "업종 코드 리터럴 키"), (ecos_tuple, "상대국 튜플 리터럴")):
            m = rx.search(src)
            if m:
                bad.append(f"{os.path.basename(fp)}: {what} — {m.group(0).strip()[:60]}")
    assert not bad, "대응표는 reference/relations_ledger.json 에만:\n" + "\n".join(bad)


# ---------------------------------------------------------------- 지평 곡선(국소투영, 사전등록 H)
def test_horizon_curves_find_the_horizon_where_information_lives():
    """업종 점수가 정확히 h=4 까지 누적 수익을 설명하도록 만든 가짜 자료 → IC 곡선은 1~4 양·t 통과, 5~ 는 0 근처; 판정은 '목표 지평 정합' h*≤4."""
    from shared.data_library.global_macro import reading_horizon as H
    rng = np.random.default_rng(7)
    idx = pd.date_range("2007-12-31", "2026-06-30", freq="ME")
    secs = ["U008", "U009", "U011", "U012", "U013", "U014", "U015"]
    hist, close = [], pd.DataFrame(0.0, index=idx, columns=secs + ["U001"])
    scores = {}
    for i, t in enumerate(idx[:-13]):
        sc = rng.standard_normal(len(secs))
        scores[t] = sc
        hist.append({"t": str(t.date()), "demand": {}, "sectors": {s: {"score": float(sc[k]), "score_D": float(-sc[k]), "D": float(-sc[k]), "C": None} for k, s in enumerate(secs)}})
    # 월 수익: 점수 신호가 다음 4개월에만 들어감(t+1..t+4), 그 뒤는 잡음
    ret = pd.DataFrame(rng.standard_normal((len(idx), len(secs))) * 0.02, index=idx, columns=secs)
    for t, sc in scores.items():
        p = idx.get_loc(t)
        for j in range(1, 5):
            if p + j < len(idx):
                ret.iloc[p + j] += 0.01 * sc
    close[secs] = np.exp(ret.cumsum())
    close["U001"] = 1.0

    class _D:
        pass
    D = _D(); D.close = close
    sec = H.sector_curve(D, hist, list(range(1, 9)))
    a = sec[(sec.score == "composite") & (sec.per == "all")].set_index("h")
    assert all(a.loc[h, "ic"] > 0 and a.loc[h, "t"] >= 2 for h in (1, 2, 3, 4))
    assert abs(a.loc[8, "ic_marginal"]) < 0.08                      # 8개월째 한 달 구간엔 정보 없음
    j = H.judge(sec[sec.score == "composite"], "ic", list(range(1, 9)))
    assert j["verdict"].startswith("목표 지평 정합") and 3 <= j["h_star"] <= 4 and j["run"][0] == 1
    jd = H.judge(sec[sec.score == "D"], "ic", list(range(1, 9)))    # 부호 반대 점수 → 통과 없음
    assert jd["verdict"] == "지평 정보 없음" and jd["h_star"] is None


def test_horizon_demand_curve_scores_against_mean_reversion_baseline():
    from shared.data_library.global_macro import reading_horizon as H
    idx = pd.date_range("2000-01-31", "2026-06-30", freq="ME")
    y = pd.Series(np.sin(np.arange(len(idx)) / 4.0), index=idx)            # 주기 ~25개월 → 평균 회귀가 잘 맞는 세계
    class _D:
        pass
    D = _D(); D.y = pd.DataFrame({"미국": y}); D.y_total = y
    hist = [{"t": str(t.date()), "demand": {"미국": {"delta": float(y.get(t + pd.offsets.MonthEnd(6)) - y.get(t - pd.offsets.MonthEnd(1)))}}, "total": None}
            for t in idx[12:-13]]                                            # 6개월 뒤를 정확히 아는 '완벽한 판독'
    cur = H.demand_curve(D, hist, [1, 3, 6, 9, 12])
    a = cur[cur.per == "all"].set_index("h")
    assert a.loc[6, "hit"] == 1.0 and a.loc[6, "diff"] >= 0
    assert a.loc[12, "hit"] < a.loc[6, "hit"]                                # 지평이 멀어지면 정보가 준다
    assert len(H.demand_curve(D, hist, [3], total=True)) == 3 and H.demand_curve(D, hist, [3], total=True).n_months.sum() == 0   # total 없음 → 빈 곡선


# ---------------------------------------------------------------- 측정 엣지 저장소(append-only)
def _edge_inputs():
    hist = pd.DataFrame([
        {"t": "2026-08-31", "kind": "link", "key": "미국", "state": "약함", "rho": 0.4, "k": 4.0, "call": "감속", "delta": -0.3},
        {"t": "2026-08-31", "kind": "link", "key": "호주", "state": "끊김", "rho": 0.1, "k": 9.0, "call": np.nan, "delta": np.nan},
        {"t": "2026-08-31", "kind": "sector", "key": "U013", "state": "우호", "rho": 0.9, "k": np.nan, "call": "우호", "delta": -0.07},
        {"t": "2026-09-30", "kind": "link", "key": "미국", "state": "약함", "rho": 0.41, "k": 4.0, "call": "감속", "delta": -0.4},
    ])
    reading = {"t": "2026-09-30", "exposure_by_year": {"2025": {"U013": {"미국": 0.13, "중국+홍콩": 0.36}}},
               "stocks": {"U013": {"stocks": [{"code": "A000660", "name": "SK하이닉스", "beta": 1.69, "mcap_bn": 1.0, "ret_6m": 49.3}]}}}
    relations = {"export_shares": {"미국": [["2026-07-31", 17.5], ["2026-08-31", 17.8]]},
                 "us_competition": {"8542": {"series": [["한국", [[f"{y}-{m:02d}-28", 5.0 + y - 2020] for y in (2021, 2022, 2023, 2024, 2025) for m in range(1, 13)]]]}}}
    influence = {"rolling": [{"date": "2026-09-05", "to_kr": {"US": 8.0, "KR": 55.0}}, {"date": "2026-09-26", "to_kr": {"US": 8.8, "KR": 54.0}}]}
    return hist, reading, relations, influence


def test_edge_store_is_append_only_and_keeps_first_record(stage):
    from shared.data_library.global_macro import edge_store as ES
    hist, reading, relations, influence = _edge_inputs()
    info = ES.sync(hist, reading, relations, influence)
    assert info["rows"] == info["added"] > 0 and info["latest"] == "2026-09-30"
    first = ES.read()
    assert set(first["type"]) == {"link", "demand", "sector_reading", "export_share", "sector_exposure", "equity_spillover", "import_share", "stock_beta"}
    assert (first[first.type == "demand"].src == "g:미국").all()                    # 끊긴 호주는 수요 엣지 없음
    hist2 = hist.copy(); hist2.loc[0, "rho"] = 0.99                                # 재계산으로 과거 값이 바뀌어도
    info2 = ES.sync(hist2, reading, relations, influence)
    assert info2["added"] == 0
    df = ES.read(); df["t"] = pd.to_datetime(df["t"])
    row = df[(df.type == "link") & (df.t == pd.Timestamp("2026-08-31")) & (df.src == "g:미국")].iloc[0]
    assert row.weight == 0.4                                                         # 처음 기록이 남는다
    # 그 달에 보이는 엣지: 월별은 같은 달, 노출은 (t−1 월의 전년) 12-31, 베타는 최신 달만, 주가 연결도는 달의 마지막 창
    a = ES.at("2026-09-30")
    assert set(a[a.type == "sector_exposure"].t.astype(str).str[:4]) == {"2025"}
    assert (a[a.type == "equity_spillover"].weight == 8.8).all()
    assert len(a[a.type == "stock_beta"]) == 1 and len(ES.at("2026-08-31", ["stock_beta"])) == 0
    p = ES.payload()
    assert p["t"] == "2026-09-30" and "link" in p["units"] and any(e["type"] == "demand" and e["state"] == "감속" for e in p["edges"])
    imp = [e for e in ES.payload("2025-12-28")["edges"] if e["type"] == "import_share"]
    assert imp and imp[0]["evidence"]["chg3y"] == pytest.approx(3.0)              # 2025 − 2022


# ---------------------------------------------------------------- 회사별 수출 비중 파서(DART 사업보고서 표)
def _xml_table(title, rows):
    body = "".join("<TR>" + "".join(f"<TD>{c}</TD>" for c in r) + "</TR>" for r in rows)
    return f"<P>{title}</P><TABLE>{body}</TABLE>"


def test_export_share_parser_handles_row_col_region_and_rowspan_layouts():
    from shared.data_library.global_macro import kr_export_share as X
    # (1) 제품별 수출/내수 행 + 끝에 합계 블록(합계 행은 나머지 합과 같아 한 번만 센다)
    t1 = _xml_table("가. 매출실적 (단위 : 백만원)", [["사업부문", "품목", "구분", "제50기", "제49기"], ["A", "a", "내수", "100", "90"], ["수출", "300", "280"], ["합계", "400", "370"],
                                                     ["B", "b", "내수", "50", "40"], ["수출", "150", "120"], ["합계", "200", "160"], ["합 계", "내수", "150", "130"], ["수출", "450", "400"], ["합계", "600", "530"]])
    p = X.parse_xml(t1)
    assert p["method"] == "rows" and p["export"] == 450 and p["domestic"] == 150 and p["share"] == pytest.approx(0.75) and p["verified"]
    # (2) 열 방향: 머리글 수출/내수, 합계 행
    t2 = _xml_table("가. 매출실적 (단위 : 백만원)", [["품목", "수출", "내수", "합계"], ["a", "30", "70", "100"], ["b", "10", "90", "100"], ["합계", "40", "160", "200"]])
    p = X.parse_xml(t2)
    assert p["method"] == "cols" and p["share"] == pytest.approx(0.2) and p["verified"]
    # (3) 국가별 표: 한국 행 = 내수, 나머지 나라 = 수출(소계·합계·연결조정은 제외)
    t3 = _xml_table("(3) 지역별 매출 (단위: 천원)", [["지역", "당기", "전기"], ["국내", "600", "500"], ["국외"], ["일본", "100", "80"], ["북미", "300", "250"], ["소 계", "400", "330"], ["합 계", "1,000", "830"]])
    p = X.parse_xml(t3)
    assert p["method"] == "region" and p["share"] == pytest.approx(0.4) and p["verified"]
    # (4) ROWSPAN: 국내/해외 라벨 아래 라벨 없는 품목 행이 이어진다
    t4 = _xml_table("가. 매출실적 (단위 : 억원)", [["사업부문", "품목", "2025년", "2024년"], ["철강", "국내", "열연", "100", "90"], ["냉연", "100", "95"], ["해외", "열연", "300", "280"], ["냉연", "500", "450"], ["단순합계", "1,000", "915"]])
    p = X.parse_xml(t4)
    assert p["method"] == "rows" and p["export"] == 800 and p["domestic"] == 200 and p["verified"]
    # (5) 가격표·채권표·비중(%)표는 매출이 아니다
    bad = _xml_table("나. 가격변동 추이 (단위 : 원/kg)", [["품목", "구분", "2025년"], ["a", "수출", "1,200"], ["내수", "1,100"]]) + \
          _xml_table("지역별 신용위험 노출 (단위 : 천원)", [["구분", "당기"], ["국내", "1,000"], ["해외", "500"]]) + \
          _xml_table("[부문별 주요제품]", [["사업부문", "국가", "매출비중(%)"], ["a", "한국", "3.2"], ["b", "미국", "28.0"]])
    assert X.parse_xml(bad) is None
    # (6) 가격표와 매출표가 같이 있으면 매출표를 고른다
    assert X.parse_xml(bad + t2)["method"] == "cols"


def test_export_share_reads_cp949_filings_declared_as_utf8():
    """옛 공시(~2019)는 헤더가 utf-8 이어도 실제 cp949 — 본문·첨부 전부 읽히고 표가 파싱돼야 한다."""
    import io
    import zipfile
    from shared.data_library.global_macro import kr_export_share as X
    body = '<?xml version="1.0" encoding="utf-8"?><DOCUMENT>' + _xml_table("가. 매출실적 (단위 : 백만원)", [["품목", "수출", "내수", "합계"], ["a", "30", "70", "100"], ["합계", "30", "70", "100"]]) + "</DOCUMENT>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("20190401000001.xml", body.encode("cp949"))
        z.writestr("20190401000001_00760.xml", "<DOCUMENT>첨부</DOCUMENT>".encode("cp949"))
    xml = X.main_xml(buf.getvalue())
    assert "매출실적" in xml and xml.index("매출실적") < xml.index("첨부")       # 본문이 첨부보다 앞
    assert X.parse_xml(xml)["share"] == pytest.approx(0.3)


# ---------------------------------------------------------------- 이슈 원장(수집·파싱·빌드 문제를 한 곳에)
def test_issue_ledger_event_and_state_semantics(stage):
    from shared.data_library.global_macro import issues as I
    # 사건형: 같은 키는 upsert(count 증가), 성공 시 닫힘·history 에 남음
    I.step_failed("trade", RuntimeError("ECOS 500"))
    I.step_failed("trade", RuntimeError("ECOS 500 again"))
    s = I.summary()
    assert s["total"] == 1 and I.load()[0]["count"] == 2 and I.load()[0]["detail"]["error"].startswith("ECOS 500 again")
    I.step_ok("trade")
    assert I.summary()["total"] == 0
    # 상태형: replace 는 사라진 키를 해결, 새 키를 열고, 남은 키는 유지
    r1 = I.replace("export_share:2025", [{"kind": "no_table", "key": "A1:no_table", "title": "A1"}, {"kind": "jump", "key": "A2:jump", "title": "A2"}])
    r2 = I.replace("export_share:2025", [{"kind": "jump", "key": "A2:jump", "title": "A2"}, {"kind": "unverified", "key": "A3:unverified", "title": "A3"}])
    assert (r1["opened"], r1["resolved"]) == (2, 0) and (r2["opened"], r2["resolved"]) == (1, 1)
    assert {it["key"] for it in I.load()} == {"A2:jump", "A3:unverified"}
    hist = [json.loads(l) for l in open(I._p("history.jsonl"), encoding="utf-8")]
    assert [h["event"] for h in hist].count("resolved") == 2            # trade 해결 + A1 해결
    # 다이제스트: 원천 × 종류 표와 예가 들어간다; 고칠 수 없는 종류는 fixable 에서 빠진다
    I.from_fail_list("kr_ksic", ["A000020 corp_code 없음"], kind="no_source")
    txt = open(I.write_digest(), encoding="utf-8").read()
    assert "export_share:2025" in txt and "kr_ksic" in txt and "A3" in txt
    assert I.summary()["fixable"] == 1 and I.summary()["review"] == 1 and I.summary()["total"] == 3   # jump 는 고칠 것, unverified 는 확인 대상, no_source 는 원천 없음


# ---------------------------------------------------------------- R5-V 부가가치 가중 수요(전진 트랙 V)
def test_track_v_weights_demand_by_value_added_exposure_and_respects_publication_lag():
    from shared.data_library.global_macro import reading_ext as X
    from shared.data_library.global_macro import tiva_industry as TI
    secs = {s: {"name": s, "score": 0.0} for s in ("U008", "U009", "U011", "U012", "U013")}
    dem = {"미국": {"delta": 0.10}, "중국+홍콩": {"delta": -0.20}}
    expo = {2022: {"U013": {"미국": 0.2, "중국+홍콩": 0.35}, "U009": {"미국": 0.25, "중국+홍콩": 0.05}, "U008": {"미국": 0.18, "중국+홍콩": 0.25},
                   "U011": {"미국": 0.22, "중국+홍콩": 0.16}, "U012": {"미국": 0.2, "중국+홍콩": 0.19}}}
    r = X.attach_v({"t": "2026-09-30", "sectors": secs, "demand": dem}, expo)
    assert r["va_year"] == 2022 and r["sectors"]["U009"]["reading_V"] == "우호" and r["sectors"]["U013"]["reading_V"] == "비우호"
    assert r["sectors"]["U009"]["D_VA"] == pytest.approx(0.25 * 0.10 - 0.05 * 0.20)
    import copy
    r2 = X.attach_v({"t": "2024-06-30", "sectors": copy.deepcopy({s: {"name": s, "score": 0.0} for s in secs}), "demand": dem}, expo)      # 2022 는 2025-11 공표 → 2024 시점엔 못 씀
    assert "va_year" not in r2 and "score_V" not in r2["sectors"]["U009"]
    assert TI.year_available("2026-01-31", [2019, 2020, 2021, 2022]) == 2022 and TI.year_available("2010-01-31", [2019]) is None
