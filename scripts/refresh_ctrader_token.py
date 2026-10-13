#!/usr/bin/env python3
"""cTrader(Spotware Open API) 접속 토큰 갱신 — refresh 토큰으로 새 access/refresh 토큰을 받아 ~/.bashrc 에 반영.

갱신하면 refresh 토큰도 교체될 수 있어(예전 것 무효) 응답의 새 값을 반드시 저장한다.
실행 전 ~/.bashrc 를 ~/.bashrc.bak_ctrader_<날짜> 로 백업(권한 600). 토큰 값은 출력하지 않는다.

  bash -ic ".venv/bin/python scripts/refresh_ctrader_token.py"
  이후 새 셸을 열거나 `source ~/.bashrc`
"""
import os
import re
import shutil
import sys
from datetime import datetime

import requests

TOKEN_URL = "https://openapi.ctrader.com/apps/token"


def main():
    for k in ("CTRADER_REFRESH_TOKEN", "CTRADER_CLIENT_ID", "CTRADER_SECRET"):
        if not os.environ.get(k):
            sys.exit(f"환경변수 {k} 없음 — bash -ic 로 실행하세요")
    rc = os.path.expanduser("~/.bashrc")
    bak = f"{rc}.bak_ctrader_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(rc, bak)
    os.chmod(bak, 0o600)

    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token", "refresh_token": os.environ["CTRADER_REFRESH_TOKEN"],
        "client_id": os.environ["CTRADER_CLIENT_ID"], "client_secret": os.environ["CTRADER_SECRET"]}, timeout=20)
    try:
        j = r.json()
    except ValueError:
        sys.exit(f"응답 해석 실패 HTTP {r.status_code}")
    if not j.get("accessToken"):
        sys.exit(f"갱신 실패 HTTP {r.status_code}: {j.get('errorCode')} {j.get('description')}")

    s = open(rc, encoding="utf-8").read()
    s, n1 = re.subn(r"^export CTRADER_ACCESS_TOKEN=.*$", lambda _: f"export CTRADER_ACCESS_TOKEN='{j['accessToken']}'", s, flags=re.M)
    n2 = 0
    if j.get("refreshToken"):
        s, n2 = re.subn(r"^export CTRADER_REFRESH_TOKEN=.*$", lambda _: f"export CTRADER_REFRESH_TOKEN='{j['refreshToken']}'", s, flags=re.M)
    if n1 != 1:
        sys.exit(f"~/.bashrc 에서 CTRADER_ACCESS_TOKEN 줄을 찾지 못함({n1}) — 백업 {bak}")
    open(rc, "w", encoding="utf-8").write(s)
    print(f"갱신 성공 · 유효기간 {int(j.get('expiresIn', 0)) // 86400}일 · refresh 토큰 교체 {'예' if n2 else '아니오'} · 백업 {bak}")


if __name__ == "__main__":
    main()
