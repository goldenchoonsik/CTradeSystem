"""
프로젝트 격리 구조로 기존 프로젝트 이전 (1회성)
================================================

이전 규칙
---------
- 프로젝트 루트에 흩어진 지난 자동연구 산출물(leaderboard.json, trials/, candidates/, backtest_run.log,
  reports/train_run.log) → ``runs/<평가시각>_legacy/`` 로 이동.
- model/:
  · 지난 연구가 PASSED → 챔피언 후보(candidates/rank_N)로 model/ 을 다시 채우고 출처(model_source.json) 기록.
  · 지난 연구가 REJECTED → model/ 의 모델(탈락 실행이 남긴 마지막 재학습 후보)을 legacy run 의
    ``work/model`` 로 옮기고 model/ 은 비운다.
  · 자동연구 기록이 없으면 그대로 둔다 (사용자가 직접 학습한 모델로 간주).
- 프로젝트 정보(메모·생성일) → config_snapshot.json 의 PROJECT_META.
- 열린 프로젝트 포인터(active_project.json) ← 기존 active.json 의 종목/인터벌/세션.

사용법
------
    .venv/bin/python CAiModelBaker/scripts/migrate_project_isolation.py           # 미리보기
    .venv/bin/python CAiModelBaker/scripts/migrate_project_isolation.py --apply   # 백업 후 실행
"""

import argparse
import json
import os
import shutil
import sys
import tarfile
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_BAKER = os.path.dirname(_HERE)
_REPO = os.path.dirname(_BAKER)
for p in (_REPO, _BAKER):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.config import project_store  # noqa: E402
from shared.config import config_manager as cm  # noqa: E402

LEGACY_ROOT_ITEMS = ("leaderboard.json", "trials", "candidates", "backtest_run.log")
LEGACY_REPORT_ITEMS = ("train_run.log",)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _legacy_stamp(pdir, lb):
    ts = (lb or {}).get("evaluated_at")
    if ts:
        try:
            return datetime.fromisoformat(ts).strftime("%Y%m%d_%H%M%S")
        except Exception:
            pass
    mtimes = [os.path.getmtime(os.path.join(pdir, n)) for n in LEGACY_ROOT_ITEMS if os.path.exists(os.path.join(pdir, n))]
    return datetime.fromtimestamp(max(mtimes) if mtimes else os.path.getmtime(pdir)).strftime("%Y%m%d_%H%M%S")


def _same_file(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except Exception:
        return False


def plan_project(ref):
    pdir = project_store.project_dir(ref)
    interval = project_store.interval_of_market_dir(ref.split("/")[0])
    lb = _read_json(os.path.join(pdir, "leaderboard.json"))
    actions = []
    has_legacy = any(os.path.exists(os.path.join(pdir, n)) for n in LEGACY_ROOT_ITEMS) or \
        os.path.exists(os.path.join(pdir, "reports", "train_run.log"))
    run_name = f"{_legacy_stamp(pdir, lb)}_legacy" if has_legacy else None

    if has_legacy:
        for n in LEGACY_ROOT_ITEMS:
            if os.path.exists(os.path.join(pdir, n)):
                actions.append(("move", n, f"runs/{run_name}/{n}"))
        if os.path.exists(os.path.join(pdir, "reports", "train_run.log")):
            # 종전 train_run.log 는 공용 로그 끝부분 복사본이라 이 프로젝트 로그가 아닐 수 있다
            actions.append(("move", "reports/train_run.log", f"runs/{run_name}/train_run.log.unreliable_copy"))

    model_dir = os.path.join(pdir, "model")
    model_file = os.path.join(model_dir, f"model_{interval}.txt")
    status = (lb or {}).get("status")
    champ = (lb or {}).get("champion_rank")
    if lb and status == "PASSED" and champ:
        cand = os.path.join(pdir, "candidates", f"rank_{champ}")
        match = _same_file(os.path.join(cand, f"model_{interval}.txt"), model_file)
        actions.append(("champion", champ, "model/ 을 rank_%s 로 다시 채움 (현재 model/ 일치=%s)" % (champ, match)))
    elif lb:
        if os.path.isdir(model_dir) and os.listdir(model_dir):
            actions.append(("evict_model", status, f"model/ → runs/{run_name}/work/model (탈락 실행이 남긴 후보)"))
    else:
        actions.append(("keep_model", None, "자동연구 기록 없음 — model/ 유지 (직접 학습 모델로 간주)"))

    if (lb and status != "PASSED") and os.path.exists(os.path.join(pdir, "reports", "test_trades.csv")):
        actions.append(("move", "reports/test_trades.csv", f"runs/{run_name}/test_trades.csv"))

    return {"ref": ref, "dir": pdir, "leaderboard_status": status, "champion_rank": champ,
            "run_name": run_name, "actions": actions}


def _meta_from_model(pdir):
    meta = {}
    model_dir = os.path.join(pdir, "model")
    if os.path.isdir(model_dir):
        for f in sorted(os.listdir(model_dir)):
            if f.startswith("best_params_") and f.endswith(".json"):
                bp = _read_json(os.path.join(model_dir, f)) or {}
                if bp.get("memo"):
                    meta["memo"] = bp["memo"]
                if bp.get("created_at"):
                    meta["created_at"] = bp["created_at"]
                break
    return meta


def apply_project(plan):
    ref, pdir, run_name = plan["ref"], plan["dir"], plan["run_name"]
    interval = project_store.interval_of_market_dir(ref.split("/")[0])

    # 프로젝트 정보는 model/ 을 건드리기 전에 옮겨 둔다
    meta = _meta_from_model(pdir)
    existing = project_store.read_meta(ref) if os.path.exists(project_store.config_path(ref)) else {}
    meta.update(existing)
    meta.setdefault("migrated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if os.path.exists(project_store.config_path(ref)):
        project_store.update_meta(ref, **meta)

    run_dir = os.path.join(pdir, project_store.RUNS_DIRNAME, run_name) if run_name else None
    if run_dir:
        os.makedirs(os.path.join(run_dir, project_store.RUN_WORK_DIRNAME), exist_ok=True)

    for kind, a, b in plan["actions"]:
        if kind == "move":
            src, dst = os.path.join(pdir, a), os.path.join(pdir, b)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
        elif kind == "champion":
            champ = a
            cand = os.path.join(run_dir, "candidates", f"rank_{champ}")
            model_dir = os.path.join(pdir, "model")
            old_model = os.path.join(run_dir, project_store.RUN_WORK_DIRNAME, "model_before_migration")
            if os.path.isdir(model_dir):
                shutil.move(model_dir, old_model)
            os.makedirs(model_dir)
            for f in (f"model_{interval}.txt", f"best_params_{interval}.json", "feature_spec.json", "candidate_detail.json"):
                if os.path.exists(os.path.join(cand, f)):
                    shutil.copy2(os.path.join(cand, f), os.path.join(model_dir, f))
            with open(os.path.join(model_dir, "model_source.json"), "w", encoding="utf-8") as f:
                json.dump({"kind": "autolab_champion", "status": "PASSED", "rank": champ,
                           "run": os.path.relpath(run_dir, pdir), "migrated": True,
                           "promoted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f, indent=2, ensure_ascii=False)
        elif kind == "evict_model":
            model_dir = os.path.join(pdir, "model")
            dst = os.path.join(run_dir, project_store.RUN_WORK_DIRNAME, "model")
            shutil.move(model_dir, dst)
            os.makedirs(model_dir)

    if run_dir:
        lb = _read_json(os.path.join(run_dir, "leaderboard.json")) or {}
        with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump({"status": lb.get("status", "UNKNOWN"), "champion_rank": lb.get("champion_rank"),
                       "finished_at": lb.get("evaluated_at"), "legacy": True,
                       "model_replaced": bool(lb.get("champion_rank")),
                       "note": "프로젝트 격리 이전(migrate_project_isolation) 전 구조에서 옮긴 실행 기록"},
                      f, indent=2, ensure_ascii=False)


def resolve_pointer():
    active = _read_json(cm.get_active_path("CAiModelBaker")) or {}
    common = cm.load_common_config()
    sid = (active.get("ACTIVE_SESSION_ID") or "").strip()
    symbol = active.get("SYMBOL_UPBIT") or common.get("SYMBOL_UPBIT")
    interval = active.get("INTERVAL") or common.get("INTERVAL")
    if not sid:
        return None, "active.json 에 ACTIVE_SESSION_ID 없음"
    ref = project_store.resolve(sid, symbol=symbol, interval=interval)
    if ref:
        return ref, f"{symbol}/{interval}/{sid}"
    try:
        ref = project_store.resolve(sid)
    except project_store.AmbiguousProjectError as e:
        return None, str(e)
    return ref, f"이름으로 찾음 ({sid})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="백업 후 실제 이전")
    args = ap.parse_args()

    refs = project_store.list_projects()
    plans = [plan_project(r) for r in refs]
    for pl in plans:
        print(f"\n== {pl['ref']}  (지난 연구: {pl['leaderboard_status']}, 챔피언 rank: {pl['champion_rank']})")
        if pl["run_name"]:
            print(f"   legacy run: runs/{pl['run_name']}/")
        for kind, a, b in pl["actions"]:
            print(f"   - {kind:12s} {a if a is not None else ''} → {b}")
    ref, why = resolve_pointer()
    print(f"\n== 열린 프로젝트 포인터: {ref}  ({why})")

    names = {}
    for r in refs:
        names.setdefault(r.split("/", 1)[1], []).append(r)
    dups = {n: rs for n, rs in names.items() if len(rs) > 1}
    if dups:
        print(f"\n⚠️ 이름 중복 프로젝트: {dups} — 이름 변경 후 다시 실행하세요.")
        if args.apply:
            sys.exit(2)

    if not args.apply:
        print("\n(미리보기) 실제 이전은 --apply")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(_BAKER, "logs", "archive")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, f"pre_project_isolation_{stamp}.tar.gz")
    with tarfile.open(backup, "w:gz") as tar:
        tar.add(project_store.dpaths.DEPLOY_ROOT, arcname="deploy")
        tar.add(cm.get_config_dir("CAiModelBaker"), arcname="config")
    print(f"\n💾 백업: {backup}")

    for pl in plans:
        apply_project(pl)
        print(f"✅ {pl['ref']} 이전 완료")
    if ref:
        cm.set_active_project_ref(ref)
        print(f"✅ 열린 프로젝트 포인터 설정: {ref}")


if __name__ == "__main__":
    main()
