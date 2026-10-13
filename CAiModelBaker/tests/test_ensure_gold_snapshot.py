# -*- coding: utf-8 -*-
"""ASRL 시작 전 Gold 스냅샷 준비 보장(ensure_gold_snapshot) 검증"""
from CAiModelBaker.core import autolab_bridge as ab


def test_existing_snapshot_is_not_rebuilt(tmp_path, monkeypatch):
    snap = tmp_path / "data_snapshot_10s.csv"
    snap.write_text("timestamp\n", encoding="utf-8")
    monkeypatch.setattr(ab, "build_gold_snapshot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rebuilt")))
    res = ab.ensure_gold_snapshot("069500", "10s", str(snap))
    assert res == {"ready": True, "built": False, "message": res["message"]}


def test_missing_snapshot_is_built_automatically(tmp_path, monkeypatch):
    snap = tmp_path / "data_snapshot_10s.csv"
    calls = []

    def fake_build(sym, itv, local_log=None):
        calls.append((sym, itv))
        snap.write_text("timestamp\n", encoding="utf-8")
        return {}

    monkeypatch.setattr(ab, "build_gold_snapshot", fake_build)
    res = ab.ensure_gold_snapshot("A069500", "10s", str(snap))
    assert res["ready"] and res["built"] and calls == [("069500", "10s")]


def test_build_failure_points_to_the_missing_scale(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "build_gold_snapshot", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no parquet")))
    monkeypatch.setattr(ab.SourceMappingManager, "load_all_mappings",
                        classmethod(lambda cls, *a, **k: {"396500": {"target_scales": ["1m"]}}))
    res = ab.ensure_gold_snapshot("396500", "10s", str(tmp_path / "missing.csv"))
    assert not res["ready"]
    assert "스케일" in res["message"] and "10s" in res["message"] and "no parquet" in res["message"]


def test_build_failure_with_declared_scale_points_to_normalized_build(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "build_gold_snapshot", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no parquet")))
    monkeypatch.setattr(ab.SourceMappingManager, "load_all_mappings",
                        classmethod(lambda cls, *a, **k: {"396500": {"target_scales": ["10s", "1m"]}}))
    res = ab.ensure_gold_snapshot("396500", "10s", str(tmp_path / "missing.csv"))
    assert not res["ready"] and "정규화 빌드" in res["message"]


def _bridge_meta(snap, **overrides):
    import json
    meta = {"source": "datavault_features_store", "ref_symbols": ["000660"], "series_symbols": ["10100"]}
    meta.update(overrides)
    (snap.parent / "data_snapshot_10s.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _plan(monkeypatch, refs, series):
    monkeypatch.setattr(ab.SymbolRelationsManager, "join_plan",
                        classmethod(lambda cls, t: {"refs": list(refs), "series": list(series)}))


def _fake_build(snap, calls):
    def build(sym, itv, local_log=None):
        calls.append(sym)
        snap.write_text("timestamp\n", encoding="utf-8")
        return {}
    return build


def test_snapshot_built_for_a_different_join_plan_is_rebuilt(tmp_path, monkeypatch):
    snap = tmp_path / "data_snapshot_10s.csv"
    snap.write_text("timestamp\n", encoding="utf-8")
    _bridge_meta(snap)                                   # 예전에는 대장주 하나 + 선물 하나만 결합
    _plan(monkeypatch, ["000660", "005930"], ["10100"])  # 지금 계획은 대장주가 둘
    calls = []
    monkeypatch.setattr(ab, "build_gold_snapshot", _fake_build(snap, calls))
    res = ab.ensure_gold_snapshot("069500", "10s", str(snap))
    assert res["ready"] and res["built"] and calls == ["069500"]


def test_snapshot_from_the_old_bridge_schema_is_rebuilt(tmp_path, monkeypatch):
    snap = tmp_path / "data_snapshot_10s.csv"
    snap.write_text("timestamp\n", encoding="utf-8")
    import json
    (tmp_path / "data_snapshot_10s.meta.json").write_text(
        json.dumps({"source": "datavault_features_store", "ref_symbols": ["000660"], "macro_symbols": ["nq_futures"]}), encoding="utf-8")
    _plan(monkeypatch, ["000660"], [])
    calls = []
    monkeypatch.setattr(ab, "build_gold_snapshot", _fake_build(snap, calls))
    assert ab.ensure_gold_snapshot("069500", "10s", str(snap))["built"] and calls == ["069500"]


def test_matching_snapshot_and_foreign_snapshots_are_left_alone(tmp_path, monkeypatch):
    snap = tmp_path / "data_snapshot_10s.csv"
    snap.write_text("timestamp\n", encoding="utf-8")
    monkeypatch.setattr(ab, "build_gold_snapshot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rebuilt")))
    _bridge_meta(snap)
    _plan(monkeypatch, ["000660"], ["10100"])
    assert not ab.ensure_gold_snapshot("069500", "10s", str(snap))["built"]      # 계획과 일치
    import json
    (tmp_path / "data_snapshot_10s.meta.json").write_text(json.dumps({"rows": 1}), encoding="utf-8")
    assert not ab.ensure_gold_snapshot("069500", "10s", str(snap))["built"]      # 브리지가 만든 게 아니면 판단하지 않는다


def test_force_rebuild_triggers_rebuild_even_if_exists(tmp_path, monkeypatch):
    """force_rebuild=True 인 경우 스냅샷이 이미 존재하더라도 최신화를 위해 강제 재빌드되어야 한다."""
    snap = tmp_path / "data_snapshot_10s.csv"
    snap.write_text("timestamp\n", encoding="utf-8")
    _bridge_meta(snap)
    _plan(monkeypatch, ["000660"], ["10100"])

    calls = []
    monkeypatch.setattr(ab, "build_gold_snapshot", _fake_build(snap, calls))
    res = ab.ensure_gold_snapshot("069500", "10s", str(snap), force_rebuild=True)
    assert res["ready"] and res["built"] and calls == ["069500"]
