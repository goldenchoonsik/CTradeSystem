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
    assert ran == ["creon_daily_unadj", "creon_daily_ext"]       # 무수정 실패 → 수정·패널·골드 건너뜀, 보조지표는 독립이라 실행
    assert st["kr"]["status"] == "failed" and "건너뜀" in st["kr"]["detail"]
    assert st["kr_ext"]["status"] == "done"

    ran.clear()
    fail.clear()
    uu.finish()
    uu.begin(["kr", "kr_ext"])
    uu.run_kr(["kr", "kr_ext"])
    assert ran == ["creon_daily_unadj", "creon_daily", "creon_daily_ext", "panel", "gold"]   # 보조지표는 패널 전에
    assert uu.load()["items"]["kr"]["status"] == "done"
