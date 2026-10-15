"""kr_equity Linux 단계 실행 — `python -m shared.data_library.kr_equity.cli <step>`  (step 은 sources.STEPS 의 linux 단계)"""
import argparse
import os
import sys

from . import sources


def runner_steps():
    from . import runner
    return runner.THEN_BRONZE


def creon_then_bronze(step: str, full: bool) -> int:
    """Windows Creon 단계(universe_driver) 실행 → 성공하면 원천 적재(creon_bronze). 출력은 그대로 흘려보낸다."""
    import subprocess
    from . import runner
    argv, cwd = runner.creon_argv(step, full)
    sources.log(f"[{step}] Windows 수집 시작: {' '.join(argv)}")
    rc = subprocess.call(argv, cwd=cwd, stdin=subprocess.DEVNULL)
    if rc != 0:
        sources.log(f"[{step}] Windows 수집 실패(rc={rc}) — 원천 적재는 하지 않음")
        return rc
    sources.log(f"[{step}] 완료 → 원천 적재(creon_bronze) 이어서")
    return 0 if sources.run("creon_bronze") else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["creon_then_bronze"]:
        ap = argparse.ArgumentParser()
        ap.add_argument("step", choices=sorted(runner_steps()))
        ap.add_argument("--full", action="store_true")
        a = ap.parse_args(argv[1:])
        os.makedirs(sources.path("logs"), exist_ok=True)
        sys.exit(creon_then_bronze(a.step, a.full))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("step", choices=sorted(sources.LINUX_STEPS))
    a = ap.parse_args(argv)
    os.makedirs(sources.path("logs"), exist_ok=True)
    ok = sources.run(a.step)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
