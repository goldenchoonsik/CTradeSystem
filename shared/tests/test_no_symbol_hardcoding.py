# -*- coding: utf-8 -*-
"""운영 코드에 종목 심볼/별칭 리터럴이 새로 늘어나는 것을 막는 래칫(ratchet) 가드.

종목 신원(이름/시장/별칭/자산 그룹)의 정답은 마스터 원장(symbols_registry.json) 하나다. 코드에
`if sym == "10100"`, `config.get("SYMBOL_UPBIT", "KRW-BTC")` 같은 리터럴이 박히면 원장과 따로 노는
두 번째 신원 체계가 생겨 버그가 두더지처럼 반복된다.

규칙
  - 파일별 리터럴 개수가 baseline 보다 늘면 실패한다 (새 하드코딩 금지 → 원장/설정에서 읽을 것).
  - 줄어드는 것은 자유다. 줄였다면 baseline 을 갱신해 그 성과를 고정한다:
        python -m shared.tests.test_no_symbol_hardcoding --update
  - 테스트/주석/문서/데이터 폴더는 검사하지 않는다.
"""
import json
import os
import re
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "symbol_hardcode_baseline.json")
LEDGER_PATH = os.path.join(REPO_ROOT, "shared", "config", "symbols_registry.json")

_EXCLUDE_DIRS = {".git", ".venv", "node_modules", "__pycache__", "DataVault", "logs", "vultr_logs",
                 "scratch", "deploy", "docs", ".pytest_cache"}
_EXTS = {".py", ".js", ".html"}
# 리터럴을 '한 곳에서 정의'하는 규약 모듈 — 여기 있는 이름은 다른 코드가 상수/함수로 참조한다
_DEFINITION_MODULES = {"shared/core/stream_keys.py", "shared/config/market_policies.py"}

# 심볼 모양의 따옴표 리터럴: KRX 코드(6자리), KRW-XXX, 해외 거래소 표기
_SYM_LITERAL = re.compile(r"""["'](\d{6}|\d{2}[0-9A-Z]{4}|A\d{6}|KRW-[A-Z0-9]{2,10}|[A-Z0-9]{3,12}(?:USDT|-PERP|-USD))["']""")
_COMPARE = re.compile(r"(==|in \(|in \[|\.startswith|===)")
_ALIASES = ("futures", "spot")


def _ledger_keys() -> set:
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        return {k.upper() for k in json.load(f)}


def _is_test(path: str) -> bool:
    return "/tests/" in path.replace(os.sep, "/") or os.path.basename(path).startswith("test_") \
        or os.path.basename(path) == "conftest.py"


def scan() -> Counter:
    """{ "상대경로::SYM"|"상대경로::ALIAS": 개수 } — 운영 코드의 하드코딩 리터럴 수"""
    ledger = _ledger_keys()
    counts = Counter()
    for dp, dn, fn in os.walk(REPO_ROOT):
        # 숨김 폴더(.claude/worktrees 의 저장소 사본 등)는 운영 코드가 아니다 — 가지치기 안 하면 사본이 위반으로 잡힌다(2026-10-22)
        dn[:] = [d for d in dn if d not in _EXCLUDE_DIRS and not d.startswith(".")]
        for name in fn:
            if os.path.splitext(name)[1] not in _EXTS:
                continue
            path = os.path.join(dp, name)
            if _is_test(path):
                continue
            rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
            if rel in _DEFINITION_MODULES:
                continue
            try:
                lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
            except OSError:
                continue
            for ln in lines:
                s = ln.strip()
                if not s or s.startswith(("#", "//", "*", "/*", "<!--")):
                    continue
                for m in _SYM_LITERAL.finditer(ln):
                    v = m.group(1).upper()
                    if v in ledger or v.startswith("KRW-") or v.endswith(("USDT", "-PERP", "-USD")):
                        counts[f"{rel}::SYM"] += 1
                if _COMPARE.search(ln):
                    for a in _ALIASES:
                        if re.search(rf"""["']{a}["']""", ln, re.I):
                            counts[f"{rel}::ALIAS"] += 1
    return counts


def _load_baseline() -> dict:
    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_no_new_symbol_hardcoding_in_production_code():
    baseline = _load_baseline()
    now = scan()
    grew = {k: (baseline.get(k, 0), n) for k, n in now.items() if n > baseline.get(k, 0)}
    assert not grew, (
        "운영 코드에 종목 심볼/별칭 리터럴이 새로 늘었습니다 (파일::종류 -> (기준, 현재)):\n  "
        + "\n  ".join(f"{k}: {b} -> {n}" for k, (b, n) in sorted(grew.items()))
        + "\n종목 신원은 코드에 박지 말고 원장(config_manager)/설정에서 읽으세요. "
          "정말 필요한 리터럴이면 이유를 남기고 `python -m shared.tests.test_no_symbol_hardcoding --update` 로 기준을 갱신하세요."
    )


def test_baseline_is_not_stale_by_a_lot():
    """리터럴을 줄였는데 baseline 을 갱신하지 않으면 그 여유분으로 새 하드코딩이 조용히 들어갈 수 있다."""
    baseline = _load_baseline()
    now = scan()
    slack = {k: (b, now.get(k, 0)) for k, b in baseline.items() if now.get(k, 0) < b}
    assert not slack, (
        "하드코딩이 줄었습니다 — `python -m shared.tests.test_no_symbol_hardcoding --update` 로 baseline 을 갱신해 고정하세요:\n  "
        + "\n  ".join(f"{k}: {b} -> {n}" for k, (b, n) in sorted(slack.items()))
    )


if __name__ == "__main__":
    if "--update" in sys.argv:
        data = dict(sorted(scan().items()))
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"baseline 갱신: {len(data)}개 항목, 합계 {sum(data.values())}")
    else:
        print(json.dumps(dict(sorted(scan().items())), ensure_ascii=False, indent=2))
