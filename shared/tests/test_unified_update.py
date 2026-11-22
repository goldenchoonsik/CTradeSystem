"""통합 업데이트 실행기 — 순서·의존 건너뛰기·예상 시간. 임시 DataVault 로만(실제 수집·실물 검사는 돌리지 않는다)."""
import pytest

from shared.data_library import paths
from shared.data_library import unified_update as uu


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_VAULT_ROOT", str(tmp_path))
    return tmp_path


def test_total_estimate_runs_korea_then_us_serially():
    est = {"ops": 15, "us": 25, "kr": 70, "kr_ext": 17, "checks": 5}
    assert uu.total_estimate(uu.KEYS, est) == 15 + 70 + 17 + 25 + 5
    assert uu.total_estimate(["us", "checks"], est) == 25 + 5


def test_estimates_learn_from_last_successful_run(vault):
    uu.begin(["checks"])
    uu.set_item("checks", "running")
    uu.set_item("checks", "done", "ok")
    uu.finish()
    assert uu.estimates()["checks"] == 0.5                       # 실측(최소 0.5분)이 기본값 5분을 대신한다
    assert uu.estimates()["kr"] == 70                            # 안 돌린 항목은 기본값


def test_begin_refuses_second_run_and_empty_selection(vault):
    with pytest.raises(ValueError):
        uu.begin([])
    uu.begin(["us"])
    with pytest.raises(RuntimeError):
        uu.begin(["us"])
    uu.mark_interrupted_if_stale(alive=False)                    # 대시보드 재시작으로 실행기가 사라진 경우
    st = uu.load()
    assert st["status"] == "interrupted" and st["items"]["us"]["status"] == "skipped"


def test_korea_steps_order_and_skip_after_failure(vault, monkeypatch):
    from shared.data_library.kr_equity import runner
    ran = []
    fail = {"creon_daily_unadj"}
    monkeypatch.setattr(runner, "run_sync", lambda step, full=False: ran.append(step) or (1 if step in fail else 0))
    uu.begin(["kr", "kr_ext"])
    uu.run_kr(["kr", "kr_ext"])
    st = uu.load()["items"]
    assert ran == uu.KR_PREP_STEPS + ["creon_daily_unadj", "creon_daily_ext"]   # 무수정 실패 → 수정·패널·골드 건너뜀, 보조지표는 독립이라 실행
    assert st["kr"]["status"] == "failed" and "건너뜀" in st["kr"]["detail"]
    assert st["kr_ext"]["status"] == "done"

    ran.clear()
    fail.clear()
    uu.finish()
    uu.begin(["kr", "kr_ext"])
    uu.run_kr(["kr", "kr_ext"])
    assert ran == ["creon_universe", "kind", "naver", "krx_marcap",                        # 종목 목록 갱신이 맨 앞
                   "creon_daily_unadj", "creon_daily", "creon_daily_ext", "panel", "gold"]   # 보조지표는 패널 전에
    assert uu.load()["items"]["kr"]["status"] == "done"


def test_korea_universe_refresh_failure_is_warning_not_stop(vault, monkeypatch):
    """종목 목록 갱신(Creon 스냅샷·KIND 등)이 실패해도 이전 목록으로 일봉·패널·골드는 끝까지 — 결과는 '확인 필요'."""
    from shared.data_library.kr_equity import runner
    ran = []
    monkeypatch.setattr(runner, "run_sync", lambda step, full=False: ran.append(step) or (1 if step == "creon_universe" else 0))
    uu.begin(["kr"])
    uu.run_kr(["kr"])
    it = uu.load()["items"]["kr"]
    assert ran == uu.KR_PREP_STEPS + uu.KR_STEPS + ["panel", "gold"]
    assert it["status"] == "warn" and "Creon 상장 스냅샷" in it["detail"] and "이전 목록" in it["detail"]


def test_universe_change_counts_new_listings_and_delistings():
    before = {"A000001": "listed", "A000002": "listed", "A000003": "delisted", "A000009": "listed"}
    after = {"A000001": "listed", "A000002": "delisted", "A000003": "delisted", "A000004": "listed"}
    assert uu.universe_change(before, after) == "종목 목록 상장 2·상폐 2 (신규 상장 1·상폐 전환 1·목록에서 사라짐 1)"
    assert uu.universe_change(after, after) == "종목 목록 상장 2·상폐 2 (신규 상장 0·상폐 전환 0)"


def test_item_start_time_kept_across_step_updates(vault):
    """단계마다 running 을 다시 불러도 시작 시각은 처음 것 — 소요 시간 실측(예상 시간)이 마지막 단계만 재지 않게."""
    uu.begin(["kr"])
    uu.set_item("kr", "running", "1/3")
    first = uu.load()["items"]["kr"]["started_at"]
    st = uu.load()
    st["items"]["kr"]["started_at"] = "2000-01-01T00:00:00"     # 이미 오래 돌고 있던 것처럼
    uu._save(st)
    uu.set_item("kr", "running", "2/3")
    assert uu.load()["items"]["kr"]["started_at"] == "2000-01-01T00:00:00" != first


def test_overview_includes_live_logs_and_orphan_detection(vault, monkeypatch, tmp_path):
    from shared.data_library.kr_equity import runner as kr_runner
    from shared.data_library.us_equity import collect as us_collect

    log_file = tmp_path / "panel_test.out"
    log_file.write_text("line1\n[panel] prices 2000/3284\n[panel] quality written\n", encoding="utf-8")

    monkeypatch.setattr(kr_runner, "running", lambda: {"step": "panel", "pid": 12345, "started_at": "2026-11-14T15:33:14", "log": str(log_file)})
    monkeypatch.setattr(us_collect, "job_status", lambda: {"running": None, "last": None})

    # 통합 업데이트는 중단되었지만 하위 kr 프로세스는 살아있는 경우 -> orphan 감지
    ov = uu.overview()
    assert ov["live"]["orphan"]["step"] == "panel"
    assert ov["live"]["orphan"]["pid"] == 12345
    assert ov["live"]["lines"] == ["line1", "[panel] prices 2000/3284", "[panel] quality written"]
    assert ov["live"]["updated_at"] != ""

    # 통합 업데이트 running 중일 때는 orphan이 아니라 해당 항목의 실시간 로그로 반환
    uu.begin(["kr"])
    uu.set_item("kr", "running", "분석 패널 진행 중 (3/4)")
    ov2 = uu.overview()
    assert ov2["live"]["key"] == "kr"
    assert ov2["live"]["orphan"] is None
    assert "[panel] quality written" in ov2["live"]["lines"]


def test_items_split_into_model_and_research_groups():
    """LightGBM 운용 데이터(자주)와 연구용 전 종목(가끔)을 화면에서 나눈다 — 모든 항목은 정의된 묶음 하나에 속한다."""
    groups = {g["key"] for g in uu.GROUPS}
    assert {i["key"]: i["group"] for i in uu.ITEMS} == {"ops": "model", "us": "research", "kr": "research", "kr_ext": "research", "checks": "check"}
    assert all(i["group"] in groups for i in uu.ITEMS)
    ops = next(i for i in uu.ITEMS if i["key"] == "ops")
    assert ops["danger_days"] < 20 <= ops["danger_days"] + 7      # 틱 서버 보관 20거래일보다 먼저 빨간불


def test_freshness_levels():
    from datetime import datetime
    now = datetime(2026, 11, 15, 12, 0)
    ops = next(i for i in uu.ITEMS if i["key"] == "ops")
    us = next(i for i in uu.ITEMS if i["key"] == "us")
    checks = next(i for i in uu.ITEMS if i["key"] == "checks")
    assert uu.freshness(ops, "2026-11-14T12:00:00", now) == {"last_ok": "2026-11-14T12:00:00", "days": 1.0, "level": "ok"}
    assert uu.freshness(ops, "2026-11-07T12:00:00", now)["level"] == "warn"
    assert uu.freshness(ops, "2026-10-31T12:00:00", now)["level"] == "danger"
    assert uu.freshness(us, "2026-10-01T12:00:00", now)["level"] == "warn"     # 연구 데이터는 늦어도 손실 없음 → 노랑까지만
    assert uu.freshness(ops, None, now)["level"] == "never"
    assert uu.freshness(checks, "2026-11-01T00:00:00", now)["level"] == "none"


def test_last_success_from_history_and_records(vault):
    st = {"history": [
        {"started_at": "2026-11-01T10:00:00", "finished_at": "2026-11-01T12:00:00", "selected": ["ops", "kr"], "failed": ["kr"]},
        {"started_at": "2026-11-05T10:00:00", "finished_at": "2026-11-05T12:00:00", "selected": ["kr"], "failed": [], "status": "interrupted"},
    ], "last_ok": {"us": "2026-11-03T09:00:00"}}
    assert uu.last_success(st) == {"ops": "2026-11-01T12:00:00", "us": "2026-11-03T09:00:00"}   # 실패·중단은 성공 아님

    uu.begin(["checks"])
    uu.set_item("checks", "running")
    uu.set_item("checks", "warn", "실패 1")                     # 검사가 무언가 잡아도 '돌았다'는 성공
    uu.finish()
    ov = uu.overview()
    assert ov["groups"] == uu.GROUPS
    assert next(i for i in ov["items"] if i["key"] == "checks")["fresh"]["level"] == "none"
    assert "checks" in uu.load()["last_ok"] and uu.load()["history"][-1]["status"] == "finished"
