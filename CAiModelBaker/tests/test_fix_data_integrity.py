# -*- coding: utf-8 -*-
"""데이터 계층 수정(이슈 4~13) 회귀 테스트 — 실제 DataVault 는 건드리지 않는다(tmp_path 사용)."""
import os
import threading

import pandas as pd
import pytest

from CAiModelBaker.core import data_manager as dm
from CAiModelBaker.core import creon_collector as cc
from CAiModelBaker.core import overseas_futures_collector as ofc


# ---- 5. 심볼 정규화 ---------------------------------------------------------
def test_clean_symbol_preserves_tickers():
    assert dm._clean_sym("AAPL") == "AAPL"
    assert dm._clean_sym("A069500") == "069500"
    assert cc._clean_sym("AAPL") == "AAPL"
    assert cc._clean_sym("A069500") == "069500"


# ---- 4. 원자적 쓰기 / dedup -------------------------------------------------
def test_atomic_write_csv_replaces_and_cleans_tmp(tmp_path):
    p = tmp_path / "a.csv"
    dm._atomic_write_csv(pd.DataFrame({"x": [1]}), str(p), index=False)
    assert pd.read_csv(p)["x"].tolist() == [1]
    assert [f.name for f in tmp_path.iterdir()] == ["a.csv"]

    class Boom:
        def to_csv(self, *a, **k):
            raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        dm._atomic_write_csv(Boom(), str(p))
    assert pd.read_csv(p)["x"].tolist() == [1]          # 원본 보존
    assert [f.name for f in tmp_path.iterdir()] == ["a.csv"]  # tmp 정리


def test_creon_merge_pure_backfill_and_last_bar_update(tmp_path, monkeypatch):
    """pandas 없는 경로: back-fill 행 삽입 + 같은 ts 마지막 봉은 신규가 우선"""
    import builtins
    real_import = builtins.__import__

    def no_pandas(name, *a, **k):
        if name == "pandas":
            raise ImportError
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_pandas)

    tgt = tmp_path / "t.csv"
    tgt.write_text("timestamp,close\n2024-01-01 09:00:00,1\n2024-01-01 09:02:00,3\n2024-01-01 09:03:00,4\n")
    new = tmp_path / "n.csv"
    new.write_text("timestamp,close\n2024-01-01 09:01:00,2\n2024-01-01 09:03:00,44\n2024-01-01 09:04:00,5\n")
    cc.merge_and_save_data(str(new), str(tgt), subset_keys=["timestamp"], local_log=lambda *_: None)
    monkeypatch.setattr(builtins, "__import__", real_import)
    df = pd.read_csv(tgt)
    assert df["timestamp"].tolist() == [f"2024-01-01 09:0{i}:00" for i in range(5)]
    assert df["close"].tolist() == [1, 2, 3, 44, 5]
    assert not [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]


def test_creon_uniq_tmp_differs():
    assert cc._uniq_tmp("/x/a.csv", "raw") != cc._uniq_tmp("/x/a.csv", "raw")


# ---- 10. 해외 참조 시프트 토큰 매칭 ------------------------------------------
def test_overseas_shift_tokens():
    hit, d = dm._overseas_shift_delta("nq_60m.csv")
    assert hit and d == pd.Timedelta(hours=1)
    hit, d = dm._overseas_shift_delta("candles_a069500_1m.csv")
    assert not hit                                   # 'es' 부분문자열 오탐 없음
    assert d == pd.Timedelta(minutes=1)
    hit, d = dm._overseas_shift_delta("es_15m.csv")
    assert hit and d == pd.Timedelta(minutes=15)
    _, d = dm._overseas_shift_delta("x_1m5m.csv")    # 부분문자열 오탐 방지
    assert d is None


# ---- 11. tz-aware -> KST ---------------------------------------------------
def test_to_kst_naive():
    aware = pd.Series(["2024-01-01T00:00:00+00:00", "2024-01-01T01:00:00+00:00"])
    out = dm._to_kst_naive(aware)
    assert out.iloc[0] == pd.Timestamp("2024-01-01 09:00:00")
    naive = pd.Series(["2024-01-01 09:00:00"])
    assert dm._to_kst_naive(naive).iloc[0] == pd.Timestamp("2024-01-01 09:00:00")
    mixed = pd.Series(["2024-01-01T00:00:00+00:00", "2024-01-01T09:00:00+09:00"])
    assert dm._to_kst_naive(mixed).iloc[1] == pd.Timestamp("2024-01-01 09:00:00")


def test_import_external_csv_converts_offset(tmp_path, monkeypatch):
    monkeypatch.setattr(ofc, "raw_target", lambda kind, sym, iv: str(tmp_path / "raw" / f"{sym}_{iv}.csv"))
    src = tmp_path / "s.csv"
    src.write_text("datetime,close\n2024-01-01T00:00:00+00:00,10\n")
    out = ofc.import_external_futures_csv(str(src), "NQ", "1m", baker_root=str(tmp_path), local_log=lambda *_: None)
    assert pd.read_csv(out)["timestamp"].iloc[0] == "2024-01-01 09:00:00"


# ---- 6. 미완성 봉 / 병합 실패 시 아카이브 보존 --------------------------------
def test_drop_incomplete_last_bar():
    now = pd.Timestamp("2024-01-01 10:00:30")
    df = pd.DataFrame({"timestamp": ["2024-01-01 09:58:00", "2024-01-01 09:59:00", "2024-01-01 10:00:00"]})
    out = ofc._drop_incomplete_last_bar(df, "1m", now_kst=now)
    assert out["timestamp"].tolist() == ["2024-01-01 09:58:00", "2024-01-01 09:59:00"]


def test_futures_save_failure_keeps_archive(tmp_path, monkeypatch):
    """저장 절차가 실패하면(예: 헤더가 맞지 않는 옛 파일) 기존 원천은 그대로 두고 신규 수신분은 격리한다."""
    archive = tmp_path / "NQ_1m.csv"
    archive.write_text("timestamp,open,high,low,close,volume\n2020-01-01 00:00:00,1,1,1,1,1\n2019-01-01 00:00:00,1,1,1,1,1\n")   # 정렬 안 된 옛 파일
    new = pd.DataFrame({"timestamp": ["2024-01-01 00:00:00"], "open": [1.0], "high": [1.0],
                        "low": [1.0], "close": [2.0], "volume": [1.0]})
    monkeypatch.setattr(ofc, "fetch_yahoo_chart_data", lambda *a, **k: new)
    monkeypatch.setattr(ofc, "raw_target", lambda kind, sym, iv: str(archive))
    before = archive.read_text()
    assert ofc.download_and_save_futures("NQ", "1m", "7d", local_log=lambda *_: None) == ""
    assert archive.read_text() == before
    assert os.listdir(tmp_path / ".merge_quarantine" / "rejected_merges")


def test_futures_latest_bar_is_updated_by_the_next_fetch(tmp_path, monkeypatch):
    """마지막 날(형성 중일 수 있는 봉)은 새로 받은 값으로 갱신된다 — 봉인된 과거 날짜와 달리."""
    archive = tmp_path / "NQ_1m.csv"
    archive.write_text("timestamp,open,high,low,close,volume\n2024-01-01 00:00:00,1,1,1,1,1\n")
    new = pd.DataFrame({"timestamp": ["2024-01-01 00:00:00"], "open": [1.0], "high": [1.0],
                        "low": [1.0], "close": [9.0], "volume": [1.0]})
    monkeypatch.setattr(ofc, "fetch_yahoo_chart_data", lambda *a, **k: new)
    monkeypatch.setattr(ofc, "raw_target", lambda kind, sym, iv: str(archive))
    ofc.download_and_save_futures("NQ", "1m", "7d", local_log=lambda *_: None)
    assert pd.read_csv(archive)["close"].tolist() == [9.0]


# ---- 12. 락 / tmp 정리 -------------------------------------------------------
def test_file_lock_serializes_and_reentrant(tmp_path):
    from shared.core.file_lock import file_locked
    p = str(tmp_path / "x.json")
    counter = {"v": 0, "max": 0, "cur": 0}

    def work():
        for _ in range(20):
            with file_locked(p):
                with file_locked(p):     # 재진입
                    counter["cur"] += 1
                    counter["max"] = max(counter["max"], counter["cur"])
                    counter["v"] += 1
                    counter["cur"] -= 1
    ts = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert counter["v"] == 80 and counter["max"] == 1


def test_relations_save_cleans_tmp_on_failure(tmp_path, monkeypatch):
    from shared.data_library import symbol_relations_manager as srm
    monkeypatch.setattr(srm, "_RELATIONS_FILE_PATH", str(tmp_path / "r.json"))
    assert srm.SymbolRelationsManager.save_all_relations({"a": 1})
    assert not srm.SymbolRelationsManager.save_all_relations({"a": object()})
    assert [f.name for f in tmp_path.iterdir()] == ["r.json"]


# ---- 9. 배포 디렉터리 교체 ---------------------------------------------------
def test_replace_model_dir_removes_stale(tmp_path):
    from CAiModelBaker.web_dashboard.main import _replace_model_dir_atomic
    src = tmp_path / "src"
    dst = tmp_path / "shared" / "model"
    src.mkdir()
    dst.mkdir(parents=True)
    (src / "model_15m_primary.txt").write_text("new")
    (src / "feature_spec.json").write_text("{}")
    (dst / "model_15m_meta.txt").write_text("stale")
    (dst / "best_params_old.json").write_text("stale")
    (dst / "notes.md").write_text("keep")
    n = _replace_model_dir_atomic(str(src), str(dst))
    assert n == 2
    assert sorted(f.name for f in dst.iterdir()) == ["feature_spec.json", "model_15m_primary.txt", "notes.md"]
    assert not [f for f in (tmp_path / "shared").iterdir() if f.name.startswith(".model_")]
