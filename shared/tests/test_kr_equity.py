"""kr_equity 수집·패널 모듈 — 임시 폴더로만 검사(실제 DataVault·네트워크 안 씀)."""
import json
import os
import zipfile

import numpy as np
import pandas as pd
import pytest

from shared.data_library.kr_equity import panel, runner, sources, store


@pytest.fixture
def stage(tmp_path, monkeypatch):
    root = tmp_path / "staging" / "kr_equity"
    root.mkdir(parents=True)
    monkeypatch.setattr(store, "stage", lambda: str(root))
    return root


def _corpcode_zip(root, rows):
    xml = "<result>" + "".join(f"<list><corp_code>{c}</corp_code><corp_name>{n}</corp_name><stock_code>{s}</stock_code>"
                                f"<modify_date>20260101</modify_date></list>" for c, s, n in rows) + "</result>"
    (root / "raw" / "dart").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(root / "raw" / "dart" / "corpCode.zip", "w") as z:
        z.writestr("CORPCODE.xml", xml)


# ---------------------------------------------------------------- store
def test_write_once_is_immutable_unless_replace(stage):
    fp = str(stage / "raw" / "x" / "a.json")
    assert store.write_once(fp, b"1") is True
    assert store.write_once(fp, b"2") is False
    assert open(fp, "rb").read() == b"1"
    assert oct(os.stat(fp).st_mode & 0o777) == "0o444"
    assert store.write_once(fp, b"3", replace=True) is True
    assert open(fp, "rb").read() == b"3"


def test_job_tracks_counts_and_caps_failed_list(stage):
    j = store.Job("t", "테스트", 100, "건")
    for i in range(60):
        j.tick(failed=f"f{i}")
    j.tick(fetched=True)
    j.tick(skipped=True)
    j.finish()
    s = json.load(open(stage / "collect" / "t.json", encoding="utf-8"))
    assert (s["done"], s["fetched"], s["skipped"], s["failed_n"], len(s["failed"]), s["finished"]) == (62, 1, 1, 60, 50, True)
    j2 = store.Job("t", "테스트", 1, "건")
    assert j2.s["first_started_at"] == s["first_started_at"]


def test_build_universe_prefers_listed_unless_delisted_after_snapshot(stage):
    (stage / "reference").mkdir()
    (stage / "reference" / "listed_snapshot.csv").write_text(
        "code,name,market,section,is_preferred,is_spac,control_kind,supervision_kind,status_kind,listed_date,snapshot_date\n"
        "A000010,가,KOSPI,STOCK,0,0,0,0,0,19900101,2026-10-11\n"
        "A000020,나,KOSDAQ,STOCK,0,0,0,0,0,20000101,2026-10-11\n", encoding="utf-8")
    (stage / "reference" / "delisted.csv").write_text(
        "code,name,market,delist_date,reason,kind_code,code_match,no\n"
        "000020,나,KOSDAQ,2026-10-12,시총미달,00002,name,1\n"       # 스냅샷 뒤 폐지 → 상폐
        "000010,가,KOSPI,2015-01-01,재상장전,00001,name,2\n"        # 스냅샷 전 폐지 뒤 재상장 → 상장 유지
        "000030,다,KONEX,2020-01-01,x,00003,name,3\n"             # 코넥스 제외
        "000040,라,KOSPI,2018-05-05,해산,00004,name,4\n", encoding="utf-8")
    n = store.build_universe()
    u = {r["code"]: r for r in store.read_universe()}
    assert n == {"listed": 1, "delisted": 2}
    assert u["A000010"]["status"] == "listed" and u["A000020"]["status"] == "delisted"
    assert u["A000040"]["price_source"] == "naver" and "A000030" not in u
    assert (stage / "targets_creon.txt").read_text().split() == ["A000010"]


def test_universe_corps_maps_preferred_to_common(stage):
    (stage / "universe.csv").write_text("code,name,market,status,is_preferred,is_spac,listed_date,delist_date,delist_reason,price_source\n"
                                        "A005935,삼성전자우,KOSPI,listed,1,0,,,,creon\n", encoding="utf-8")
    _corpcode_zip(stage, [("00126380", "005930", "삼성전자"), ("00000001", "000001", "기타")])
    assert store.universe_corps() == {"00126380": "005930"}


# ---------------------------------------------------------------- sources
def test_parse_kind_rows():
    html = ('<tr><td class="first txc">7</td><td><img alt=\'유가증권\'> <a onclick="companysummary_open(\'00503\');" title=\'부산주공\'>'
            ' 부산주공</a></td><td class="txc">2026-10-12</td><td>기업의  계속성 <b>등</b></td><td></td></tr>')
    r = sources.parse_kind(html)
    assert r == [{"no": 7, "kind_code": "00503", "name": "부산주공", "market": "KOSPI", "delist_date": "2026-10-12", "reason": "기업의 계속성 등"}]


def test_fnltt_chunks_are_stable_when_corps_added(stage):
    a = sources._fnltt_chunks([f"{i:08d}" for i in range(150)])
    assert [len(c) for c in a] == [100, 50]
    b = sources._fnltt_chunks([f"{i:08d}" for i in range(160)])
    assert b[0] == a[0] and b[1][:50] == a[1] and len(b[1]) == 60       # 기존 묶음 유지, 새 회사는 마지막에
    c = sources._fnltt_chunks([f"{i:08d}" for i in range(260)])
    assert [len(x) for x in c] == [100, 100, 60]


def test_steps_registry_and_runner_commands(monkeypatch):
    keys = [s["key"] for s in sources.STEPS]
    assert "panel" in keys and "creon_daily" in keys and len(keys) == len(set(keys))
    argv, cwd = runner.command("dart_list")
    assert argv[-2:] == ["shared.data_library.kr_equity.cli", "dart_list"]
    monkeypatch.setattr(runner, "_py_launcher", lambda: ["py.exe", "-3.10-32"])
    argv, cwd = runner.command("creon_daily_ext", full=True)
    assert argv[:3] == ["py.exe", "-3.10-32", "universe_driver.py"] and "--full" in argv and "--stage" in argv
    assert cwd.endswith(os.path.join("CAiModelBaker", "core"))
    monkeypatch.setattr(runner, "_py_launcher", lambda: None)
    with pytest.raises(RuntimeError):
        runner.command("creon_index")


def test_runner_lock_clears_dead_pid(stage):
    (stage / "collect").mkdir()
    (stage / "collect" / ".running.json").write_text(json.dumps({"step": "x", "pid": 999999999}))
    assert runner.running() is None
    assert not (stage / "collect" / ".running.json").exists()
    (stage / "collect" / ".running.json").write_text(json.dumps({"step": "y", "pid": os.getpid()}))
    assert runner.running()["step"] == "y"


# ---------------------------------------------------------------- panel
def test_period_end_parses_both_forms():
    assert panel.period_end_of("2024.01.01 ~ 2024.06.30") == "2024-06-30"
    assert panel.period_end_of("2024.06.30 현재") == "2024-06-30"
    assert panel.period_end_of("") is None


def test_derive_q4_from_annual_minus_q3_cumulative():
    w = pd.DataFrame({"stock_code": ["1"] * 4, "bsns_year": [2024] * 4, "reprt_code": ["11013", "11012", "11014", "11011"],
                      "q": [1, 2, 3, 4], "sales": [10.0, 20.0, 30.0, 100.0], "sales_cum": [10.0, 30.0, 60.0, np.nan]})
    for k in ("op_income", "net_income", "pretax_income"):
        w[k] = np.nan
    out = panel.derive_q4(w)
    r = out[out.q == 4].iloc[0]
    assert r.sales == 40.0 and r.sales_annual == 100.0 and r.sales_cum == 100.0
    assert out[out.q < 4].sales.tolist() == [10.0, 20.0, 30.0]


def test_parse_naver_and_shares():
    d = panel.parse_naver(b'<x><item data="20170428|150|158|110|117|18522676" /><item data="20170502|98|106|79|79|11917330" /></x>')
    assert list(d.close) == [117.0, 79.0] and str(d.date.iloc[0].date()) == "2017-04-28"
    assert panel.parse_naver(b"<x></x>").empty
    j = {"response": {"list": [{"se": "보통주", "rcept_no": "20170331004360", "distb_stock_co": "26,177,380"},
                               {"se": "합계", "rcept_no": "20170331004360", "distb_stock_co": "26,177,380"}]}}
    assert panel.parse_shares_file(j) == ("20170331", 26177380.0)
    assert panel.parse_shares_file({"response": {"list": [{"se": "비고", "rcept_no": "1", "distb_stock_co": "-"}]}}) is None


def test_read_creon_restores_regular_session_from_1m(stage, monkeypatch):
    from shared.data_library import paths
    from shared.data_library.kr_equity import bronze
    monkeypatch.setattr(paths, "DATA_VAULT_ROOT", str(stage.parent))
    monkeypatch.setattr(bronze, "LANDING", (stage.name, "raw", "creon"))
    base = stage / "raw" / "creon"
    (base / "candle_1d").mkdir(parents=True)
    (base / "candle_1d_unadj").mkdir(parents=True)
    hdr = "timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume,upbit_trade_price\n"
    # 2:1 분할이 09-14 에 반영 → 09-11 수정가는 무수정의 2배(계수 2), 09-14 이후는 같다
    adj = "2026-09-11 00:00:00,200,220,180,200,10,1000\n2026-09-14 00:00:00,100,120,95,118,30,3000\n2026-09-15 00:00:00,118,119,117,119,5,500\n"
    raw = "2026-09-11 00:00:00,100,110,90,100,10,1000\n2026-09-14 00:00:00,100,120,95,118,30,3000\n2026-09-15 00:00:00,118,119,117,119,5,500\n"
    (base / "candle_1d" / "A000001_1d.csv").write_text(hdr + adj)
    (base / "candle_1d_unadj" / "A000001_1d.csv").write_text(hdr + raw)
    bronze.ingest_code("A000001", log=lambda *_: None)
    m1 = stage / "m1" / "A000001"
    m1.mkdir(parents=True)
    (m1 / "2026-09-14.csv").write_text("timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume,upbit_trade_price\n"
                                       "2026-09-14 09:01:00,100,105,99,104,10,1000\n2026-09-14 15:30:00,104,108,103,106,15,1500\n"
                                       "2026-09-14 20:00:00,110,120,110,118,5,500\n")
    monkeypatch.setattr(panel, "M1_STORE", str(stage / "m1"))
    d = panel.read_creon("A000001").set_index("date")
    assert d.loc["2026-09-11", "close"] == pytest.approx(200) and d.loc["2026-09-11", "reg_fix"] == 0
    assert d.loc["2026-09-14", "close"] == pytest.approx(106) and d.loc["2026-09-14", "close_unadj"] == 106      # 정규장 종가 × 계수 1
    assert d.loc["2026-09-14", "volume"] == 25 and d.loc["2026-09-14", "reg_fix"] == 1
    assert np.isnan(d.loc["2026-09-15", "close"]) and d.loc["2026-09-15", "reg_fix"] == 2                       # 1분봉 없음 → NaN


# ---- Creon 일봉 원천 적재 (임시 DataVault 로만)
def _landing(tmp, code, adj_close, raw_close):
    import os
    for kind, closes in (("candle_1d", adj_close), ("candle_1d_unadj", raw_close)):
        d = tmp / "staging" / "kr_equity" / "raw" / "creon" / kind
        os.makedirs(d, exist_ok=True)
        ts = pd.bdate_range("2024-01-02", periods=len(closes))
        pd.DataFrame({"timestamp": [t.strftime("%Y-%m-%d 00:00:00") for t in ts], "upbit_open": closes, "upbit_high": closes,
                      "upbit_low": closes, "upbit_close": closes, "upbit_volume": 1000, "upbit_trade_price": 0}
                     ).to_csv(d / f"{code}_1d.csv", index=False)


def test_creon_bronze_ingest_is_unadjusted_and_idempotent(tmp_path, monkeypatch):
    from shared.data_library import paths
    from shared.data_library.kr_equity import bronze
    monkeypatch.setattr(paths, "DATA_VAULT_ROOT", str(tmp_path))
    # 4:1 병합이 5번째 날 반영: 수정 종가는 병합 전 날이 4배
    raw_c = [1000.0] * 4 + [4000.0] * 6
    adj_c = [4000.0] * 10
    _landing(tmp_path, "A000001", adj_c, raw_c)
    r1 = bronze.ingest_code("A000001", log=lambda *_: None)
    out = pd.read_csv(bronze.unadj_path("A000001"))
    assert list(out.columns) == bronze.COLUMNS and out["close"].tolist() == raw_c       # 원천은 무수정
    assert r1["events_added"] == 1
    r2 = bronze.ingest_code("A000001", log=lambda *_: None)
    assert r2["added_rows"] == 0 and r2["events_added"] == 0                           # 다시 돌려도 같은 결과
    from shared.data_library import corporate_actions as ca
    ev = ca.events(bronze.actions_root(), "A000001")
    assert abs(ev[0]["price_factor"] - 4.0) < 0.01


def test_silver_price_is_raw_times_ledger_factor(tmp_path, monkeypatch):
    from shared.data_library import paths
    from shared.data_library.kr_equity import bronze, panel
    monkeypatch.setattr(paths, "DATA_VAULT_ROOT", str(tmp_path))
    monkeypatch.setattr(panel, "NXT_FROM", pd.Timestamp("2100-01-01"))
    raw_c = [1000.0] * 4 + [4000.0] * 6
    _landing(tmp_path, "A000001", [4000.0] * 10, raw_c)
    bronze.ingest_code("A000001", log=lambda *_: None)
    d = panel.read_creon("A000001")
    assert np.allclose(d.close, 4000.0, rtol=0.01) and d.close_unadj.tolist() == raw_c   # 수정가는 원장 계수로, 무수정가는 그대로
    assert np.allclose(d.volume[:4], 250.0, rtol=0.01) and (d.reg_fix == 0).all()


# ---- 골드 (지표는 그날까지의 자료만, 라벨은 분리)
def _px(n=400, seed=0):
    rng = np.random.default_rng(seed)
    close = 1000 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    d = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame({"code": "A000001", "date": d, "open": close, "high": close, "low": close, "close": close,
                         "volume": rng.integers(1000, 5000, n).astype(float), "value": close * 1000, "close_unadj": close,
                         "factor": 1.0, "reg_fix": np.int8(0), "src": "creon"})


def test_gold_features_use_only_past_data():
    from shared.data_library.kr_equity import gold
    p = _px()
    full = gold.build_features(p).set_index("date")
    cut = gold.build_features(p[p.date <= p.date.iloc[299]]).set_index("date")      # 미래 100봉을 지운 패널
    cols = [c for c in cut.columns if c not in ("code", "reg_fix", "resumed")]
    a, b = full.loc[cut.index, cols].astype("float64"), cut[cols].astype("float64")
    assert np.allclose(a.to_numpy(), b.to_numpy(), equal_nan=True, rtol=1e-4)       # 미래를 지워도 과거 지표 불변


def test_gold_labels_enter_next_close_and_respect_halts():
    from shared.data_library.kr_equity import gold
    p = _px(60)
    lab = gold.build_labels(p).set_index("date")
    c = p.set_index("date").close
    d = p.date
    assert lab.lab_ret_5.iloc[3] == pytest.approx(np.log(c.iloc[3 + 1 + 5] / c.iloc[3 + 1]), rel=1e-4)    # t+1 종가 진입
    assert np.isnan(lab.lab_ret_20.iloc[-5])                                                              # 미래 없음
    h = p.copy()
    h.loc[h.index >= 30, "date"] = h.loc[h.index >= 30, "date"] + pd.Timedelta(days=60)                  # 30번째 봉 앞에 두 달 공백(거래정지)
    lab2 = gold.build_labels(h)
    assert lab2.lab_ret_5.iloc[24:29].isna().all() and not np.isnan(lab2.lab_ret_5.iloc[29]) and not np.isnan(lab2.lab_ret_5.iloc[10])           # 공백을 가로지르는 라벨은 NaN


def test_gold_zero_volume_halt_is_not_a_return():
    """Creon 은 거래정지 기간을 거래량 0·가격 고정 봉으로 채운다 — 재개일 점프는 수익률·라벨에 들어가면 안 된다."""
    from shared.data_library.kr_equity import gold
    p = _px(120)
    halt = (p.index >= 50) & (p.index < 90)                     # 40봉 정지(달력 56일)
    p.loc[halt, "volume"] = 0.0
    p.loc[halt, ["open", "high", "low", "close"]] = p.close.iloc[49]
    p.loc[p.index >= 90, ["open", "high", "low", "close"]] *= 3.0          # 재개일 +200% 점프
    f = gold.build_features(p).set_index(p.date)
    assert f.resumed.iloc[90] == 1 and np.isnan(f.ret_1.iloc[90]) and np.isnan(f.ret_20.iloc[95])
    assert f.ret_1.iloc[50:90].isna().all() and (f.traded.iloc[50:90] == 0).all()
    assert np.nanmax(np.abs(f.ret_1.to_numpy())) < 0.2                    # 점프가 어디에도 안 남음
    lab = gold.build_labels(p)
    assert lab.lab_ret_5.iloc[44:89].isna().all()                         # 정지 중 진입·정지를 가로지르는 보유 = NaN
    assert not lab.lab_ret_5.iloc[[43, 89]].isna().any()                  # 정지 전에 끝나는 보유·재개일 진입은 유효
    assert not np.isnan(lab.lab_ret_5.iloc[95]) and not np.isnan(lab.lab_ret_5.iloc[30])


def test_operating_adjusted_from_unadj_and_ledger(tmp_path, monkeypatch):
    """운용 종목: 수정 일봉(upbit_* 열, 원장 근거) + 무수정 원천(표준 열) → 원장 갱신, 수정가 = 무수정 × 계수."""
    from shared.data_library import paths
    from shared.data_library.kr_equity import bronze
    monkeypatch.setattr(paths, "DATA_VAULT_ROOT", str(tmp_path))
    ts = [t.strftime("%Y-%m-%d 00:00:00") for t in pd.bdate_range("2024-01-02", periods=8)]
    raw_c = [100.0] * 3 + [50.0] * 5                         # 1:2 분할
    d = tmp_path / "raw" / "creon"
    (d / "candle_1d").mkdir(parents=True)
    (d / "candle_1d_unadj").mkdir(parents=True)
    pd.DataFrame({"timestamp": ts, "upbit_open": 50.0, "upbit_high": 50.0, "upbit_low": 50.0, "upbit_close": 50.0,
                  "upbit_volume": 10, "upbit_trade_price": 0}).to_csv(d / "candle_1d" / "A069500_1d.csv", index=False)
    pd.DataFrame({"timestamp": ts, "open": raw_c, "high": raw_c, "low": raw_c, "close": raw_c, "volume": 10.0,
                  "value": 1000.0}).to_csv(d / "candle_1d_unadj" / "A069500_1d.csv", index=False)
    out = bronze.operating_adjusted("A069500")
    assert np.allclose(out.close, 50.0, rtol=0.01) and out.close_unadj.tolist() == raw_c
    assert np.allclose(out.volume[:3], 20.0, rtol=0.01) and np.allclose(out.factor[3:], 1.0)
    assert bronze.operating_adjusted("A122630") is None               # 무수정 원천 없으면 None → 옛 방식


def test_creon_daily_steps_chain_bronze(monkeypatch):
    """Creon 일봉 단계는 Linux 감싸개로 실행돼, Windows 수집이 성공했을 때만 원천 적재(creon_bronze)가 이어진다."""
    import subprocess
    from shared.data_library.kr_equity import cli, runner, sources as src
    monkeypatch.setattr(runner, "_py_launcher", lambda: ["py.exe", "-3.10-32"])
    argv, _ = runner.command("creon_daily_unadj")
    assert argv[1:4] == ["-m", "shared.data_library.kr_equity.cli", "creon_then_bronze"] and argv[4] == "creon_daily_unadj"
    argv, cwd = runner.command("creon_daily_ext")                         # 원천 적재와 무관한 단계는 그대로
    assert argv[0] == "py.exe" and cwd == runner.CREON_DRIVER_DIR
    ran = []
    monkeypatch.setattr(src, "run", lambda step: ran.append(step) or True)
    monkeypatch.setattr(src, "log", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "call", lambda *a, **k: 1)
    assert cli.creon_then_bronze("creon_daily_unadj", False) == 1 and ran == []      # 수집 실패 → 적재 안 함
    monkeypatch.setattr(subprocess, "call", lambda *a, **k: 0)
    assert cli.creon_then_bronze("creon_daily_unadj", False) == 0 and ran == ["creon_bronze"]
