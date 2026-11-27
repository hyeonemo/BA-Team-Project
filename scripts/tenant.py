"""기업(테넌트)별 자동 운영.

입력 폼(JSON) -> tenants/<기업>/config.json -> 과거 데이터 받기 -> 연결 강도 계산 -> 매일 계산·발송.

    python scripts/tenant.py add 폼.json           # 폼 내용으로 기업 폴더와 config.json 만들기
    python scripts/tenant.py run [--dry] [--weekly] [--only 기업] [--skip-collect]
                                                    # 모든 기업을 차례로 돌린다 (처음이면 과거 데이터 받기부터)

API 키는 운영자(우리) 것 하나를 모든 기업이 같이 쓴다: 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET.
슬랙 주소는 저장소에 올리면 안 되므로 환경변수 TENANT_WEBHOOKS = {"기업": "https://hooks.slack.com/..."} 로 받는다.
기업마다 폴더가 따로라서(설정, 데이터, 보낸 알림 기록) 서로 섞이지 않는다.
"""
import os, re, sys, json, hashlib, datetime as dt, subprocess, traceback
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TENANTS = os.path.join(REPO, "tenants")
BASE = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))   # 공통 설정(창 길이, API 주소 등)
MIN_POOL = 8          # 시장 공통 변화를 빼려면 비교 키워드가 이 정도는 있어야 한다


def slugify(name):
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "-", name).strip("-")
    return s or hashlib.sha1(name.encode()).hexdigest()[:6]


def _uniq(xs):
    out = []
    for x in xs:
        x = (x or "").strip()
        if x and x not in out: out.append(x)
    return out


def build_config(form):
    """폼 입력 -> 시스템이 쓰는 config.json 내용. (연결 강도는 과거 데이터를 받은 뒤 calibrate 에서 채운다.)"""
    brand = form["brand"].strip()
    warn, products, comps, link_kw = [], {}, {}, []
    for p in form["products"]:
        name = p["name"].strip()
        ing = _uniq(p.get("ing", [])); cat = _uniq(p.get("cat", []))
        products[name] = {"mode": "score", "weights": {}, "layers": {"ing": ing, "cat": cat}}
        link_kw += ing + cat
        names = _uniq(p.get("competitors", []))
        if names: comps[name] = {"label": p.get("competitor_label") or "경쟁 제품", "names": names}
    pool = _uniq(link_kw + list(products) + form.get("extra_keywords", []))
    if len(pool) < MIN_POOL:
        warn.append(f"비교 키워드가 {len(pool)}개뿐이라 시장 공통 변화를 정확히 빼기 어렵다 (권장 {MIN_POOL}개 이상: 같은 시장의 성분·카테고리 키워드를 더 넣어 주세요)")
    allkw = _uniq([brand] + pool + [n for c in comps.values() for n in c["names"]])
    cfg = {k: BASE[k] for k in ("start_date", "api_base", "window_recent_days", "window_base_days", "history_days", "event_end_days")}
    cfg.update({
        "brand": brand,
        "threshold": float(form.get("threshold", BASE["threshold"])),
        "keywords": {k: [k] for k in allkw},
        "pool_keywords": pool,
        "products": products,
        "reference": [brand],
        "competitors": comps,
    })
    return cfg, warn


def add(form_path):
    form = json.load(open(form_path, encoding="utf-8"))
    cfg, warn = build_config(form)
    slug = slugify(form["brand"]); tdir = os.path.join(TENANTS, slug)
    os.makedirs(os.path.join(tdir, "data"), exist_ok=True)
    json.dump(cfg, open(os.path.join(tdir, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"기업 등록: {slug} · 키워드 {len(cfg['keywords'])}개 (첫 실행 때 API {len(cfg['keywords'])}번 호출) · 제품 {len(cfg['products'])}개")
    for w in warn: print("주의:", w)
    return slug


def _env(tdir, webhook=""):
    e = dict(os.environ); e["BA_ROOT"] = tdir; e["SLACK_WEBHOOK_URL"] = webhook or ""
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def _py(script, tdir, *args, webhook=""):
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", script), *args], env=_env(tdir, webhook),
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0: raise RuntimeError(f"{script} 실패: {(r.stderr or r.stdout)[-400:]}")
    return r.stdout


def calibrate(tdir):
    """연결 강도 = 연결 키워드(공통 변화를 뺀 z)와 제품 자체 검색 z 의 상관계수. 0.1 미만이면 연결 없음으로 본다."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import compute as C
    d = C.load(); Z, Zc, mkt, _ = C.build(d)
    path = os.path.join(tdir, "config.json"); cfg = json.load(open(path, encoding="utf-8"))
    for p, c in cfg["products"].items():
        w = {}
        for k in (c["layers"].get("ing") or []) + (c["layers"].get("cat") or []):
            if k in Zc.columns and p in Z.columns:
                r = Zc[k].corr(Z[p])
                if r == r and r >= 0.1: w[k] = round(float(r), 2)
        c["weights"] = w
    cfg["calibrated"] = dt.date.today().isoformat()
    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {p: c["weights"] for p, c in cfg["products"].items()}


def post(url, text):
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=20).status


def welcome(cfg, weights, asof):
    lines = [f"*[연결 완료] {cfg['brand']}* · 기준일 {asof}",
             f"• 키워드 {len(cfg['keywords'])}개의 과거 검색 흐름을 받아 기준선을 만들었습니다.",
             f"• 알림 기준: 평소 폭의 {cfg['threshold']:.1f}배 이상 · 기준 아래로 {cfg['event_end_days']}일 연속 내려가면 종료"]
    for p, w in weights.items():
        lines.append(f"• {p}: " + (", ".join(f"{k} {v:.2f}" for k, v in w.items()) + " 와 함께 움직이는 정도를 반영" if w else "연결된 키워드가 없어 제품 자체 검색만 봅니다"))
    lines.append("• 이제 매일 아침 급등이 시작·확대·완화·종료될 때 이 채널로 알려드립니다.")
    return "\n".join(lines)


def run_tenant(slug, hooks, dry, weekly, skip_collect):
    tdir = os.path.join(TENANTS, slug); cfg = json.load(open(os.path.join(tdir, "config.json"), encoding="utf-8"))
    hook = "" if dry else hooks.get(slug, "")
    first = not os.path.exists(os.path.join(tdir, "data", "search_daily.csv")) or not cfg.get("calibrated")
    if not skip_collect: _py("collect.py", tdir)                 # 첫 실행이면 과거 전체를 받는 것과 같다(매번 전체 구간)
    weights = None
    if first:
        weights = json.loads(_py("tenant.py", tdir, "_calibrate")); cfg = json.load(open(os.path.join(tdir, "config.json"), encoding="utf-8"))
    _py("compute.py", tdir)
    latest = json.load(open(os.path.join(tdir, "data", "latest.json"), encoding="utf-8"))
    if first:
        msg = welcome(cfg, weights, latest["asof"])
        if hook: print(f"[{slug}] 환영 메시지 발송", post(hook, msg))
        else: print(f"[{slug}] --- 드라이런(환영) ---\n{msg}\n")
    out = _py("notify.py", tdir, *(["--weekly"] if weekly else []), *(["--dry"] if (dry or not hook) else []), webhook=hook)
    print(f"[{slug}] " + out.strip().replace("\n", "\n    "))


HOOK_FILE = os.path.join(TENANTS, ".webhooks.json")      # 웹 서버가 받은 슬랙 주소 (git 에는 올리지 않는다)


def load_hooks():
    """슬랙 주소: 로컬 파일(웹 서버 등록분) + 환경변수 TENANT_WEBHOOKS(깃허브 시크릿). 같은 기업이면 환경변수가 우선."""
    hooks = {}
    if os.path.exists(HOOK_FILE): hooks.update(json.load(open(HOOK_FILE, encoding="utf-8")))
    hooks.update(json.loads(os.environ.get("TENANT_WEBHOOKS") or "{}"))
    return hooks


def run(dry=False, weekly=False, only=None, skip_collect=False):
    hooks = load_hooks()
    slugs = sorted(d for d in (os.listdir(TENANTS) if os.path.isdir(TENANTS) else [])
                   if os.path.exists(os.path.join(TENANTS, d, "config.json")) and (not only or d == only))
    print(f"기업 {len(slugs)}곳 실행")
    fail = 0
    for s in slugs:
        try: run_tenant(s, hooks, dry, weekly, skip_collect)
        except Exception:
            fail += 1; print(f"[{s}] 실패(다른 기업은 계속):"); traceback.print_exc()
    return fail


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "_calibrate": print(json.dumps(calibrate(os.environ["BA_ROOT"]), ensure_ascii=False))   # 내부용: BA_ROOT 폴더 기준
    elif a and a[0] == "add": add(a[1])
    elif a and a[0] == "run":
        only = a[a.index("--only") + 1] if "--only" in a else None
        sys.exit(1 if run("--dry" in a, "--weekly" in a, only, "--skip-collect" in a) else 0)
    else: print(__doc__)
