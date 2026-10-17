"""301건 전체에서 글 추출 → 섹션 지표 CSV (코드만, 본문 미열람). 출력: metrics.csv"""
import re, json, sys, csv, os
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(__file__))
from html.parser import HTMLParser
import html as _h
ROOT = "/home/kuzimo/projects/CTradeSystem/DataVault/staging/us_filings_sec"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics.csv")
class _P(HTMLParser):
    VOID = {"br", "hr", "img", "meta", "link", "input"}
    BLOCK = {"p", "div", "tr", "li", "br", "h1", "h2", "h3", "h4", "table"}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.out = []; self.stack = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            if tag == "br": self.out.append("\n")
            return
        hide = tag in ("script", "style", "ix:header") or "display:none" in (dict(attrs).get("style") or "").replace(" ", "").lower()
        self.stack.append(hide); self.skip += hide
        if tag in self.BLOCK and not self.skip: self.out.append("\n")
    def handle_endtag(self, tag):
        if tag in self.VOID or not self.stack: return
        self.skip -= self.stack.pop()
        if tag in self.BLOCK and not self.skip: self.out.append("\n")
    def handle_data(self, d):
        if not self.skip: self.out.append(d)
def text_of(path):
    s = open(path, "rb").read().decode("utf-8", "ignore"); p = _P(); p.feed(s)
    t = _h.unescape("".join(p.out)); t = re.sub(r"[ \t ]+", " ", t); t = re.sub(r"\n\s*\n+", "\n", t)
    return len(s), t
PAT = re.compile(r"^\s*Item\s+(1A|1B|1C|1|2|3|4|5|6|7A|7|8|9A|9B|9|10|11|12|13|14|15)\.?\s*(.*)$", re.I | re.M)
def sections(txt):
    raw = [(m.start(), m.group(1).upper()) for m in PAT.finditer(txt)]; out = {}
    hits = [h for i, h in enumerate(raw) if i == 0 or h[1] != raw[i - 1][1]]   # 쪽마다 같은 항목 머리글이 반복되는 문서(MSFT)는 연속 구간을 하나로
    for i, (pos, key) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(txt)
        if end - pos > len(out.get(key, "")): out[key] = txt[pos:end]
    return out
sent = lambda t: {re.sub(r"\W+", " ", s).strip().lower() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", t)) if len(s) > 40}
NEG = set("adverse adversely decline declines declined loss losses litigation lawsuit investigation harm harmed shortage disruption disruptions weak weaken weakness uncertain uncertainty fail failure failures penalties restrictions volatile impairment impairments restructuring".split())
POS = set("growth strong strength improve improved opportunity opportunities leadership innovation record increase increased benefit benefits".split())
KEYS = {"tariff": r"tariff", "ai": r"artificial intelligence|\bAI\b|machine learning", "china": r"\bchina\b", "supply": r"supply chain|supplier", "legal": r"antitrust|litigation|lawsuit|investigation", "cyber": r"cybersecurity|cyber|data breach", "inflation": r"inflation"}
def work(rec):
    p = f"{ROOT}/raw/{rec['ticker']}/{rec['file']}"
    rl, t = text_of(p); s = sections(t)
    mda = s.get("7", "") if rec["form"] == "10-K" else s.get("2", "")
    rf = s.get("1A", "")
    w = re.findall(r"[a-z]+", mda.lower()); n = max(len(w), 1)
    row = dict(rec); row.update(raw_kb=rl // 1000, text_kb=len(t) // 1000, rf_chars=len(rf), mda_words=len(w),
        neg_pm=sum(x in NEG for x in w) / n * 1000, pos_pm=sum(x in POS for x in w) / n * 1000)
    for k, rx in KEYS.items(): row[f"kw_{k}_pm"] = len(re.findall(rx, rf + " " + mda, re.I if k != "ai" else 0)) / max(len((rf + mda).split()), 1) * 1000
    row["_rf_sent"] = sorted(sent(rf)) if rf else []
    return row
if __name__ == "__main__":
    idx = json.load(open(ROOT + "/reference/index.json"))
    with Pool(6) as pool: rows = pool.map(work, idx, chunksize=4)
    rows.sort(key=lambda r: (r["ticker"], r["form"], r["filed"]))
    prev = {}
    for r in rows:   # 같은 종목·같은 양식의 직전 공시 대비 위험요인 새 문장 비율
        k = (r["ticker"], r["form"]); a = set(r.pop("_rf_sent")); b = prev.get(k)
        r["rf_new_frac"] = (len(a - b) / len(a)) if (b is not None and a and b) else ""
        r["rf_nsent"] = len(a); prev[k] = a
    cols = list(rows[0].keys())
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(rows)
    print(len(rows), "rows ->", OUT)
