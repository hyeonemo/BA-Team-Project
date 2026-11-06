"""[시험용] 네이버 웹문서 API로 판매 링크를 모으고, 우리 코드가 못 읽는 페이지를 Gemini(URL context)가 읽어 가격을 알려 주는지 확인한다.
- 우리 접속 결과(읽힘/막힘)와 Gemini 결과를 나란히 보여 준다. 막힌 곳을 Gemini가 읽으면 이 경로가 의미 있다.
- 환각 방지: 가격과 함께 '페이지에 적힌 원문'을 받아, 원문 안에 그 숫자가 실제로 있는지 검사한다(없으면 불일치 표시).
- 키(GEMINI_API_KEY)는 GitHub Secrets 에만 둔다. 모델 이름은 환경변수 GEMINI_MODEL 로 바꿀 수 있다.
"""
import os, re, json, time, urllib.parse, urllib.request, urllib.error
import price_probe as pp

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
N = int(os.environ.get("PROBE_N", "12"))
QUERY = os.environ.get("PROBE_QUERY", "에스트라 에이시카365 가격")
PROMPT = """아래 URL의 상품 페이지를 읽고 JSON 한 개만 출력하세요(설명 금지).
{"readable": true/false, "product_name": "페이지의 상품명", "volume": "용량(예: 60ml) 또는 null", "price_krw": 숫자 또는 null, "price_quote": "페이지에 적힌 가격 문구 원문 그대로 또는 null"}
규칙: 페이지에서 직접 확인한 값만 쓰고, 못 읽었거나 가격이 없으면 readable:false 또는 null 로 두세요. 추측·계산 금지. 세트·묶음·할인 전 가격이 있으면 현재 판매가를 쓰세요.
URL: """

_tried = False
def list_models():
    key = os.environ["GEMINI_API_KEY"].strip()
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": key})
    ms = json.load(urllib.request.urlopen(req, timeout=30)).get("models", [])
    return [m["name"].split("/")[-1] for m in ms if "generateContent" in m.get("supportedGenerationMethods", [])]

def pick(names):
    """flash 계열 중 최신 버전을 앞에 둔다(lite·이미지·음성·실시간 등은 제외)."""
    bad = ("lite", "image", "tts", "live", "audio", "embedding", "robotics", "vision", "customtools", "computer")
    c = [n for n in names if "flash" in n and not any(b in n for b in bad)]
    def ver(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n); return (float(m.group(1)) if m else 0, "preview" not in n, "exp" not in n, n)
    return sorted(c, key=ver, reverse=True)

def _call(url):
    key = os.environ["GEMINI_API_KEY"].strip()
    body = {"contents": [{"parts": [{"text": PROMPT + url}]}], "tools": [{"url_context": {}}], "generationConfig": {"temperature": 0}}
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": key})
    r = json.load(urllib.request.urlopen(req, timeout=120))
    cand = r["candidates"][0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    meta = cand.get("urlContextMetadata", {}).get("urlMetadata", [])
    status = meta[0].get("urlRetrievalStatus", "") if meta else "메타 없음"
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0)) if m else {}
    return data, status

NOTES = []
_cands, _ci = [], 0
def gemini(url):
    """404(모델 없음)이면 쓸 수 있는 모델을 골라 바꾸고, 503·429·시간초과(붐빔)이면 기다렸다 재시도, 계속 안 되면 다음 모델로 넘어간다."""
    global MODEL, _tried, _cands, _ci
    fails = 0
    for attempt in range(8):
        try:
            return _call(url)
        except urllib.error.HTTPError as e:
            if e.code == 404 and not _tried:
                _tried = True; names = list_models(); _cands = pick(names)
                NOTES.append(f"요청한 모델 `{MODEL}` 은(는) 사용할 수 없어 목록에서 선택: " + ", ".join(_cands[:6]))
                NOTES.append("generateContent 가능한 모델: " + ", ".join(names[:60]))
                if not _cands: raise
                MODEL = _cands[0]; continue
            if e.code in (429, 500, 503):
                fails += 1
                if fails >= 3 and _cands and _ci + 1 < len(_cands):
                    _ci += 1; MODEL = _cands[_ci]; fails = 0
                    NOTES.append(f"붐벼서 다음 모델로 변경: `{MODEL}`")
                else: time.sleep(min(60, 5 * 2 ** fails))
                continue
            raise
        except (TimeoutError, OSError) as e:
            fails += 1; time.sleep(min(60, 5 * 2 ** fails)); continue
    raise RuntimeError("재시도 초과(서버 붐빔)")

def ours(url):
    try:
        if not pp.allowed(url): return "robots 금지", None
        page = pp.fetch(url); price, how, _ = pp.extract(page)
        return ("읽힘·가격 " + f"{price:,.0f}원" if price else "읽힘·가격 없음"), price
    except urllib.error.HTTPError as e: return f"막힘 {e.code}", None
    except Exception as e: return f"오류 {type(e).__name__}", None

def main():
    items = pp.search(QUERY, N); rows = []
    for it in items:
        url = it["link"]; dom = urllib.parse.urlparse(url).netloc; t = pp.strip_tags(it["title"])[:36]
        o, op = ours(url)
        try:
            d, st = gemini(url)
            price = d.get("price_krw"); quote = d.get("price_quote") or ""
            ok = bool(price) and str(int(float(price))) in re.sub(r"[,\s]", "", quote)
            g = f"{float(price):,.0f}원" if price else "가격 없음"
            check = "원문 일치" if ok else ("원문 불일치⚠" if price else "-")
            rows.append((dom, t, o, g, st, check, quote[:30]))
        except urllib.error.HTTPError as e: rows.append((dom, t, o, f"Gemini 오류 {e.code}", e.read()[:80].decode("utf-8", "ignore"), "", ""))
        except Exception as e: rows.append((dom, t, o, f"Gemini 오류 {type(e).__name__}", str(e)[:60], "", ""))
    out = [f"## Gemini 가격 읽기 시험", "", "| 사이트 | 제목 | 우리 접속 | Gemini 가격 | 가져오기 상태 | 검증 | 원문 |", "|---|---|---|---|---|---|---|"]
    out += [f"| {' | '.join(r)} |" for r in rows]
    blocked = [r for r in rows if r[2].startswith(("막힘", "robots")) and r[3].endswith("원")]
    out += ["", *[f"- {n}" for n in dict.fromkeys(NOTES)], f"- 최종 사용 모델: `{MODEL}`", ""]
    out += ["", f"**우리는 못 읽었는데 Gemini가 가격을 읽은 페이지: {len(blocked)}개**"]
    text = "\n".join(out); print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"): open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8").write(text + "\n")

if __name__ == "__main__":
    main()
