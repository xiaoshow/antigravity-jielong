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

slot = sys.argv[1] if len(sys.argv) >= 2 else "4"
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
for s in ["1", "2", "3", "4", "5"]:
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
    choice = input("请选择 (默认 1): ").strip()
    if choice not in ("1", "2", "3"):
        choice = "1"

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
            if cur_token:
                subprocess.run(
                    ["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", cur_token],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            if os.path.exists(b1): shutil.move(b1, f1)
            if os.path.exists(b2): shutil.move(b2, f2)
            if os.path.exists(LOCK_FILE):
                try: os.remove(LOCK_FILE)
                except Exception: pass
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

            # 调起 agy，输出完全重定向，防止控制台打印冗余的 Quota
            proc = subprocess.Popen(
                ["/Users/weinliu/.local/bin/agy", "-p", "/usage"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
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
                print("📋 [手动模式] 请复制上方链接，粘贴到你已登录该账号的任意浏览器窗口打开！", flush=True)
                print("👉 在浏览器点击账号完成授权后，本终端将自动接收凭证（无需手动复制 Token）...", flush=True)

            if choice == "2":
                print("💡 授权后若页面打不开/无反应：把浏览器地址栏里以 http://localhost 或 http://127.0.0.1 开头的整条网址复制，粘贴到这里按回车即可。", flush=True)
                wait_secs = 600
            else:
                wait_secs = 120
            print(f"⏳ 等待浏览器授权回调中（最长 {wait_secs} 秒）...", flush=True)

            def relay_callback(url):
                """把用户粘回来的回调网址由本机转交给 agy 的本地接收端口"""
                u = urllib.parse.urlparse(url)
                if u.scheme != "http" or u.hostname not in ("localhost", "127.0.0.1") or "code=" not in u.query:
                    print("⚠️ 这不是授权回调网址（需以 http://localhost 开头且含 code=），已忽略。", flush=True)
                    return
                try:
                    urllib.request.urlopen(url, timeout=10).read()
                    print("✅ 已提交授权，正在接收凭证...", flush=True)
                except Exception as e:
                    print(f"⚠️ 提交失败：{e}（授权可能已过期，请重新运行）", flush=True)

            # 轮询获取新 Token
            import select
            for _ in range(wait_secs):
                if choice == "2" and select.select([sys.stdin], [], [], 0)[0]:
                    pasted = sys.stdin.readline().strip()
                    if pasted:
                        relay_callback(pasted)
                # 检查钥匙串
                try:
                    new_token = subprocess.check_output(
                        ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                        stderr=subprocess.DEVNULL
                    ).decode().strip()
                    if new_token and new_token != cur_token:
                        token_to_save = new_token
                        break
                except Exception:
                    pass

                # 检查独立 token 文件
                if os.path.exists(f1):
                    try:
                        with open(f1, "r", encoding="utf-8") as sf:
                            sdata = json.load(sf)
                        if sdata.get("token") or sdata.get("id_token_email"):
                            sb64 = base64.b64encode(json.dumps(sdata).encode("utf-8")).decode("utf-8")
                            token_to_save = "go-keyring-base64:" + sb64
                            break
                    except Exception:
                        pass

                time.sleep(1.0)
                if proc.poll() is not None and token_to_save:
                    break

            try:
                proc.terminate()
            except Exception:
                pass

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
    print(f"🎉 成功绑定【{slot}号账号】！", flush=True)
    print(f"📧 邮箱: {email or '已授权'}", flush=True)
    print(f"💾 存储文件: {target_path}", flush=True)
    print("==================================================", flush=True)
    print(f"👉 桌面悬浮 HUD 现已自动识别并显示【{slot}号】！", flush=True)
else:
    print("", flush=True)
    print("⚠️ 绑定未完成（超时或未在浏览器中确认授权）。", flush=True)
