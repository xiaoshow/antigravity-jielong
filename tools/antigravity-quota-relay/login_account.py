#!/usr/bin/env python3
"""
Interactive Account Adder & Token Importer for Antigravity 接龙 (Jielong)
用法:
    python3 login_account.py [槽位编号, 如 3, 4, 5]
"""
import sys, os, time, subprocess, json, base64, urllib.request, urllib.parse

VAULT_DIR = os.path.expanduser("~/.gemini/account_vault")
BIN_DIR = os.path.join(VAULT_DIR, ".bin")
os.makedirs(VAULT_DIR, exist_ok=True)
os.makedirs(BIN_DIR, exist_ok=True)

# 确保 open 拦截器存在，强制注入 prompt=select_account 并在无痕模式打开
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
    echo ""
    echo "========================================================"
    echo "🔗 Google 官方授权链接已生成 (已强制注入 prompt=select_account):"
    echo "$TARGET_URL"
    echo "========================================================"
    echo "💡 提示：系统已在【无痕窗口】中打开此链接（无旧账号缓存）。"
    echo "   如果浏览器未弹出，你也可以直接复制上方链接到任意浏览器完成登录！"
    echo ""
    if [ -d "/Applications/Google Chrome.app" ]; then
        /usr/bin/open -na "Google Chrome" --args --incognito "$TARGET_URL"
    else
        /usr/bin/open "$TARGET_URL"
    fi
else
    /usr/bin/open "$@"
fi
''')
os.chmod(open_wrapper, 0o755)

slot = sys.argv[1] if len(sys.argv) >= 2 else "3"
target_path = os.path.join(VAULT_DIR, f"acc{slot}.token")

print("==================================================")
print(f"👉 准备绑定/更新【{slot}号账号】")
print("==================================================")

def extract_email(token_str):
    """从 Token 中解析 Google 邮箱"""
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

# 获取已绑定的其他账号邮箱，防止误绑重复账号
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

# 1. 检查剪贴板是否已经复制了 Token
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
    print(f"📋 检测到剪贴板中已有有效的账号 Token！")
    print(f"   账号邮箱: [{clipboard_token[1]}]")
    ans = input(f"👉 是否直接将此剪贴板 Token 绑定为【{slot}号】？(Y/n): ").strip().lower()
    if ans in ("", "y", "yes"):
        token_to_save = clipboard_token[0]

if not token_to_save:
    print("")
    print("请选择绑定方式：")
    print("  [1] 浏览器全新授权（自动开启无痕/强制选择账号，防止误连旧账号）")
    print("  [2] 手动粘贴 Token 字符串")
    choice = input("请选择 (默认 1): ").strip()

    if choice == "2":
        user_input = input("👉 请粘贴你的 Token 字符串后按回车: ").strip()
        formatted = format_token_blob(user_input)
        if formatted:
            token_to_save = formatted
        else:
            print("❌ 输入的 Token 格式不正确，操作已取消。")
            sys.exit(1)
    else:
        # 备份当前钥匙串
        cur_token = None
        try:
            cur_token = subprocess.check_output(
                ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            pass

        subprocess.run(["security", "delete-generic-password", "-s", "gemini", "-a", "antigravity"], stderr=subprocess.DEVNULL)

        print("")
        print("🚀 正在调起 Google 官方授权服务...")
        print(f"💡 浏览器将自动以【无痕模式】打开，请在登录页面中选择或输入你的【第 {slot} 个账号】！")
        print("--------------------------------------------------")

        env = dict(os.environ)
        env["PATH"] = f"{BIN_DIR}:{env.get('PATH', '')}"

        try:
            subprocess.run(["/Users/weinliu/.local/bin/agy", "-p", "/usage"], env=env, check=False)
        except Exception:
            pass

        for _ in range(5):
            try:
                new_token = subprocess.check_output(
                    ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                    stderr=subprocess.DEVNULL
                ).decode().strip()
                if new_token:
                    token_to_save = new_token
                    break
            except Exception:
                time.sleep(0.5)

        # 恢复原钥匙串
        if cur_token:
            subprocess.run(
                ["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", cur_token],
                check=False, stderr=subprocess.DEVNULL
            )

if token_to_save:
    email = extract_email(token_to_save)

    # 重复账号安全检测
    if email:
        for s, em in existing_emails.items():
            if em.lower() == email.lower():
                print("")
                print("==================================================")
                print(f"⚠️ 警告：检测到本次授权的邮箱是【{email}】！")
                print(f"   它与已经存在的【{s}号账号】完全相同！")
                print("==================================================")
                ans = input(f"👉 你确定要将该邮箱再次绑定为【{slot}号】吗？(y/N): ").strip().lower()
                if ans not in ("y", "yes"):
                    print("❌ 操作已取消，未保存。")
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

    print("")
    print("==================================================")
    print(f"🎉 成功绑定【{slot}号账号】！")
    print(f"📧 邮箱: {email or '已授权'}")
    print(f"💾 存储文件: {target_path}")
    print("==================================================")
    print(f"👉 桌面悬浮 HUD 现已自动识别并显示【{slot}号】！")
else:
    print("⚠️ 绑定未完成。")
