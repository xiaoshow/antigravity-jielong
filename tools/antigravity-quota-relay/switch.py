#!/usr/bin/env python3
"""
Antigravity 秒级热切账号脚本
用法:
    python3 switch.py 1
    python3 switch.py 2
    python3 switch.py 3
"""
import sys, os, time, subprocess, json, base64

slot = sys.argv[1] if len(sys.argv) > 1 else "1"
vault_dir = os.path.expanduser("~/.gemini/account_vault")
token_file = os.path.join(vault_dir, f"acc{slot}.token")
app_storage = os.path.expanduser("~/Library/Application Support/Antigravity/app_storage.json")

if not os.path.exists(token_file):
    print(f"❌ 槽位【{slot}号】尚未绑定！")
    print(f"👉 请先运行绑定脚本: python3 ~/.gemini/account_vault/login_account.py {slot}")
    sys.exit(1)

with open(token_file, "r", encoding="utf-8") as f:
    raw_token = f.read().strip()

# 1. 提取邮箱
email = ""
try:
    b64 = raw_token.replace("go-keyring-base64:", "")
    data = json.loads(base64.b64decode(b64))
    email = data.get("id_token_email", "")
    if not email and "id_token" in data:
        parts = data["id_token"].split(".")
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        email = json.loads(base64.urlsafe_b64decode(pad)).get("email", "")
except Exception:
    pass

# 2. 写入系统钥匙串
subprocess.run(
    ["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", raw_token],
    check=True, stderr=subprocess.DEVNULL
)

# 3. 更新桌面 App 用户偏好设置
if email and os.path.exists(app_storage):
    try:
        with open(app_storage, "r", encoding="utf-8") as af:
            st = json.load(af)
        st["jetski.onboarding.lastLoginUsername"] = email
        with open(app_storage, "w", encoding="utf-8") as af:
            json.dump(st, af, indent=2, ensure_ascii=False)
    except Exception:
        pass

print(f"✅ 系统钥匙串与凭证已切换至【{slot}号账号】({email or '已就绪'})！")

# 4. 如果 Antigravity 桌面端正在运行，进行平滑热重载（保持所有会话与标签页）
pids = subprocess.getoutput("pgrep -f Antigravity").strip().split()
if pids and pids[0]:
    print("🔄 正在平滑重启 Antigravity 桌面端与后台语言服务...")
    subprocess.run(["osascript", "-e", 'tell application "Antigravity" to quit'], check=False)
    time.sleep(0.8)
    subprocess.run(["pkill", "-9", "-f", "Antigravity.app/Contents/Resources/bin/language_server"], check=False)
    subprocess.run(["pkill", "-9", "-f", "Antigravity.app/Contents/MacOS/Antigravity"], check=False)
    time.sleep(0.5)
    # 再次确保钥匙串稳固
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", raw_token],
        check=False, stderr=subprocess.DEVNULL
    )
    subprocess.run(["open", "-a", "Antigravity"], check=False)
    print("🎉 Antigravity 桌面端已成功带着新账号重新打开！")
else:
    print("💡 桌面 App 当前未运行，下次启动将自动使用该账号。")
