"""global_macro 실행 — `python -m shared.data_library.global_macro.cli {fetch|panel|dashboard|all} [--keys k1,k2]`"""
import argparse
import sys

from . import runner, store


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
            store.log(f"[industry] 실패: {e}")
            if step == "industry":
                return False
    if step in ("kr_industry", "all"):
        from . import kr_industry
        try:
            kr_industry.run()
        except Exception as e:   # noqa: BLE001
            store.log(f"[kr_industry] 실패: {e}")
            if step == "kr_industry":
                return False
    if step in ("trade", "all"):
        from . import trade
        try:
            ok_t = trade.run()
            if step == "trade" and not ok_t:
                return False
        except Exception as e:   # noqa: BLE001
            store.log(f"[trade] 실패: {e}")
            if step == "trade":
                return False
    if step in ("us_imports", "all"):
        from . import us_imports
        try:
            ok_u = us_imports.run()
            if step == "us_imports" and not ok_u:
                return False
        except Exception as e:   # noqa: BLE001
            store.log(f"[us_imports] 실패: {e}")
            if step == "us_imports":
                return False
    if step in ("kr_trade_items", "all"):
        from . import kr_trade_items
        try:
            ok_k = kr_trade_items.run()
            if step == "kr_trade_items" and not ok_k:
                return False
        except Exception as e:   # noqa: BLE001
            store.log(f"[kr_trade_items] 실패: {e}")
            if step == "kr_trade_items":
                return False
    if step in ("panel", "all"):
        from . import panel
        panel.build()
    if step in ("reading", "all"):
        from . import reading_board
        try:
            reading_board.build()
        except Exception as e:   # noqa: BLE001
            store.log(f"[reading] 실패: {e}")
            if step == "reading":
                return False
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
