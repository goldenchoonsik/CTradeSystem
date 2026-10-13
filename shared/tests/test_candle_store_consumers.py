# -*- coding: utf-8 -*-
"""1분봉 일자 저장소(통합 CSV 없음)에서 경로 기반 소비자(헬스·품질 감사·자산 삭제/초기화·백업·무결성 도구)가 그대로 동작하는가. 모두 tmp_path."""
import importlib.util
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from shared.data_library import candle_store as cs

HDR = "timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume,upbit_trade_price,cvd"
LATER = datetime.now(timezone(timedelta(hours=9))) + timedelta(days=2)      # 픽스처 날짜가 모두 '완결'인 수집 시각
SYM = "000003"


def _bars(day, n=20, vol=10, close0=0):
    t = datetime.strptime(f"{day} 09:01:00", "%Y-%m-%d %H:%M:%S")
    return [f"{(t + timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S')},1,2,1,{close0 + i},{vol},0,0" for i in range(n)]


@pytest.fixture
def day_store(tmp_path, monkeypatch):
    from CAiModelBaker.core import candle_day_store as cds
    root = tmp_path / "candle_1m"
    root.mkdir()
    monkeypatch.setattr(cs, "candle_root", lambda: str(root))
    today = date.today()
    wd = sorted([today - timedelta(days=i) for i in range(1, 15) if (today - timedelta(days=i)).weekday() < 5][:5])
    lines = []
    for d in wd[:2] + wd[3:]:                                    # wd[2] 는 영구 결손
        lines += _bars(str(d))

    def ingest(ls, name="in.csv", sym=SYM):
        inc = tmp_path / name
        inc.write_text(HDR + "\n" + "\n".join(reversed(ls)) + "\n", encoding="utf-8")
        return cds.ingest(str(inc), sym, candle_root=str(root), quarantine_dir=str(tmp_path / "q"), collected_at=LATER, log=lambda m: None)

    ingest(lines)
    cds.write_meta(str(root), SYM, str(wd[2]), {"date": str(wd[2]), "status": "LOST"})
    return root, wd, cds, ingest


def test_path_exists_covers_day_store_and_combined_csv(day_store, tmp_path):
    root, wd, cds, ingest = day_store
    logical = str(root / f"A{SYM}_1m.csv")
    assert not os.path.exists(logical) and cs.is_day_layout(SYM)
    assert cs.path_exists(logical) and not cs.path_exists(str(root / "A000009_1m.csv"))
    other = tmp_path / "plain.csv"                                 # 논리 경로가 아닌 실제 파일은 종전대로
    other.write_text("a\n1\n")
    assert cs.path_exists(str(other)) and not cs.path_exists(str(tmp_path / "none.csv"))
    comb = root / "A000008_1m.csv"                                 # 아직 통합 CSV 인 종목도 놓치지 않는다
    comb.write_text(HDR + "\n" + "\n".join(_bars("2026-09-29")) + "\n")
    assert cs.path_exists(str(comb)) and not cs.is_day_layout("000008")


def test_health_and_quality_inputs_on_day_layout(day_store):
    root, wd, cds, ingest = day_store
    path = str(root / f"A{SYM}_1m.csv")
    from shared.data_library.raw_store_health import RawStoreHealthEngine, _stamp
    h = RawStoreHealthEngine.compute_asset_health(path, "KRX_STOCK", days=20)
    st = {s["date"]: s["status"] for s in h["slots"]}
    assert st[str(wd[0])] == "found" and st[str(wd[2])] in ("lost", "holiday") and h["missing_count"] == 0
    stamp = _stamp(path)
    assert isinstance(stamp, str) and stamp
    from shared.data_library import data_quality as dq
    sig = dq._sig(path)
    assert sig is not None and sig == [cs.signature(SYM)]
    df = dq._load_series(path)
    assert len(df) == 80 and {"ts", "close", "volume"} <= set(df.columns)
    ingest(_bars(str(wd[-1]), 21, close0=0), name="in2.csv")        # 새 봉 → 지문이 바뀐다(캐시 무효화)
    assert dq._sig(path) != sig and _stamp(path) != stamp


def test_ledger_check_uses_day_meta_and_pending_conflicts(day_store):
    root, wd, cds, ingest = day_store
    path = str(root / f"A{SYM}_1m.csv")
    from shared.data_library import data_quality as dq
    days = {}
    dq._apply_ledger_check(days, path)
    assert days == {}                                               # 건강한 저장소 — 지적 없음
    p = cds.day_csv(str(root), SYM, str(wd[1]))                     # 엔진 밖 수정 → 메타 불일치로 불량
    os.chmod(p, 0o644)
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{wd[1]} 15:31:00,1,1,1,1,1,0,0\n")
    days = {}
    dq._apply_ledger_check(days, path)
    assert "ledger_digest" in days[str(wd[1])]["checks"]
    # 확인 필요(같은 시각 다른 내용) — 일자별 .csv.conflicts.json
    import json
    cp = cds.day_csv(str(root), SYM, str(wd[0])) + ".conflicts.json"
    with open(cp, "w", encoding="utf-8") as f:
        json.dump({"items": [{"day": str(wd[0]), "status": "pending", "ts": f"{wd[0]} 09:01:00", "old": ["a"], "new": ["b"]}]}, f)
    assert len(cs.pending_conflicts(SYM)) == 1
    days = {}
    dq._apply_ledger_check(days, path)
    assert days[str(wd[0])]["checks"]["pending_conflicts"]["value"] == 1.0
    from CAiModelBaker.core import raw_day_store as rds              # 대시보드 동기화 요약/무결성 도구의 raw 스캐너도 같은 파일명으로 찾는다
    assert len(rds.pending_conflicts(cds.day_csv(str(root), SYM, str(wd[0])))) == 1


def test_remove_candle_day_store_moves_or_deletes_including_sealed(day_store, tmp_path):
    root, wd, cds, ingest = day_store
    cds.seal_closed_days(str(root), SYM, now=LATER, log=lambda m: None)
    assert any(os.stat(cds.day_csv(str(root), SYM, str(d))).st_mode & 0o222 == 0 for d in wd[:2])   # 봉인(0o444) 포함
    from shared.data_library.source_mapping_manager import SourceMappingManager as S
    deleted = []
    freed = S._remove_candle_day_store(SYM, deleted, backup_to=str(tmp_path / "bk"), backup_name_suffix="_1m")
    assert freed > 0 and deleted and not os.path.isdir(cs.day_dir(SYM))
    assert (tmp_path / "bk" / f"A{SYM}_1m").is_dir()                 # 틱 폴더(A000003)와 이름이 겹치지 않는다
    ingest(_bars(str(wd[0])), name="in3.csv")
    os.chmod(cds.day_csv(str(root), SYM, str(wd[0])), 0o444)
    deleted = []
    assert S._remove_candle_day_store(SYM, deleted) > 0 and not os.path.isdir(cs.day_dir(SYM))        # 영구 삭제도 봉인 파일 포함
    assert S._remove_candle_day_store(SYM, []) == 0                  # 없으면 아무 일도 없다


def test_remove_candle_day_store_futures_key(day_store, tmp_path):
    root, wd, cds, ingest = day_store
    ingest(_bars(str(wd[0])), name="f.csv", sym="10100")
    assert os.path.isdir(root / "A10100")
    from shared.data_library.source_mapping_manager import SourceMappingManager as S
    deleted = []
    assert S._remove_candle_day_store("10100", deleted) > 0 and not os.path.isdir(root / "A10100")


def test_backup_seals_candle_day_files_like_tick():
    spec = importlib.util.spec_from_file_location("backup_raw", os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "backup_raw.py"))
    br = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(br)
    for rel in ("creon/candle_1m/A005930/2026-10-01.csv", "creon/candle_1m/AU180/2026-10-01.meta.json",
                "creon/tick/A005930/2026-10-01.csv", "creon/candle_1m/A10100/2026-10-01.csv"):
        assert br.is_sealed(rel), rel
    for rel in ("creon/candle_1m/A005930_1m.csv", "creon/candle_1m/A005930/2026-10-01.csv.conflicts.json",
                "creon/candle_1d/A005930/2026-10-01.csv", "creon/candle_1m/A005930/x/2026-10-01.csv"):
        assert not br.is_sealed(rel), rel


def test_raw_store_integrity_skips_creon_day_folders(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("raw_store_integrity", os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "raw_store_integrity.py"))
    rsi = importlib.util.module_from_spec(spec)
    for name in [n for n in sys.modules if n == "core" or n.startswith("core.")]:      # CTrade 의 `core` 가 먼저 로드돼 있어도(전체 실행) 스크립트의 `from core import ...` 가 CAiModelBaker/core 를 보게 한다
        monkeypatch.delitem(sys.modules, name)
    spec.loader.exec_module(rsi)
    raw = tmp_path / "DataVault" / "raw"
    for rel in ("creon/candle_1m/A1/2026-10-01.csv", "creon/candle_1m/A1_1m.csv", "ctrader/EURUSD_1m.csv"):
        (raw / rel).parent.mkdir(parents=True, exist_ok=True)
        (raw / rel).write_text(HDR + "\n")
    monkeypatch.setattr(rsi, "VAULT", str(tmp_path / "DataVault"))
    monkeypatch.setattr(cs, "candle_root", lambda: str(raw / "creon" / "candle_1m"))
    assert [r[0] for r in rsi.other_raw_files()] == [os.path.join("ctrader", "EURUSD_1m.csv")]
    import json
    (raw / "creon/candle_1m/A1/2026-10-01.csv.conflicts.json").write_text(json.dumps(
        {"items": [{"day": "2026-10-01", "status": "pending", "ts": "t", "detected_at": "d", "old": ["o"], "new": ["n"]}]}))
    import argparse
    rsi.conflicts_cmd(argparse.Namespace(cmd="resolve", symbols=None, label="1M", day="2026-10-01", use="new"))
    out = capsys.readouterr().out
    assert "1분봉 일자 파일" in out and "해결하지 않습니다" in out
    rsi.conflicts_cmd(argparse.Namespace(cmd="conflicts", symbols=None, label="1M", day=None, use=None))
    assert "확인 필요 합계 1건" in capsys.readouterr().out


def test_find_raw_file_and_manifest_sees_day_store(day_store, monkeypatch):
    root, wd, cds, ingest = day_store
    from shared.data_library import source_mapping_manager as smm
    real = smm.data_vault_path
    monkeypatch.setattr(smm, "data_vault_path",
                        lambda *p: os.path.join(str(root), p[-1]) if p[:3] == ("raw", "creon", "candle_1m") else real(*p))
    stat = smm.SourceMappingManager.find_raw_file_and_manifest(SYM, "CREON", "KRX_STOCK")
    assert stat["exists"] and stat["file_path"].endswith(f"A{SYM}_1m.csv") and not os.path.exists(stat["file_path"])
    assert stat["rows"] == 80 and stat["trading_days"] == 4 and stat["file_size"] > 0
    assert stat["last_timestamp"] == cs.last_timestamp(SYM)
