"""
계좌 원장 (Account Ledger) — accounts.json
=========================================
일 단위 배분 엔진의 운영 단위는 '계좌'다 (2026-10-10). 현금·세금·주문 경로가 모두 계좌 단위로 정해지기 때문이다.
종목은 계좌 안의 슬리브일 뿐이고, 같은 배분 정책을 계좌마다 다른 세금 조건·실행 방식으로 돌린다.

필드:
  name            : 사람이 읽는 이름
  broker          : 주문 경로 (KIS 등)
  account_type    : 과세 규칙을 고르는 계좌 유형 (shared/core/tax_rules.ACCOUNT_TYPES — GENERAL/ISA/PENSION)
  execution       : auto(API 자동 주문) / manual(주문표만 발송, 사람이 주문) — ISA·연금은 증권사 API 가 주문을 받지 않아 manual
  credentials_env : 계좌번호 등 비밀값이 들어 있는 '환경변수 이름'. 비밀값 자체는 이 파일에 절대 쓰지 않는다.

이 파일은 반드시 이 모듈의 함수로만 접근한다 (shared/tests/test_symbol_ledger.py 가 직접 접근을 막는다).
"""

import json
import os

_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_PATH = os.path.join(_CONFIG_DIR, "accounts.json")

BROKERS = ("KIS",)
EXECUTION_MODES = ("auto", "manual")
_CREDENTIAL_KEYS = ("acc_no", "acc_code")

_cache = {"sig": None, "raw": None}


class UnknownAccountError(KeyError):
    """원장에 없는 계좌 ID"""


def validate_accounts(accounts) -> list:
    """계좌 원장 구조 검증. 문제 목록(문자열)을 반환하며 비어 있으면 정상."""
    from shared.core.tax_rules import ACCOUNT_TYPES
    if not isinstance(accounts, dict):
        return [f"계좌 원장 최상위는 dict 여야 합니다 (현재 {type(accounts).__name__})"]
    problems = []
    for key, a in accounts.items():
        if not isinstance(a, dict):
            problems.append(f"{key}: 엔트리가 dict 가 아닙니다")
            continue
        if key != str(key).strip().upper():
            problems.append(f"{key}: 계좌 ID 는 공백 없는 대문자여야 합니다")
        if not str(a.get("name", "")).strip():
            problems.append(f"{key}: name 이 비어 있습니다")
        if a.get("broker") not in BROKERS:
            problems.append(f"{key}: broker({a.get('broker')!r}) 는 {BROKERS} 중 하나여야 합니다")
        if a.get("account_type") not in ACCOUNT_TYPES:
            problems.append(f"{key}: account_type({a.get('account_type')!r}) 은 {ACCOUNT_TYPES} 중 하나여야 합니다")
        if a.get("execution") not in EXECUTION_MODES:
            problems.append(f"{key}: execution({a.get('execution')!r}) 은 {EXECUTION_MODES} 중 하나여야 합니다")
        cred = a.get("credentials_env")
        if a.get("execution") == "auto":
            if not isinstance(cred, dict) or any(not str(cred.get(k, "")).strip() for k in _CREDENTIAL_KEYS):
                problems.append(f"{key}: 자동 주문 계좌는 credentials_env 에 {_CREDENTIAL_KEYS} 환경변수 이름이 있어야 합니다")
        if isinstance(cred, dict):
            for k, v in cred.items():
                # 환경변수 이름만 허용: 대문자·숫자·밑줄. 숫자로 된 계좌번호를 직접 적는 실수를 막는다.
                if not isinstance(v, str) or not v or not all(c.isupper() or c.isdigit() or c == "_" for c in v) or v[0].isdigit():
                    problems.append(f"{key}: credentials_env.{k} 는 환경변수 이름이어야 합니다 (비밀값 직접 기입 금지)")
    return problems


def _read_raw() -> dict:
    try:
        st = os.stat(ACCOUNTS_PATH)
    except OSError:
        return {}
    sig = (ACCOUNTS_PATH, st.st_mtime_ns, st.st_size)
    if _cache["sig"] == sig and _cache["raw"] is not None:
        return _cache["raw"]
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    problems = validate_accounts(raw)
    if problems:
        raise ValueError("계좌 원장 검증 실패: " + "; ".join(problems[:5]))
    _cache["sig"], _cache["raw"] = sig, raw
    return raw


def load_accounts() -> dict:
    """{계좌ID: 엔트리} 사본"""
    return {k: dict(v) for k, v in _read_raw().items()}


def get_account(account_id: str) -> dict:
    """계좌 엔트리 사본 (+ account_id). 없으면 UnknownAccountError."""
    key = str(account_id or "").strip().upper()
    raw = _read_raw()
    if key not in raw:
        raise UnknownAccountError(f"계좌 원장에 없는 계좌: {account_id!r} (등록된 계좌: {sorted(raw)})")
    return {**raw[key], "account_id": key}
