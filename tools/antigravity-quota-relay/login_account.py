#!/usr/bin/env python3
"""
Interactive Account Adder & Token Importer for Antigravity 接龙 (Jielong)
用法:
    python3 login_account.py [槽位编号, 如 3, 4, 5]
"""
import sys, os, time, subprocess, json, base64, urllib.request, urllib.parse, shutil, signal

VAULT_DIR = os.path.expanduser("~/.gemini/account_vault")
BIN_DIR = os.path.join(VAULT_DIR, ".bin")
LOCK_FILE = os.path.join(VAULT_DIR, ".login_lock")
CAPTURED_URL_FILE = os.path.join(VAULT_DIR, ".captured_url")
os.makedirs(VAULT_DIR, exist_ok=True)
os.makedirs(BIN_DIR, exist_ok=True)

# 确保 open 拦截器
open_wrapper = os.path.join(BIN_DIR, "open")
with open(open_wrapper, "w") as f:
    f.write('''#!/usr/bin/env bash
TARGET_URL=""
for arg in "$@"; do
    if [[ "$arg" == *"accounts.google.com"* ]]; then
        TARGET_URL="$arg"
        break
    fi
done

if [[ -n "$TARGET_URL" ]]; then
    if [[ "$TARGET_URL" != *"prompt="* ]]; then
        if [[ "$TARGET_URL" == *"?"* ]]; then
            TARGET_URL="${TARGET_URL}&prompt=select_account"
        else
            TARGET_URL="${TARGET_URL}?prompt=select_account"
        fi
    fi
    echo "$TARGET_URL" > ''' + f'"{CAPTURED_URL_FILE}"' + '''
    if [[ "$LOGIN_MODE" == "auto" ]]; then
        /usr/bin/open "$TARGET_URL"
    fi
    exit 0
else
    /usr/bin/open "$@"
fi
''')
os.chmod(open_wrapper, 0o755)

def next_free_slot():
    """不带参数时自动用第一个空槽位（accN.token 不存在的最小 N），不再默认写 4 号覆盖已有账号"""
    n = 1
    while os.path.exists(os.path.join(VAULT_DIR, f"acc{n}.token")):
        n += 1
    return str(n)

slot = sys.argv[1] if len(sys.argv) >= 2 else next_free_slot()

# 本机直连 Google 不通：本地代理端口开着且没设代理时，自动走它
import socket as _socket
try:
    _socket.create_connection(("127.0.0.1", 10808), timeout=0.5).close()
    for _k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        os.environ.setdefault(_k, "http://127.0.0.1:10808")
except OSError:
    pass
target_path = os.path.join(VAULT_DIR, f"acc{slot}.token")

print("==================================================", flush=True)
print(f"👉 准备绑定/更新【{slot}号账号】", flush=True)
print("==================================================", flush=True)

def extract_email(token_str):
    try:
        raw = token_str.strip().replace("go-keyring-base64:", "")
        data = json.loads(base64.b64decode(raw))
        if data.get("id_token_email"):
            return data["id_token_email"].lower()
        if data.get("id_token"):
            parts = data["id_token"].split(".")
            if len(parts) >= 2:
                pad = parts[1] + "=" * (-len(parts[1]) % 4)
                email = json.loads(base64.urlsafe_b64decode(pad)).get("email", "").lower()
                if email:
                    return email
        acc_tok = data.get("token", {}).get("access_token", "")
        if acc_tok:
            req = urllib.request.Request(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {acc_tok}"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode()).get("email", "").lower()
    except Exception:
        pass
    return ""

def format_token_blob(raw_str):
    raw_str = raw_str.strip()
    if not raw_str:
        return None
    if raw_str.startswith("go-keyring-base64:"):
        return raw_str
    try:
        decoded = base64.b64decode(raw_str).decode('utf-8')
        json.loads(decoded)
        return "go-keyring-base64:" + raw_str
    except Exception:
        pass
    try:
        json.loads(raw_str)
        b64 = base64.b64encode(raw_str.encode('utf-8')).decode('utf-8')
        return "go-keyring-base64:" + b64
    except Exception:
        pass
    return None

existing_emails = {}
for s in [str(i) for i in range(1, 13)]:
    if s == str(slot):
        continue
    sp = os.path.join(VAULT_DIR, f"acc{s}.token")
    if os.path.exists(sp):
        try:
            with open(sp, "r", encoding="utf-8") as f:
                em = extract_email(f.read())
                if em:
                    existing_emails[s] = em
        except Exception:
            pass

import hashlib
def refresh_fp(blob):
    try:
        d = json.loads(base64.b64decode(blob.strip().replace("go-keyring-base64:", "")))
        rt = d.get("token", {}).get("refresh_token", "") or d.get("refresh_token", "")
        return hashlib.sha1(rt.encode()).hexdigest() if rt else ""
    except Exception:
        return ""

known_fps = set()
for _s in [str(i) for i in range(1, 13)]:
    _p = os.path.join(VAULT_DIR, f"acc{_s}.token")
    if os.path.exists(_p):
        try:
            with open(_p, "r", encoding="utf-8") as _f:
                _fp = refresh_fp(_f.read())
            if _fp:
                known_fps.add(_fp)
        except Exception:
            pass

DEBUG_LOG = os.path.join(VAULT_DIR, "login_debug.log")
def dbg(msg):
    try:
        with open(DEBUG_LOG, "a") as _f:
            _f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass

def find_new_credential():
    """钥匙串 / CLI 令牌文件里只要出现一个保险库里没有的账号凭据，就是刚授权成功的"""
    cands = []
    try:
        kc = subprocess.check_output(["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                                     stderr=subprocess.DEVNULL).decode("utf-8", "ignore").strip()
        if kc:
            cands.append(("keychain", kc))
    except Exception as e:
        dbg(f"keychain read failed: {type(e).__name__}")
    for _name, _path in (("standalone", os.path.expanduser("~/.gemini/jetski-standalone-oauth-token")),
                         ("cli", os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token"))):
        try:
            if os.path.exists(_path):
                with open(_path, "r", encoding="utf-8") as _f:
                    _d = json.load(_f)
                if _d.get("token") or _d.get("id_token_email"):
                    cands.append((_name, "go-keyring-base64:" + base64.b64encode(json.dumps(_d).encode("utf-8")).decode("utf-8")))
        except Exception as e:
            dbg(f"{_name} read failed: {type(e).__name__}")
    for _name, _blob in cands:
        _fp = refresh_fp(_blob)
        dbg(f"candidate {_name}: fp={_fp[:8] if _fp else None} known={_fp in known_fps}")
        if _fp and _fp not in known_fps:
            return _blob
    return None

def copy_to_clipboard(text):
    """把链接放进系统剪贴板，省得手动选中复制；失败不影响流程"""
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True, timeout=5)
        return True
    except Exception:
        return False

clipboard_token = None
try:
    cb = subprocess.check_output(["pbpaste"]).decode("utf-8", errors="ignore").strip()
    formatted = format_token_blob(cb)
    if formatted and len(cb) > 80:
        e = extract_email(formatted)
        if e:
            clipboard_token = (formatted, e)
except Exception:
    pass

token_to_save = None

if clipboard_token:
    print(f"📋 检测到剪贴板中已有有效的账号 Token！", flush=True)
    print(f"   账号邮箱: [{clipboard_token[1]}]", flush=True)
    ans = input(f"👉 是否直接将此剪贴板 Token 绑定为【{slot}号】？(Y/n): ").strip().lower()
    if ans in ("", "y", "yes"):
        token_to_save = clipboard_token[0]

if not token_to_save:
    print("", flush=True)
    print("请选择绑定方式：", flush=True)
    print("  [1] 浏览器自动打开（保留已有登录态，弹出账号选择列表，鼠标点击账号）", flush=True)
    print("  [2] 获取授权链接（手动复制链接，粘贴到你指定的浏览器/分身窗口打开）", flush=True)
    print("  [3] 手动粘贴 Token 字符串（高级备用）", flush=True)
    choice = input("请选择 (默认 2): ").strip()
    if choice not in ("1", "2", "3"):
        choice = "2"

    if choice == "3":
        user_input = input("👉 请粘贴你的 Token 字符串后按回车: ").strip()
        formatted = format_token_blob(user_input)
        if formatted:
            token_to_save = formatted
        else:
            print("❌ 输入的 Token 格式不正确，操作已取消。", flush=True)
            sys.exit(1)
    else:
        # 上锁，防止后台多账号 HUD 轮询并发冲突钥匙串
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        # HUD 正在轮换凭据时，钥匙串里是借用的别的号：等这一轮结束再备份「当前登录状态」
        _rot = os.path.join(VAULT_DIR, ".rotating")
        for _ in range(120):
            if not os.path.exists(_rot):
                break
            time.sleep(1)

        if os.path.exists(CAPTURED_URL_FILE):
            try: os.remove(CAPTURED_URL_FILE)
            except Exception: pass

        # 本地 Token 缓存文件路径（彻底移开以触发全新授权）
        f1 = os.path.expanduser("~/.gemini/jetski-standalone-oauth-token")
        f2 = os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token")
        b1 = f1 + ".bak_login"
        b2 = f2 + ".bak_login"

        if os.path.exists(f1): shutil.move(f1, b1)
        if os.path.exists(f2): shutil.move(f2, b2)

        # 备份钥匙串中当前活动的 Token
        cur_token = None
        try:
            cur_token = subprocess.check_output(
                ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            pass

        # 彻底清空钥匙串中的旧凭证以触发官方 OAuth 流程
        while True:
            r = subprocess.run(
                ["security", "delete-generic-password", "-s", "gemini", "-a", "antigravity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if r.returncode != 0:
                break

        def restore_env(sig=None, frame=None):
            try:
                _act = open(os.path.join(VAULT_DIR, ".active_slot")).read().strip()
                _ap = os.path.join(VAULT_DIR, f"acc{_act}.token")
                if _act and os.path.exists(_ap) and _act != str(slot):
                    with open(_ap) as _f:
                        cur_token_slot = _f.read().strip()
                    if cur_token_slot:
                        globals()["cur_token"] = cur_token_slot
            except Exception:
                pass
            if cur_token:
                subprocess.run(
                    ["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", cur_token],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            if os.path.exists(b1): shutil.move(b1, f1)
            if os.path.exists(b2): shutil.move(b2, f2)
            try:
                if os.path.exists(LOCK_FILE) and open(LOCK_FILE).read().strip() == str(os.getpid()):
                    os.remove(LOCK_FILE)
            except Exception:
                pass
            if os.path.exists(CAPTURED_URL_FILE):
                try: os.remove(CAPTURED_URL_FILE)
                except Exception: pass
            if sig is not None:
                sys.exit(0)

        signal.signal(signal.SIGINT, restore_env)
        signal.signal(signal.SIGTERM, restore_env)

        try:
            env = dict(os.environ)
            env["PATH"] = f"{BIN_DIR}:{env.get('PATH', '')}"
            env["LOGIN_MODE"] = "manual" if choice == "2" else "auto"

            print("", flush=True)
            print("🚀 正在向 Google 申请官方授权链接...", flush=True)

            _used_codes = set()   # 已提交过的 code：Google 的 code 只能用一次，重复粘贴必然 invalid_grant
            for attempt in range(1 if choice == "1" else 8):
                if attempt > 0:
                    token_to_save = find_new_credential()
                    if token_to_save:
                        print("✅ 上一次授权其实已成功，直接使用这份凭据。", flush=True)
                        break
                if os.path.exists(CAPTURED_URL_FILE):
                    try: os.remove(CAPTURED_URL_FILE)
                    except Exception: pass
                # 调起 agy，输出完全重定向，防止控制台打印冗余的 Quota
                proc = subprocess.Popen(
                    ["/Users/weinliu/.local/bin/agy", "-p", "/usage"],
                    env=env,
                    stdout=open(os.path.join(VAULT_DIR, "login_agy.log"), "w"),
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE
                )

                # 等待拦截器截获 Google 官方授权 URL
                auth_url = ""
                for _ in range(30):
                    if os.path.exists(CAPTURED_URL_FILE):
                        try:
                            with open(CAPTURED_URL_FILE, "r") as uf:
                                auth_url = uf.read().strip()
                            if auth_url:
                                break
                        except Exception:
                            pass
                    time.sleep(0.3)

                if not auth_url:
                    print("❌ 未能获取到授权链接，请检查网络或重试。", flush=True)
                    proc.kill()
                    sys.exit(1)

                print("", flush=True)
                print("======================================================================", flush=True)
                print("🔗 Google 官方授权链接已生成：", flush=True)
                print(auth_url, flush=True)
                print("======================================================================", flush=True)
                print("", flush=True)

                if choice == "1":
                    print("🌐 [自动模式] 浏览器已自动调起（弹出账号列表供点选）...", flush=True)
                    print("👉 请在浏览器中点击你要绑定的账号，并确认授权！", flush=True)
                else:
                    if copy_to_clipboard(auth_url):
                        print("📋 [手动模式] 链接已复制到剪贴板，直接粘贴到你已登录该账号的浏览器窗口打开！", flush=True)
                    else:
                        print("📋 [手动模式] 请复制上方链接，粘贴到你已登录该账号的任意浏览器窗口打开！", flush=True)
                    print("👉 在浏览器授权后会显示一串 code（4/0A 开头），本终端只等你把它粘贴进来，不会自动接收。", flush=True)

                if choice == "2":
                    print("💡 链接 60 秒内有效：授权后复制那串 code，粘贴到这里按回车。超时会自动换新链接（以最新那条为准）。", flush=True)
                    wait_secs = 66   # agy 自己只等 60 秒授权，超时就退出；这里按 60 秒算，过期自动换新链接
                else:
                    wait_secs = 120
                print(f"⏳ {'等你粘贴 code' if choice == '2' else '等待浏览器授权回调'}（第 {attempt + 1} 次，最长 {wait_secs} 秒）...", flush=True)

                def relay_callback(url):
                    """把用户粘回来的回调网址（或 code）转交给本机 agy 的接收端口"""
                    url = url.strip()
                    if url.startswith("4/") and "://" not in url:
                        try:
                            proc.stdin.write((url + "\n").encode()); proc.stdin.flush()
                            print("✅ 已把 code 交给 agy，正在接收凭证...", flush=True)
                            return
                        except Exception as e:
                            print(f"⚠️ 写入 agy 失败：{e}", flush=True)
                    if "://" not in url and "code=" not in url:
                        url = "https://antigravity.google/oauth-callback?code=" + urllib.parse.quote(url)
                    u = urllib.parse.urlparse(url)
                    query = u.query or urllib.parse.urlparse(url.replace("#", "?")).query
                    if "code=" not in query:
                        print("⚠️ 没找到授权码（需含 code=），已忽略。请粘贴浏览器地址栏的完整网址。", flush=True)
                        return
                    if u.hostname in ("localhost", "127.0.0.1"):
                        targets = [url.replace("localhost", "127.0.0.1")]
                    else:
                        # antigravity.google/oauth-callback 等网页地址：改投 agy 进程本机监听的端口
                        out = subprocess.run(["lsof", "-nP", "-a", "-p", str(proc.pid), "-iTCP", "-sTCP:LISTEN"],
                                             capture_output=True, text=True).stdout
                        ports = sorted({l.split(":")[-1].split()[0] for l in out.splitlines() if "LISTEN" in l})
                        targets = [f"http://127.0.0.1:{pt}{path}?{query}" for pt in ports
                                   for path in ("/oauth-callback", "/oauth2callback", "/callback", "/")]
                        if not ports:
                            print("⚠️ 找不到本机接收端口（agy 可能已退出），请重新运行绑定。", flush=True)
                            return
                    for t in targets:
                        try:
                            urllib.request.urlopen(t, timeout=5).read()
                            print("✅ 已提交授权，正在接收凭证...", flush=True)
                            return
                        except Exception:
                            continue
                    print("⚠️ 提交失败（授权可能已过期），请重新运行绑定。", flush=True)

                # 轮询获取新 Token
                import select
                _gone = 0
                pasted_once = False   # 注意：agy 会自己从终端读 code，脚本未必读得到这一行，所以不能以「粘贴过」作为接收凭据的前提；已有账号靠令牌指纹排除
                try:   # 丢掉上一轮残留在终端缓冲里的输入（旧链接的 code 对新链接无效）
                    import termios; termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                except Exception:
                    pass
                _deadline = time.time() + wait_secs
                for _ in iter(lambda: time.time() < _deadline, False):
                    if choice == "2" and select.select([sys.stdin], [], [], 0)[0]:
                        pasted = sys.stdin.readline().strip()
                        if pasted:
                            _key = pasted.strip().split("code=")[-1].split("&")[0]
                            if _key in _used_codes:
                                print("⚠️ 这串 code 已经提交过了（只能用一次，且只对应当时那条链接），已忽略。请用上方最新链接重新授权，拿新的 code。", flush=True)
                                continue
                            _used_codes.add(_key)
                            pasted_once = True
                            relay_callback(pasted)
                    # 检查钥匙串与令牌文件里有没有新账号的凭据
                    _found = find_new_credential()
                    if _found:
                        dbg("new credential found, saving")
                        token_to_save = _found
                        break

                    time.sleep(1.0)
                    if proc.poll() is not None and token_to_save:
                        break
                    if proc.poll() is not None and not token_to_save:
                        _gone = locals().get("_gone", 0) + 1
                        if _gone >= 2:
                            break

                try:
                    proc.terminate()
                except Exception:
                    pass
                if not token_to_save and choice == "2":
                    time.sleep(1.5)
                    token_to_save = find_new_credential()
                    dbg(f"final check after loop: {bool(token_to_save)}")
                if token_to_save or choice == "1":
                    break
                _why = ""
                try:
                    _errs = [l for l in open(os.path.join(VAULT_DIR, "login_agy.log")).read().splitlines() if l.startswith("Error:")]
                    _why = _errs[-1][:140] if _errs else ""
                except Exception:
                    pass
                if _why:
                    print(f"\n❌ agy 报错：{_why}\n   （code 无效、已用过或已超过 60 秒）正在生成新链接，请改用下面这一条...\n", flush=True)
                else:
                    print("\n🔄 这条链接已过期（agy 只等 60 秒），正在生成新链接，请改用下面这一条...\n", flush=True)

        finally:
            restore_env()

if token_to_save:
    email = extract_email(token_to_save)

    if email:
        for s, em in existing_emails.items():
            if em.lower() == email.lower():
                print("", flush=True)
                print("==================================================", flush=True)
                print(f"⚠️ 警告：检测到本次授权的邮箱是【{email}】！", flush=True)
                print(f"   它与已经存在的【{s}号账号】完全相同！", flush=True)
                print("==================================================", flush=True)
                ans = input(f"👉 你确定要将该邮箱再次绑定为【{slot}号】吗？(y/N): ").strip().lower()
                if ans not in ("y", "yes"):
                    print("❌ 操作已取消，未保存。", flush=True)
                    sys.exit(0)

    try:
        raw = token_to_save.replace("go-keyring-base64:", "")
        data = json.loads(base64.b64decode(raw))
        if email:
            data["id_token_email"] = email
        token_to_save = "go-keyring-base64:" + base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8")
    except Exception:
        pass

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(token_to_save)

    print("", flush=True)
    print("==================================================", flush=True)
    print("🎉 绑定成功！", flush=True)
    print(f"📧 邮箱: {email or '已授权'}", flush=True)
    print(f"💾 存储文件: {target_path}", flush=True)
    print("==================================================", flush=True)
    # 绑定后立刻查一次额度，按档位报出显示编号（P=Pro，S=其余），并让面板重画
    cache_path = os.path.join(VAULT_DIR, "multi_quota_cache.json")

    def probe_and_name():
        """查一次本槽位额度，返回 (显示编号, 档位, 探测输出)"""
        try:
            out = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "refresh_quota.py"), str(slot)],
                                 capture_output=True, text=True, timeout=90).stdout
        except Exception as e:
            out = f"探测出错: {e}"
        try:
            cache = json.load(open(cache_path))
        except Exception:
            cache = {}
        p_n = s_n = 0
        name = tier = None
        for n in range(1, 41):
            if not os.path.exists(os.path.join(VAULT_DIR, f"acc{n}.token")):
                continue
            d = cache.get(str(n)) or {}
            is_pro = d.get("argon_pct") is not None
            if is_pro: p_n += 1
            else: s_n += 1
            if str(n) == str(slot):
                name = f"P{p_n}" if is_pro else f"S{s_n}"
                tier = "Pro" if is_pro else ("Plus" if d.get("g5h_pct") is not None else ("Free" if d else "档位未知"))
        try:
            os.utime(cache_path, None)   # 面板看缓存文件时间变了就重画
        except OSError:
            pass
        return name, tier, out

    print("⏳ 正在查询这个号的额度和档位…", flush=True)
    name, tier, probe_out = probe_and_name()
    # 没通过 Antigravity 资格验证：直接给出验证链接，等用户在浏览器里做完再重查
    while "NOT_ELIGIBLE" in probe_out:
        vf = os.path.join(VAULT_DIR, f".verify_url_{slot}")
        url = open(vf).read().strip() if os.path.exists(vf) else ""
        print("", flush=True)
        print("⚠️ 这个号还没通过 Antigravity 资格验证，查不到额度。", flush=True)
        print("   用登录了这个号的浏览器打开下面的链接完成验证（链接只对这个号有效）：", flush=True)
        print(f"\n{url}\n", flush=True)
        if url and copy_to_clipboard(url):
            print("📋 验证链接已复制到剪贴板。", flush=True)
        try:
            ans = input("👉 验证完按回车重查；输入 o 回车在本机浏览器打开链接；输入 s 回车先跳过: ").strip().lower()
        except EOFError:
            ans = "s"
        if ans == "s":
            break
        if ans == "o" and url:
            subprocess.run(["/usr/bin/open", url], check=False)
            continue
        print("⏳ 重新查询…", flush=True)
        name, tier, probe_out = probe_and_name()
    print(f"🏷  编号: {name}（{tier}）", flush=True)
    if "NOT_ELIGIBLE" in probe_out:
        print("   还没验证，暂时排在 S 组；验证后重跑 python3 refresh_quota.py " + str(slot) + " 即可。", flush=True)
    elif tier == "档位未知":
        print(f"⚠️ 额度没查到，暂时排在 S 组。{probe_out.strip()[-160:]}", flush=True)
    print("👉 面板已刷新。", flush=True)
else:
    print("", flush=True)
    print("⚠️ 绑定未完成（超时或未在浏览器中确认授权）。", flush=True)
