# -*- coding: utf-8 -*-
"""외부 백업: 증분·불변 봉인본 충돌 보호·이전 판 보관·검증. 전부 tmp_path — 실제 DataVault 불변."""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location("backup_raw", os.path.join(os.path.dirname(__file__), "..", "scripts", "backup_raw.py"))
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)


def _w(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


@pytest.fixture
def env(tmp_path):
    src, dst = tmp_path / "raw", tmp_path / "usb"
    dst.mkdir()
    _w(str(src / "creon/tick/A1_tick.csv"), "a\n1\n")
    _w(str(src / "creon/tick/A1/2026-10-01.csv"), "a\n1\n")
    _w(str(src / "creon/tick/A1/2026-10-01.meta.json"), "{}")
    _w(str(src / "creon/tick/A1_tick.csv.tmp"), "junk")
    return str(src), str(dst)


def run(env, **kw):
    return br.backup_one(env[1], env[0], kw.get("keep", 3), kw.get("dry", False))


def test_incremental_and_skips_tmp(env):
    r = run(env)
    assert len(r["copied"]) == 3 and not any(x.endswith(".tmp") for x in r["copied"])
    r2 = run(env)
    assert r2["unchanged"] == 3 and not r2["copied"] and not r2["updated"]
    assert br.verify_target.__name__  # 존재 확인


def test_sealed_file_never_overwritten_conflict(env):
    run(env)
    src = os.path.join(env[0], "creon/tick/A1/2026-10-01.csv")
    os.chmod(src, 0o644)
    _w(src, "a\nTAMPERED\n")
    r = run(env)
    assert r["conflicts"] == ["creon/tick/A1/2026-10-01.csv"]
    assert open(os.path.join(env[1], "raw/creon/tick/A1/2026-10-01.csv")).read() == "a\n1\n"


def test_mutable_file_updates_and_keeps_previous_version(env):
    run(env)
    src = os.path.join(env[0], "creon/tick/A1_tick.csv")
    _w(src, "a\n1\n2\n")
    r = run(env)
    assert r["updated"] == ["creon/tick/A1_tick.csv"]
    assert open(os.path.join(env[1], "raw/creon/tick/A1_tick.csv")).read() == "a\n1\n2\n"
    vdir = os.path.join(env[1], "_versions/creon/tick/A1_tick.csv")
    assert len(os.listdir(vdir)) == 1 and open(os.path.join(vdir, os.listdir(vdir)[0])).read() == "a\n1\n"


def test_verify_detects_corruption_and_missing(env):
    run(env)
    p = os.path.join(env[1], "raw/creon/tick/A1_tick.csv")
    open(p, "w").write("XXXX")
    os.remove(os.path.join(env[1], "raw/creon/tick/A1/2026-10-01.meta.json"))
    br.RAW_ROOT = env[0]
    bad = dict(br.verify_target(env[1]))
    assert bad["creon/tick/A1_tick.csv"] == "CORRUPT" and bad["creon/tick/A1/2026-10-01.meta.json"] == "MISSING"


def test_refuses_missing_or_overlapping_target(env, tmp_path):
    with pytest.raises(br.BackupError):
        br.check_target(str(tmp_path / "nope"), env[0])
    with pytest.raises(br.BackupError):
        br.check_target(os.path.join(env[0], "inside"), env[0]) if os.makedirs(os.path.join(env[0], "inside"), exist_ok=True) is None else None


def test_dry_run_writes_nothing(env):
    r = run(env, dry=True)
    assert len(r["copied"]) == 3 and os.listdir(env[1]) == []


# ---------------------------------------------------------------------------
# 웹 API (tmp 설정·tmp raw — 실제 DataVault/설정 불변)
# ---------------------------------------------------------------------------
def test_web_api_add_run_status_remove(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from CAiModelBaker.web_dashboard import main as wb

    src, usb = tmp_path / "raw", tmp_path / "usb"
    usb.mkdir()
    _w(str(src / "creon/tick/A1_tick.csv"), "a\n1\n")
    mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(mod)
    mod.RAW_ROOT, mod.CONFIG = str(src), str(tmp_path / "backup.json")
    monkeypatch.setattr(wb, "_bk_module", lambda: mod)
    c = TestClient(wb.app)

    assert c.post("/api/raw_backup/targets", json={"path": str(tmp_path / "nope")}).json()["status"] == "error"
    assert c.post("/api/raw_backup/targets", json={"path": str(usb)}).json()["status"] == "ok"
    st = c.get("/api/raw_backup/status").json()
    assert st["targets"][0]["pending_files"] == 1 and st["targets"][0]["connected"]

    assert c.post("/api/raw_backup/run", json={"kind": "backup"}).json()["status"] == "ok"
    for _ in range(50):
        j = c.get("/api/raw_backup/status").json()["job"]
        if not j["running"]:
            break
        time.sleep(0.1)
    assert j["result"][0]["copied"] == 1
    st = c.get("/api/raw_backup/status").json()["targets"][0]
    assert st["pending_files"] == 0 and st["backed_files"] == 1

    c.post("/api/raw_backup/run", json={"kind": "verify"})
    for _ in range(50):
        if not c.get("/api/raw_backup/status").json()["job"]["running"]:
            break
        time.sleep(0.1)
    assert c.get("/api/raw_backup/status").json()["targets"][0]["last_verify_bad"] == 0

    c.post("/api/raw_backup/targets/remove", json={"path": str(usb)})
    assert c.get("/api/raw_backup/status").json()["targets"] == []
    assert (usb / "raw/creon/tick/A1_tick.csv").exists()          # 설정에서만 빠지고 백업 파일은 남는다
