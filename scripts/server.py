"""기업 등록 웹 서버 (표준 라이브러리만 사용).

    python scripts/server.py                 # http://127.0.0.1:8000 (키가 있으면 네이버에서 실제로 받는다)
    python scripts/server.py --demo          # 키 없이: 저장소의 검색 데이터로 대신한다 (발표 시연용)
    python scripts/server.py --daily-at 07:30  # 매일 이 시각(한국 시간)에 모든 기업을 자동 실행 (실수집 모드는 기본 07:30)
    ACCESS_CODE=비밀번호 python scripts/server.py --host 0.0.0.0   # 밖에 열 때는 접속 코드가 꼭 필요하다

흐름: 폼 제출(POST /api/register) → 검증 → tenants/<기업>/ 만들기 → 백그라운드로
      과거 데이터 받기 → 연결 강도 계산 → 기준선·알림 계산 → 슬랙 발송 → 화면에서 진행 상태 확인(GET /api/status).
이후 매일 자동 실행(daily-alert)이 같은 기업 폴더를 이어서 돈다.

보호 장치(시범 수준): 접속 코드(환경변수 ACCESS_CODE, 없으면 내 컴퓨터에서만 연다), IP당 시간당 등록 5회 제한, 입력 검증.
기업별 로그인·DB 는 없다. 실제 서비스로 가려면 README 의 "시범 배포" 아래 한계를 먼저 해결해야 한다.
"""
import os, re, sys, json, time, hmac, shutil, threading, datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tenant as T

PAGE = os.path.join(T.REPO, "docs", "onboard.html")
DEMO_CSV = os.path.join(T.REPO, "data", "search_daily.csv")
MAX_BODY = 100_000
HOOK_RE = re.compile(r"^https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+$")
STAGES = [("collect", "과거 검색 데이터 받기"), ("calibrate", "연결 강도 계산"), ("compute", "기준선·알림 계산"), ("notify", "슬랙 발송")]

DEMO = False
ACCESS_CODE = os.environ.get("ACCESS_CODE", "")
KST = dt.timezone(dt.timedelta(hours=9))
DAILY_AT = None                  # "HH:MM" (한국 시간) 이면 매일 자동 실행
LAST_RUN = {"at": None, "failures": None, "running": False, "log": ""}
RATE = {}                        # ip -> 최근 등록 시각들
RATE_MAX, RATE_WINDOW = 5, 3600
LOCK = threading.Lock()          # 네이버 호출·계산은 한 번에 하나씩
STATUS = {}                      # slug -> {"stage", "done", "error", "result", "started"}


class FormError(ValueError):
    pass


def _s(x, label, n=40, required=False):
    x = re.sub(r"^\s*예\)\s*", "", x or "").strip() if isinstance(x, str) else ""    # 입력칸의 "예) " 표시는 떼고 쓴다
    if required and not x: raise FormError(f"{label}을(를) 입력해 주세요.")
    if len(x) > n: raise FormError(f"{label}이(가) 너무 깁니다 ({n}자 이내).")
    if re.search(r"[\x00-\x1f]", x): raise FormError(f"{label}에 쓸 수 없는 문자가 있습니다.")
    return x


def _list(xs, label, maxn=20):
    if not isinstance(xs, list): xs = []
    out = T._uniq([_s(x, label) for x in xs])
    if len(out) > maxn: raise FormError(f"{label}은(는) {maxn}개까지 넣을 수 있습니다.")
    return out


def parse_form(raw):
    """브라우저에서 온 내용을 검증하고 깨끗하게 만든다. 이상하면 FormError(사용자에게 그대로 보여 줄 문장)."""
    if not isinstance(raw, dict): raise FormError("입력 형식이 올바르지 않습니다.")
    brand = _s(raw.get("brand"), "브랜드 이름", required=True)
    try: thr = float(raw.get("threshold", 1.0))
    except (TypeError, ValueError): raise FormError("알림 기준은 숫자여야 합니다.")
    if not 0.5 <= thr <= 3: raise FormError("알림 기준은 0.5~3 사이로 넣어 주세요.")
    prods = raw.get("products")
    if not isinstance(prods, list) or not prods: raise FormError("제품을 하나 이상 넣어 주세요.")
    if len(prods) > 10: raise FormError("제품은 10개까지 넣을 수 있습니다.")
    clean = []
    for p in prods:
        if not isinstance(p, dict): raise FormError("제품 입력 형식이 올바르지 않습니다.")
        item = {"name": _s(p.get("name"), "제품 이름", required=True), "ing": _list(p.get("ing"), "성분·효과 키워드"),
                "cat": _list(p.get("cat"), "카테고리 키워드")}
        comp = _list(p.get("competitors"), "경쟁 제품", 8)
        if comp: item["competitors"] = comp; item["competitor_label"] = _s(p.get("competitor_label"), "경쟁 그룹 이름") or "경쟁 제품"
        clean.append(item)
    if len({p["name"] for p in clean}) != len(clean): raise FormError("같은 제품 이름이 두 번 들어 있습니다.")
    form = {"brand": brand, "threshold": thr, "products": clean, "extra_keywords": _list(raw.get("extra_keywords"), "추가 키워드", 40)}
    cfg, _ = T.build_config(form)
    if len(cfg["keywords"]) > 80: raise FormError(f"키워드가 {len(cfg['keywords'])}개입니다. 80개 이내로 줄여 주세요 (네이버 호출 수 때문입니다).")
    hook = _s(raw.get("webhook"), "슬랙 웹훅 주소", 200)
    if hook and not HOOK_RE.match(hook): raise FormError("슬랙 웹훅 주소가 올바르지 않습니다 (https://hooks.slack.com/services/ 로 시작).")
    return form, hook


def demo_filter(cfg, cols):
    """데모 데이터에 없는 키워드·제품은 뺀다 (남은 게 없으면 FormError)."""
    have = set(cols); dropped = [k for k in cfg["keywords"] if k not in have]
    cfg["keywords"] = {k: v for k, v in cfg["keywords"].items() if k in have}
    cfg["pool_keywords"] = [k for k in cfg["pool_keywords"] if k in have]
    cfg["products"] = {p: c for p, c in cfg["products"].items() if p in have}
    for c in cfg["products"].values():
        c["layers"] = {k: [x for x in v if x in have] for k, v in c["layers"].items()}
    cfg["competitors"] = {p: {**c, "names": [n for n in c["names"] if n in have]} for p, c in cfg["competitors"].items() if p in cfg["products"]}
    cfg["competitors"] = {p: c for p, c in cfg["competitors"].items() if c["names"]}
    if not cfg["products"]: raise FormError("데모 데이터에 있는 제품이 하나도 없습니다. '예시 채우기'의 제품 이름을 써 보세요.")
    return dropped


def _set(slug, **kw):
    st = STATUS.setdefault(slug, {}); st.update(kw)
    p = os.path.join(T.TENANTS, slug, "status.json")
    try: json.dump(st, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    except OSError: pass


def onboard(slug, hook):
    """등록 직후 한 번: 데이터 → 연결 강도 → 계산 → 환영 메시지·첫 알림."""
    tdir = os.path.join(T.TENANTS, slug)
    try:
        with LOCK:
            _set(slug, stage="collect", error=None)
            if DEMO: shutil.copy(DEMO_CSV, os.path.join(tdir, "data", "search_daily.csv"))
            else:
                if not (os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET")):
                    raise RuntimeError("네이버 API 키(NAVER_CLIENT_ID/SECRET)가 서버에 설정되어 있지 않습니다.")
                T._py("collect.py", tdir)
            _set(slug, stage="calibrate")
            weights = json.loads(T._py("tenant.py", tdir, "_calibrate"))
            cfg = json.load(open(os.path.join(tdir, "config.json"), encoding="utf-8"))
            _set(slug, stage="compute")
            T._py("compute.py", tdir)
            latest = json.load(open(os.path.join(tdir, "data", "latest.json"), encoding="utf-8"))
            _set(slug, stage="notify")
            welcome = T.welcome(cfg, weights, latest["asof"])
            sent, slack_error = False, None
            if hook:
                try: T.post(hook, welcome); sent = True
                except Exception as e: slack_error = "슬랙으로 보내지 못했습니다 (주소를 확인해 주세요): " + str(e)[:100]
            alerts = T._py("notify.py", tdir, *([] if sent else ["--dry"]), webhook=hook if sent else "").strip()
        _set(slug, stage="done", done=True, result={"asof": latest["asof"], "weights": weights, "welcome": welcome,
                                                      "alerts": alerts, "sent_to_slack": sent, "slack_error": slack_error})
    except Exception as e:
        _set(slug, stage="error", done=True, error=str(e)[-300:])


def run_all(reason):
    """등록된 모든 기업을 한 번 돌린다 (월요일이면 주간 브리핑 포함). 매일 자동 실행과 [지금 실행]이 같이 쓴다."""
    if LAST_RUN["running"]: return False
    LAST_RUN.update(running=True, log="")
    lines = []
    def _log(*a): lines.append(" ".join(str(x) for x in a))
    T.LOG = _log
    fail = None
    try:
        with LOCK:
            now = dt.datetime.now(KST)
            fail = T.run(dry=False, weekly=(now.weekday() == 0), skip_collect=DEMO)
    except Exception as e:
        lines.append("실행 오류: " + str(e)[-300:]); fail = -1
    finally:
        T.LOG = print
        LAST_RUN.update(running=False, at=dt.datetime.now(KST).isoformat(timespec="seconds"), failures=fail,
                        log=(f"({reason})\n" + "\n".join(lines))[-3000:])
    return True


def scheduler():
    hh, mm = map(int, DAILY_AT.split(":"))
    now = dt.datetime.now(KST)
    last = now.date() if (now.hour, now.minute) >= (hh, mm) else None     # 늦게 켰으면 오늘 몫은 건너뛴다
    while True:
        now = dt.datetime.now(KST)
        if last != now.date() and (now.hour, now.minute) >= (hh, mm):
            last = now.date(); run_all("매일 자동 실행")
        time.sleep(30)


def rate_ok(ip):
    now = time.time(); hist = [t for t in RATE.get(ip, []) if now - t < RATE_WINDOW]
    if len(hist) >= RATE_MAX: RATE[ip] = hist; return False
    hist.append(now); RATE[ip] = hist
    return True


def register(raw):
    form, hook = parse_form(raw)
    cfg, warn = T.build_config(form)
    slug = T.slugify(form["brand"])
    tdir = os.path.join(T.TENANTS, slug)
    if os.path.exists(os.path.join(tdir, "config.json")): raise FormError(f"'{form['brand']}'은(는) 이미 등록되어 있습니다.")
    dropped = demo_filter(cfg, __import__("pandas").read_csv(DEMO_CSV, nrows=1, index_col=0).columns) if DEMO else []
    if len(cfg["pool_keywords"]) < T.MIN_POOL:
        raise FormError(f"비교 키워드가 {len(cfg['pool_keywords'])}개뿐입니다. 시장 공통 변화를 걷어내려면 {T.MIN_POOL}개 이상이 필요합니다 (성분·카테고리·추가 키워드를 더 넣어 주세요)"
                        + (" (데모 데이터에 없는 키워드는 제외됩니다)" if DEMO else "") + ".")
    os.makedirs(os.path.join(tdir, "data"), exist_ok=True)
    json.dump(cfg, open(os.path.join(tdir, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if hook:
        hooks = json.load(open(T.HOOK_FILE, encoding="utf-8")) if os.path.exists(T.HOOK_FILE) else {}
        hooks[slug] = hook
        json.dump(hooks, open(T.HOOK_FILE, "w", encoding="utf-8"), ensure_ascii=False)
        try: os.chmod(T.HOOK_FILE, 0o600)
        except OSError: pass
    _set(slug, brand=form["brand"], created=dt.datetime.now().isoformat(timespec="seconds"), stage="queued", done=False, error=None, result=None)
    threading.Thread(target=onboard, args=(slug, hook), daemon=True).start()
    return {"slug": slug, "keywords": len(cfg["keywords"]), "products": len(cfg["products"]), "warnings": warn,
            "demo_dropped": dropped, "webhook": bool(hook)}


def list_tenants():
    out = []
    for d in sorted(os.listdir(T.TENANTS)) if os.path.isdir(T.TENANTS) else []:
        if d.startswith((".", "_")) or not os.path.exists(os.path.join(T.TENANTS, d, "config.json")): continue
        st = STATUS.get(d)
        if st is None:
            try: st = json.load(open(os.path.join(T.TENANTS, d, "status.json"), encoding="utf-8"))
            except Exception: st = {"stage": "done", "done": True}
        cfg = json.load(open(os.path.join(T.TENANTS, d, "config.json"), encoding="utf-8"))
        out.append({"slug": d, "brand": cfg.get("brand", d), "products": len(cfg["products"]), "stage": st.get("stage"),
                    "done": st.get("done"), "created": st.get("created")})
    return out


class H(BaseHTTPRequestHandler):
    server_version = "AlertRegister/1.1"

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

    def _ok(self):
        """접속 코드가 설정돼 있으면 헤더가 맞아야 통과 (시간 차이로 추측하지 못하게 hmac 비교)."""
        if not ACCESS_CODE: return True
        return hmac.compare_digest(self.headers.get("X-Access-Code", "").encode(), ACCESS_CODE.encode())

    def _ip(self):
        f = self.headers.get("X-Forwarded-For", "")
        return f.split(",")[0].strip() if f else self.client_address[0]

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/onboard.html"):
            b = open(PAGE, "rb").read()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        elif u.path == "/api/ping":
            info = {"ok": True, "demo": DEMO, "keys": bool(os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET")),
                    "needs_code": bool(ACCESS_CODE), "authed": self._ok()}
            if info["authed"]: info.update(daily_at=DAILY_AT, last_run=LAST_RUN)
            self._json(200, info)
        elif not self._ok(): self._json(401, {"error": "접속 코드가 맞지 않습니다."})
        elif u.path == "/api/tenants": self._json(200, {"tenants": list_tenants()})
        elif u.path == "/api/status":
            slug = (parse_qs(u.query).get("slug") or [""])[0]
            if not re.fullmatch(r"[0-9A-Za-z가-힣-]+", slug): return self._json(404, {"error": "등록된 기업이 아닙니다."})
            st = STATUS.get(slug) or next((t for t in list_tenants() if t["slug"] == slug), None)
            if not st: return self._json(404, {"error": "등록된 기업이 아닙니다."})
            self._json(200, {**st, "steps": [{"id": i, "label": l} for i, l in STAGES]})
        else: self._json(404, {"error": "없는 주소입니다."})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/register", "/api/run-now"): return self._json(404, {"error": "없는 주소입니다."})
        if not self._ok(): return self._json(401, {"error": "접속 코드가 맞지 않습니다."})
        if path == "/api/run-now":
            if LAST_RUN["running"]: return self._json(409, {"error": "이미 실행 중입니다."})
            threading.Thread(target=run_all, args=("지금 실행",), daemon=True).start()
            return self._json(200, {"started": True})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY: return self._json(413, {"error": "입력이 너무 큽니다."})
        if not rate_ok(self._ip()): return self._json(429, {"error": f"등록 요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요 (IP당 시간당 {RATE_MAX}회)."})
        try: raw = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception: return self._json(400, {"error": "입력을 읽을 수 없습니다."})
        try: self._json(200, register(raw))
        except FormError as e: self._json(400, {"error": str(e)})
        except Exception as e:
            print("등록 실패:", repr(e)); self._json(500, {"error": "서버 오류가 났습니다. 잠시 뒤 다시 시도해 주세요."})

    def log_message(self, fmt, *a): print(f"{self.address_string()} {fmt % a}")


if __name__ == "__main__":
    a = sys.argv[1:]
    DEMO = "--demo" in a
    host = a[a.index("--host") + 1] if "--host" in a else ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    port = int(a[a.index("--port") + 1]) if "--port" in a else int(os.environ.get("PORT", 8000))
    if host not in ("127.0.0.1", "localhost") and not ACCESS_CODE:
        sys.exit("밖에서 접속할 수 있게 열려면 접속 코드가 필요합니다. 환경변수 ACCESS_CODE 를 정해 주세요. (예: ACCESS_CODE=내비밀 python scripts/server.py --host 0.0.0.0)")
    if "--daily-at" in a: DAILY_AT = a[a.index("--daily-at") + 1]
    elif not DEMO and "--no-schedule" not in a: DAILY_AT = "07:30"
    if DAILY_AT and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", DAILY_AT): sys.exit("--daily-at 은 HH:MM 형식이어야 합니다 (예: 07:30).")
    os.makedirs(T.TENANTS, exist_ok=True)
    if DAILY_AT: threading.Thread(target=scheduler, daemon=True).start()
    print(f"기업 등록 서버: http://{host}:{port}  ({'데모 데이터' if DEMO else '네이버 실제 수집'})"
          + (f" · 접속 코드 사용" if ACCESS_CODE else "") + (f" · 매일 {DAILY_AT}(한국 시간) 자동 실행" if DAILY_AT else ""))
    ThreadingHTTPServer((host, port), H).serve_forever()
