"""iShares Data Download(SpreadsheetML) Historical 시트 → 일별 NAV·발행주식수 CSV (2011-12~)."""
import re, sys, pandas as pd
D = "/home/kuzimo/projects/CTradeSystem/docs/research/20261025_foreign_channels/etf"
M = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
for n in sys.argv[1:]:
    x = open(f"{D}/raw_xls/{n}.xls", encoding="utf-8", errors="ignore").read()
    s = x.index('Name="Historical"'); e = min(i for i in (x.find("</Worksheet>", s), x.find("</ss:Worksheet>", s)) if i > 0); h = x[s:e]
    out = []
    for r in re.finditer(r"<(?:ss:)?Row[^>]*>(.*?)</(?:ss:)?Row>", h, re.S):
        c = [m.group(1) for m in re.finditer(r"<(?:ss:)?Data[^>]*>(.*?)</(?:ss:)?Data>", r.group(1), re.S)]
        m = re.fullmatch(r"([A-Z][a-z]{2}) (\d\d), (\d{4})", c[0]) if c else None
        if m and len(c) == 5 and c[3] not in ("--", "0"):
            out.append((int(m[3]) * 10000 + M[m[1]] * 100 + int(m[2]), float(c[1]), float(c[3])))
    df = pd.DataFrame(out, columns=["date", "nav", "shares"]).query("date>=20111201").sort_values("date")
    df.to_csv(f"{D}/hist/{n}_hist_shares.csv", index=False)
    print(n, len(df), df.date.iloc[0], df.date.iloc[-1], int(df.shares.iloc[0]), int(df.shares.iloc[-1]))
