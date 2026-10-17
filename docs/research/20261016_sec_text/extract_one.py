"""10-K 하나에서 글만 추출 → 섹션 분리 → 코드로 계산한 지표 (모델이 본문을 읽지 않음). 토큰은 글자수/4 로 어림."""
import re, sys, glob, html
from html.parser import HTMLParser
RAW = "/home/kuzimo/projects/CTradeSystem/DataVault/staging/us_filings_sec/raw/AAPL"
class _P(HTMLParser):
    """script/style/ix:header 와 display:none 영역을 건너뛰고 글만 모은다(표준 라이브러리, 설치 불필요)."""
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
    s = open(path, "rb").read().decode("utf-8", "ignore")
    p = _P(); p.feed(s)
    txt = html.unescape("".join(p.out)); txt = re.sub(r"[ \t\u00a0]+", " ", txt); txt = re.sub(r"\n\s*\n+", "\n", txt)
    return len(s), txt
def sections(txt):
    pat = re.compile(r"^\s*Item\s+(1A|1B|1C|1|2|3|4|5|6|7A|7|8|9A|9B|9|10|11|12|13|14|15)\.?\s*(.*)$", re.I | re.M)
    hits = [(m.start(), m.group(1).upper(), m.group(2)[:50]) for m in pat.finditer(txt)]
    out = {}
    for i, (pos, key, _) in enumerate(hits):          # 같은 항목이 목차에도 나오므로 가장 긴 구간을 본문으로 채택
        end = hits[i + 1][0] if i + 1 < len(hits) else len(txt)
        if end - pos > len(out.get(key, "")): out[key] = txt[pos:end]
    return out
files = sorted(glob.glob(RAW + "/*_10-K_*"))
f_new, f_old = files[-1], files[-2]
raw_len, new = text_of(f_new); _, old = text_of(f_old)
print(f_new.split("/")[-1], "| 원본 HTML %.0fKB → 글 %.0fKB (%.0f%%), 약 %d단어, 토큰 어림 %.0f천" % (raw_len / 1e3, len(new) / 1e3, len(new) / raw_len * 100, len(new.split()), len(new) / 4 / 1e3))
sn, so = sections(new), sections(old)
print("\n섹션별 글자수(KB) / 토큰 어림(천)")
for k in ("1", "1A", "7", "7A", "8", "9A"):
    if k in sn: print(f"  Item {k:3s} {len(sn[k])/1e3:6.0f}KB  {len(sn[k])/4/1e3:5.0f}천토큰")
rf_n, rf_o = sn.get("1A", ""), so.get("1A", "")
sent = lambda t: {re.sub(r"\W+", " ", s).strip().lower() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", t)) if len(s) > 40}
a, b = sent(rf_n), sent(rf_o)
print(f"\n위험요인 문장 수 올해 {len(a)} / 작년 {len(b)} | 올해에만 있는 문장 {len(a - b)} ({len(a-b)/max(len(a),1):.0%}), 작년에만 있던 문장 {len(b - a)} ({len(b-a)/max(len(b),1):.0%}), 유사도(자카드) {len(a & b)/max(len(a | b),1):.2f}")
NEG = "adverse adversely decline declines loss losses litigation lawsuit investigation regulatory harm harmed shortage disruption disruptions weak weaken uncertain uncertainty risk risks fail failure failures penalties tariffs restrictions volatile".split()
POS = "growth strong strength improve improved opportunity opportunities leadership innovation record increase increased benefit benefits".split()
cnt = lambda t, ws: sum(1 for w in re.findall(r"[a-z]+", t.lower()) if w in ws)
for nm, t in (("올해", sn.get("7", "")), ("작년", so.get("7", ""))):
    n = len(t.split()); print(f"MD&A {nm}: {n}단어, 부정어 {cnt(t, set(NEG))/n*1000:.1f}‰, 긍정어 {cnt(t, set(POS))/n*1000:.1f}‰ (임시 단어목록, 사전 아님)")
open("/tmp/claude-1000/-home-kuzimo-projects-CTradeSystem/7b211566-2b9f-4089-9da2-bd7fdf56614d/scratchpad/aapl_10k_text.txt", "w").write(new)
