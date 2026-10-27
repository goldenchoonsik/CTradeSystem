"""openpyxl 없이 xlsx 첫 시트를 행 리스트로 읽는 최소 파서(문자열·숫자만)."""
import re, zipfile
def read(path, sheet=1):
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        t = z.read("xl/sharedStrings.xml").decode("utf-8")
        ss = [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<si>(.*?)</si>", t, re.S)]
    x = z.read(f"xl/worksheets/sheet{sheet}.xml").decode("utf-8")
    rows = []
    for r in re.finditer(r"<row[^>]*>(.*?)</row>", x, re.S):
        row = {}
        for c in re.finditer(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', r.group(1), re.S):
            col, attrs, inner = c.group(1), c.group(2), c.group(3) or ""
            v = re.search(r"<v>(.*?)</v>", inner, re.S)
            if not v:
                v2 = re.search(r"<t[^>]*>(.*?)</t>", inner, re.S); val = v2.group(1) if v2 else None
            else:
                val = v.group(1)
                if 't="s"' in attrs: val = ss[int(val)]
            row[col] = val
        rows.append(row)
    return rows
if __name__ == "__main__":
    import sys
    for r in read(sys.argv[1])[:int(sys.argv[2]) if len(sys.argv) > 2 else 8]: print(r)
