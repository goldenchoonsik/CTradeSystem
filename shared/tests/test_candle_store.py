# -*- coding: utf-8 -*-
"""candle_store(읽기 창구): 논리 경로 ↔ 종목, path_has_data/read_csv 대체, 봉인된 날만의 거래량 기준. 읽기만 한다(tmp_path)."""
import json

import pandas as pd
import pytest

from shared.data_library import candle_store as cs

HDR = "timestamp,upbit_open,upbit_high,upbit_low,upbit_close,upbit_volume"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "candle_root", lambda: str(tmp_path))
    for key, days in {"A000001": {"2026-09-28": "VALID", "2026-09-29": "OPEN"}, "A10100": {"2026-09-28": "VALID"}}.items():
        d = tmp_path / key
        d.mkdir()
        for day, status in days.items():
            (d / f"{day}.csv").write_text(f"{HDR}\n{day} 09:01:00,1,1,1,1,5\n{day} 09:02:00,1,1,1,2,6\n", encoding="utf-8")
            (d / f"{day}.meta.json").write_text(json.dumps({"date": day, "status": status, "rows": 2, "volume": 11.0, "sha256": "x",
                                                            "first": f"{day} 09:01:00", "last": f"{day} 09:02:00"}), encoding="utf-8")
    return tmp_path


def test_logical_paths_map_to_symbols(root):
    assert cs.symbol_from_path(str(root / "A000001_1m.csv")) == "000001"
    assert cs.symbol_from_path(str(root / "10100_1m.csv")) == "10100"
    assert cs.symbol_from_path(str(root / "U180_1m.csv")) == "U180"
    assert cs.symbol_from_path(str(root.parent / "A000001_1m.csv")) is None            # 다른 폴더는 창구 대상이 아니다
    assert cs.path_has_data(str(root / "A000001_1m.csv")) and cs.path_has_data(str(root / "10100_1m.csv"))
    assert not cs.path_has_data(str(root / "A999999_1m.csv"))


def test_read_csv_is_a_drop_in_for_logical_and_real_files(root, tmp_path):
    df = cs.read_csv(str(root / "A000001_1m.csv"))
    assert len(df) == 4 and df["timestamp"].is_monotonic_increasing
    assert list(cs.read_csv(str(root / "A000001_1m.csv"), usecols=["timestamp", "upbit_close"]).columns) == ["timestamp", "upbit_close"]
    real = tmp_path.parent / "real.csv"
    real.write_text(f"{HDR}\n2026-09-28 09:01:00,1,1,1,1,5\n", encoding="utf-8")
    assert len(cs.read_csv(str(real))) == 1


def test_volumes_default_to_sealed_days_only(root):
    assert cs.volumes("000001") == {"2026-09-28": 11.0}
    assert cs.volumes("000001", sealed_only=False) == {"2026-09-28": 11.0, "2026-09-29": 11.0}
    assert cs.dates("10100") == {"2026-09-28"} and cs.last_timestamp("000001") == "2026-09-29 09:02:00"


def test_combined_csv_answers_until_a_day_folder_exists_then_the_day_store_wins(tmp_path, monkeypatch):
    """전환기: 통합 CSV 만 있는 종목은 그 파일이, 일자 폴더가 생기면 폴더가 우선한다(같은 이름의 통합 CSV 가 남아 있어도)."""
    monkeypatch.setattr(cs, "candle_root", lambda: str(tmp_path))
    csv = tmp_path / "A000002_1m.csv"
    csv.write_text(f"{HDR}\n2026-09-28 09:01:00,1,1,1,1,5\n", encoding="utf-8")
    assert cs.path_has_data(str(csv)) and len(cs.read_csv(str(csv))) == 1
    d = tmp_path / "A000002"
    d.mkdir()
    (d / "2026-09-29.csv").write_text(f"{HDR}\n2026-09-29 09:01:00,1,1,1,1,7\n2026-09-29 09:02:00,1,1,1,2,8\n", encoding="utf-8")
    (d / "2026-09-29.meta.json").write_text(json.dumps({"date": "2026-09-29", "status": "OPEN"}), encoding="utf-8")
    assert len(cs.read_csv(str(csv))) == 2                                     # 폴더 우선
    csv.unlink()
    assert cs.path_has_data(str(csv))                                          # 파일이 없어도 폴더가 답한다
