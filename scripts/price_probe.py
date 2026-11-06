"""[시험용] 웹문서 검색 API로 제품 판매 페이지를 찾고, 각 페이지에서 가격을 읽을 수 있는지 확인한다.
- 기존 알림 파이프라인과 분리된 수동 실행용이다(결과는 실행 로그와 요약에만 남고 저장소 파일은 바꾸지 않는다).
- 지킬 것: robots.txt 에서 막은 주소는 건너뛴다 · 도메인당 1초 간격 · 접속이 막히면(403 등) 우회하지 않고 '막힘'으로 기록한다.
- 가격은 페이지가 스스로 밝혀 둔 정보(JSON-LD offers, product:price 메타)만 읽는다. 본문 글자에서 추측하지 않는다.
"""
import os, re, json, time, html, urllib.request, urllib.parse, urllib.robotparser
from collections import defaultdict

API = "https://naverapihub.apigw.ntruss.com/search/v1/webkr"
UA = "BA-PBL-price-probe/0.1 (university course project; contact via GitHub repo hyeonemo/BA-Team-Project)"
PRODUCTS = {  # 표시 이름: (검색어, 상품명에 있어야 하는 말)
    "아토베리어365 크림": ("에스트라 아토베리어365 크림 가격", ["아토베리어365", "크림"]),
    "에이시카365": ("에스트라 에이시카365 가격", ["에이시카365"]),
    "테라크네365": ("에스트라 테라크네365 가격", ["테라크네365"]),
    "리제덤365": ("에스트라 리제덤365 가격", ["리제덤365"]),
}
PER_QUERY = int(os.environ.get("PROBE_N", "20"))

def strip_tags(s): return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()
def norm(s): return re.sub(r"\s+", "", s).lower()

def search(q, n):
    cid, sec = os.environ["NAVER_CLIENT_ID"].strip(), os.environ["NAVER_CLIENT_SECRET"].strip()
    url = f"{API}?{urllib.parse.urlencode({'query': q, 'display': n, 'start': 1, 'format': 'json'})}"
    req = urllib.request.Request(url, headers={"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec})
    return json.load(urllib.request.urlopen(req, timeout=30)).get("items", [])

_robots, _last = {}, defaultdict(float)
def allowed(url):
    p = urllib.parse.urlparse(url); base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser(); rp.set_url(base + "/robots.txt")
        try:
            rq = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA})
            rp.parse(urllib.request.urlopen(rq, timeout=10).read().decode("utf-8", "ignore").splitlines())
        except Exception: rp = None            # robots.txt 가 없거나 못 읽으면 허용으로 본다
        _robots[base] = rp
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(UA, url)

def fetch(url):
    d = urllib.parse.urlparse(url).netloc
    wait = 1.0 - (time.time() - _last[d])
    if wait > 0: time.sleep(wait)
    _last[d] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "ko"})
    r = urllib.request.urlopen(req, timeout=12)
    return r.read(600_000).decode(r.headers.get_content_charset() or "utf-8", "ignore")

def _num(v):
    try: return float(str(v).replace(",", ""))
    except Exception: return None

def extract(page):
    """(가격, 근거, 페이지 제목). 페이지가 밝혀 둔 값만 쓴다."""
    title = ""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page, re.I) or re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    if m: title = strip_tags(m.group(1))
    for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.I | re.S):
        try: data = json.loads(blob.strip())
        except Exception: continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list): stack.extend(x); continue
            if not isinstance(x, dict): continue
            stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
            t = x.get("@type"); t = t if isinstance(t, list) else [t]
            if "Product" in t or "Offer" in t or "AggregateOffer" in t:
                offers = x.get("offers", x if "price" in x or "lowPrice" in x else None)
                for o in (offers if isinstance(offers, list) else [offers]):
                    if isinstance(o, dict):
                        v = _num(o.get("price", o.get("lowPrice")))
                        if v and v >= 100: return v, "JSON-LD", x.get("name") or title
    for pat in [r'<meta[^>]+(?:property|name)=["\'](?:product:price:amount|og:price:amount)["\'][^>]+content=["\']([\d.,]+)',
                r'<meta[^>]+itemprop=["\']price["\'][^>]+content=["\']([\d.,]+)']:
        m = re.search(pat, page, re.I)
        if m and _num(m.group(1)) and _num(m.group(1)) >= 100: return _num(m.group(1)), "메타태그", title
    return None, "", title

def main():
    rows, summary = [], {}
    for name, (q, must) in PRODUCTS.items():
        try: items = search(q, PER_QUERY)
        except Exception as e: print(name, "검색 실패:", e); continue
        seen, st = set(), defaultdict(int)
        for it in items:
            link, t = it["link"], strip_tags(it["title"])
            dom = urllib.parse.urlparse(link).netloc
            if link in seen: continue
            seen.add(link); st["페이지"] += 1
            row = {"제품": name, "도메인": dom, "상태": "", "가격": None, "근거": "", "제목": t[:60], "상품명 일치": False, "링크": link}
            try:
                if not allowed(link): row["상태"] = "robots 금지"; st["robots 금지"] += 1
                else:
                    page = fetch(link); price, how, ptitle = extract(page)
                    row["상태"] = "읽음"; st["읽음"] += 1
                    row["상품명 일치"] = all(norm(k) in norm(t + " " + ptitle) for k in must)
                    if price: row["가격"], row["근거"] = price, how; st["가격 확인"] += 1
                    if price and row["상품명 일치"]: st["가격+상품명 일치"] += 1
            except urllib.error.HTTPError as e: row["상태"] = f"막힘 HTTP {e.code}"; st["막힘"] += 1
            except Exception as e: row["상태"] = f"오류 {type(e).__name__}"; st["오류"] += 1
            rows.append(row)
        summary[name] = dict(st)
    out = ["## 가격 읽기 시험 결과", "", "| 제품 | 확인한 페이지 | 읽음 | 막힘/금지/오류 | 가격 확인 | 가격+상품명 일치 |", "|---|---|---|---|---|---|"]
    for n, s in summary.items():
        out.append(f"| {n} | {s.get('페이지', 0)} | {s.get('읽음', 0)} | {s.get('막힘', 0) + s.get('robots 금지', 0) + s.get('오류', 0)} | {s.get('가격 확인', 0)} | {s.get('가격+상품명 일치', 0)} |")
    out += ["", "### 가격을 읽은 페이지", "", "| 제품 | 도메인 | 가격 | 근거 | 상품명 일치 | 제목 |", "|---|---|---|---|---|---|"]
    for r in sorted([r for r in rows if r["가격"]], key=lambda r: (r["제품"], r["가격"])):
        out.append(f"| {r['제품']} | {r['도메인']} | {r['가격']:,.0f}원 | {r['근거']} | {'O' if r['상품명 일치'] else 'X'} | {r['제목']} |")
    out += ["", "### 전체 목록(상태별)", "", "| 제품 | 도메인 | 상태 | 제목 |", "|---|---|---|---|"]
    out += [f"| {r['제품']} | {r['도메인']} | {r['상태']} | {r['제목']} |" for r in rows]
    text = "\n".join(out); print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"): open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8").write(text + "\n")

if __name__ == "__main__":
    main()
