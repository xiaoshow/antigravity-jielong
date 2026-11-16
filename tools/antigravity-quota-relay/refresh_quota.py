"""无界面刷新接龙额度缓存：python3 refresh_quota.py [槽位号...]，不带参数刷全部。在隔离 HOME 里借各号凭据查 /usage，不动真实登录状态，且物理阻断一切浏览器弹窗。"""
import os, sys, json, base64, subprocess, time, datetime, socket

V = os.path.expanduser("~/.gemini/account_vault")
CACHE = os.path.join(V, "multi_quota_cache.json")
PH = os.path.join(V, ".probe_home")
PC = os.path.join(PH, ".gemini", "antigravity-cli")
NO_BROWSER_BIN = os.path.join(V, ".no_browser_bin")
BLOCKED_URL_FILE = os.path.join(V, ".blocked_open_url")

os.makedirs(PC, mode=0o700, exist_ok=True)
os.makedirs(NO_BROWSER_BIN, mode=0o700, exist_ok=True)

# 物理级阻断 macOS /usr/bin/open 与 osascript：agy 在 token 失效时会直接调 PATH 里的 open 弹浏览器（忽略 BROWSER 环境变量）
for _cmd, _body in (
    ("open", f'#!/bin/sh\nprintf "%s\\n" "$*" > "{BLOCKED_URL_FILE}"\nexit 0\n'),
    ("osascript", "#!/bin/sh\nexit 0\n"),
    ("xdg-open", "#!/bin/sh\nexit 0\n"),
):
    _p = os.path.join(NO_BROWSER_BIN, _cmd)
    if not os.path.exists(_p) or (_cmd == "open" and BLOCKED_URL_FILE not in open(_p, encoding="utf-8").read()):
        with open(_p, "w", encoding="utf-8") as _wf:
            _wf.write(_body)
        os.chmod(_p, 0o755)

slots = sys.argv[1:] or [str(i) for i in range(1, 41)]
if os.path.exists(os.path.join(V, ".login_lock")) or os.path.exists(os.path.join(V, ".rotating")):
    sys.exit("有登录/轮换锁，退出")

# NO_BROWSER_BIN 必须排在 PATH 最前面，压制 /usr/bin/open
env = dict(os.environ, HOME=PH, BROWSER="/usr/bin/true", DESKTOP_SESSION="", DISPLAY="")
env["PATH"] = f"{NO_BROWSER_BIN}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/Users/weinliu/.local/bin"


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


if _port_open(10808):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[k] = "http://127.0.0.1:10808"

kc = os.path.join(PH, "Library", "Keychains", "login.keychain-db")
if not os.path.exists(kc):
    os.makedirs(os.path.dirname(kc), mode=0o700, exist_ok=True)
    subprocess.run(["security", "create-keychain", "-p", "", kc], env=env, check=False, stderr=subprocess.DEVNULL)
    subprocess.run(["security", "set-keychain-settings", kc], env=env, check=False, stderr=subprocess.DEVNULL)

try:
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
except Exception:
    cache = {}

for s in slots:
    tp = os.path.join(V, f"acc{s}.token")
    if not os.path.exists(tp):
        continue
    tok = open(tp).read().strip()
    if not tok:
        continue
    dec = base64.b64decode(tok.replace("go-keyring-base64:", "")).decode()
    for n in ("antigravity-oauth-token", "jetski-standalone-oauth-token"):
        p_tok = os.path.join(PC, n)
        with open(p_tok, "w", encoding="utf-8") as wf:
            wf.write(dec)
        os.chmod(p_tok, 0o600)

    subprocess.run(["security", "unlock-keychain", "-p", "", kc], env=env, check=False, stderr=subprocess.DEVNULL)
    subprocess.run(["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", tok, kc],
                   env=env, check=False, stderr=subprocess.DEVNULL)

    try:
        os.remove(BLOCKED_URL_FILE)
    except OSError:
        pass

    t = time.time()
    proc = subprocess.Popen(
        ["/Users/weinliu/.local/bin/agy", "--output-format", "json", "-p", "/usage"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    blocked_login = False
    timed_out = False
    while proc.poll() is None:
        if os.path.exists(BLOCKED_URL_FILE):
            blocked_login = True
            proc.kill()
            break
        if time.time() - t > 20:
            timed_out = True
            proc.kill()
            break
        time.sleep(0.25)

    out_str, err_str = proc.communicate()

    if blocked_login:
        ef = os.path.join(V, f".auth_expired_{s}")
        try:
            u = open(BLOCKED_URL_FILE, encoding="utf-8").read().strip()
        except Exception:
            u = "oauth_expired"
        with open(ef, "w", encoding="utf-8") as wf:
            wf.write(u)
        os.chmod(ef, 0o600)
        try:
            os.remove(BLOCKED_URL_FILE)
        except OSError:
            pass
        print(f"{s} 登录已失效 AUTH_EXPIRED（已物理拦截弹窗并隔离）", flush=True)
        continue

    if timed_out:
        print(s, "超时", flush=True)
        continue

    if proc.returncode != 0:
        msg = (err_str or out_str or "")
        if "not eligible" in msg:
            import re as _re
            m = _re.search(r"https://accounts\.google\.com\S+", msg)
            vf = os.path.join(V, f".verify_url_{s}")
            if m:
                open(vf, "w").write(m.group(0).replace("\\n", "").rstrip('"}'))
                os.chmod(vf, 0o600)
            print(f"{s} 未通过资格验证 NOT_ELIGIBLE {vf if m else ''}", flush=True)
        else:
            print(s, "失败 rc", proc.returncode, msg.strip()[-200:].replace("\n", " "), flush=True)
        continue

    try:
        groups = json.loads(out_str).get("command", {}).get("data", {}).get("groups", [])
    except Exception:
        print(s, "解析失败", out_str[:200], flush=True)
        continue

    if not groups:
        print(s, "无额度数据", out_str[:200].replace("\n", " "), flush=True)
        continue

    # 成功拉到额度则清理旧异常标记
    for old_flag in (os.path.join(V, f".verify_url_{s}"), os.path.join(V, f".auth_expired_{s}")):
        if os.path.exists(old_flag):
            try:
                os.remove(old_flag)
            except OSError:
                pass

    d = {k: (None if k.endswith("pct") else "") for k in (
        "c5h_pct", "c5h_reset", "cwk_pct", "cwk_reset",
        "g5h_pct", "g5h_reset", "gwk_pct", "gwk_reset",
        "argon_pct", "argon_reset"
    )}
    for g in groups:
        gn = g.get("name", "").lower()
        for b in g.get("buckets", []):
            bid = b.get("id", "").lower()
            dis = b.get("disabled", False)
            fr = b.get("remaining_fraction", 0.0) * 100.0
            rt = b.get("reset_time", "")
            # 满额（100%）时接口返回的是浮动滑窗伪时间（now + 7d / now + 5h），并非真实复位倒计时，直接置空防误导
            if not dis and fr >= 99.95:
                rt = ""
            if "argon" in bid or "argon" in gn:
                d["argon_pct"], d["argon_reset"] = fr, rt
            elif "3p" in bid or "claude" in gn:
                if "5h" in bid or "five" in bid:
                    d["c5h_pct"], d["c5h_reset"] = (0.0, "DISABLED") if dis else (fr, rt)
                else:
                    d["cwk_pct"], d["cwk_reset"] = fr, rt
            elif "gemini" in gn:
                if "5h" in bid or "five" in bid:
                    d["g5h_pct"], d["g5h_reset"] = fr, rt
                else:
                    d["gwk_pct"], d["gwk_reset"] = fr, rt

    if d["cwk_pct"] is not None and d["cwk_pct"] <= 0.01:
        d["c5h_pct"], d["c5h_reset"] = 0.0, "DISABLED"

    d["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        latest_cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    except Exception:
        latest_cache = dict(cache)
    old = latest_cache.get(s, {})
    latest_cache[s] = d
    latest_cache["_meta"] = {
        "updated_at": d["updated_at"],
        "last_slot": s
    }
    cache = latest_cache

    f = lambda x: "—" if x is None else f"{x:.0f}%"
    print(f"{s}号 {time.time()-t:.0f}s | Gemini周 {f(old.get('gwk_pct'))}→{f(d['gwk_pct'])} 5h {f(old.get('g5h_pct'))}→{f(d['g5h_pct'])} | Claude周 {f(old.get('cwk_pct'))}→{f(d['cwk_pct'])} 5h {f(old.get('c5h_pct'))}→{f(d['c5h_pct'])} | Argon {f(old.get('argon_pct'))}→{f(d['argon_pct'])}", flush=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as wf:
        json.dump(latest_cache, wf, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CACHE)
