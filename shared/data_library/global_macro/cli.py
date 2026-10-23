"""global_macro 실행 — `python -m shared.data_library.global_macro.cli {fetch|panel|dashboard|all} [--keys k1,k2]`"""
import argparse
import sys

from . import issues, runner, store


def run(step: str, keys=None) -> bool:
    if step in ("fetch", "all"):
        from . import sources
        ok = sources.fetch_all(keys)
        if step == "fetch":
            return ok
        if not ok:
            store.log("[all] 일부 원천 실패 — 남은 단계는 있는 데이터로 계속")
    if step in ("industry", "all"):
        from . import industry
        try:
            industry.run()
        except Exception as e:   # noqa: BLE001
            issues.step_failed("industry", e)
            if step == "industry":
                return False
        else:
            issues.step_ok("industry")
    if step in ("kr_industry", "all"):
        from . import kr_industry
        try:
            kr_industry.run()
        except Exception as e:   # noqa: BLE001
            issues.step_failed("kr_industry", e)
            if step == "kr_industry":
                return False
        else:
            issues.step_ok("kr_industry")
    if step in ("trade", "all"):
        from . import trade
        try:
            ok_t = trade.run()
            if step == "trade" and not ok_t:
                return False
        except Exception as e:   # noqa: BLE001
            issues.step_failed("trade", e)
            if step == "trade":
                return False
        else:
            issues.step_ok("trade")
    if step in ("us_imports", "all"):
        from . import us_imports
        try:
            ok_u = us_imports.run()
            if step == "us_imports" and not ok_u:
                return False
        except Exception as e:   # noqa: BLE001
            issues.step_failed("us_imports", e)
            if step == "us_imports":
                return False
        else:
            issues.step_ok("us_imports")
    if step in ("eu_imports", "all"):
        from . import eu_imports
        try:
            ok_e = eu_imports.run()
            if step == "eu_imports" and not ok_e:
                return False
        except Exception as e:   # noqa: BLE001
            issues.step_failed("eu_imports", e)
            if step == "eu_imports":
                return False
        else:
            issues.step_ok("eu_imports")
    if step in ("kr_trade_items", "all"):
        from . import kr_trade_items
        try:
            ok_k = kr_trade_items.run()
            if step == "kr_trade_items" and not ok_k:
                return False
        except Exception as e:   # noqa: BLE001
            issues.step_failed("kr_trade_items", e)
            if step == "kr_trade_items":
                return False
        else:
            issues.step_ok("kr_trade_items")
    if step in ("kr_outlook", "all"):
        from . import kr_outlook
        try:
            ok_o = kr_outlook.run()
            if step == "kr_outlook" and not ok_o:
                return False
        except Exception as e:   # noqa: BLE001
            issues.step_failed("kr_outlook", e)
            if step == "kr_outlook":
                return False
        else:
            issues.step_ok("kr_outlook")
    if step in ("kr_ksic", "all"):
        from . import kr_ksic
        try:
            ok_s = kr_ksic.run()
            if step == "kr_ksic" and not ok_s:
                return False
        except Exception as e:   # noqa: BLE001
            issues.step_failed("kr_ksic", e)
            if step == "kr_ksic":
                return False
        else:
            issues.step_ok("kr_ksic")
    if step == "kr_export_share":                                   # 수동 단계(전체 갱신 제외) — 사업연도는 지난해
        from . import kr_export_share
        try:
            kr_export_share.run_grid()                              # 최근 사업연도 → 2년 격자, 한도에 걸리면 멈추고 다음 실행에 이어서
        except Exception as e:   # noqa: BLE001
            issues.step_failed("export_share", e)
        else:
            issues.step_ok("export_share")
            return False
    if step in ("panel", "all"):
        from . import panel
        panel.build()
    if step in ("reading", "all"):
        from . import reading_board
        try:
            reading_board.build()
        except Exception as e:   # noqa: BLE001
            issues.step_failed("reading", e)
            if step == "reading":
                return False
        else:
            issues.step_ok("reading")
    if step in ("dashboard", "all"):
        from . import dashboard
        dashboard.build()
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("step", choices=[s["key"] for s in runner.STEPS])
    ap.add_argument("--keys", default="", help="fetch 대상 키(쉼표) — 비우면 전부")
    a = ap.parse_args(argv)
    keys = [k for k in a.keys.split(",") if k] or None
    sys.exit(0 if run(a.step, keys) else 1)


if __name__ == "__main__":
    main()
