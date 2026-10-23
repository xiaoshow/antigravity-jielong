#!/usr/bin/env python3
"""
Interactive Account Adder for Antigravity 接龙 (Jielong)
用法: python3 login_account.py [槽位编号, 默认 3]
"""
import sys, os, time, subprocess, json, base64, urllib.request

VAULT_DIR = os.path.expanduser("~/.gemini/account_vault")
os.makedirs(VAULT_DIR, exist_ok=True)

slot = sys.argv[1] if len(sys.argv) >= 2 else "3"
target_path = os.path.join(VAULT_DIR, f"acc{slot}.token")

print("==========================================")
print(f"👉 准备绑定/更新【{slot}号账号】")
print("系统即将调起浏览器进行 Google 官方授权登录...")
print(f"请在弹出的浏览器页面中选择你要作为【{slot}号】的 Google 账号！")
print("==========================================")
time.sleep(1)

# 备份当前 Keychain 凭证
cur_token = None
try:
    cur_token = subprocess.check_output(
        ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
        stderr=subprocess.DEVNULL
    ).decode().strip()
except Exception:
    pass

# 清空当前钥匙串以触发全新 OAuth 登录
subprocess.run(["security", "delete-generic-password", "-s", "gemini", "-a", "antigravity"], stderr=subprocess.DEVNULL)

try:
    subprocess.run(["/Users/weinliu/.local/bin/agy", "-p", "/usage"], check=False)
except Exception:
    pass

try:
    new_token = subprocess.check_output(
        ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
        stderr=subprocess.DEVNULL
    ).decode().strip()
    if new_token:
        # 解析并注入邮箱标识，防止后续 OAuth 刷新后丢失身份识别
        email = ""
        try:
            raw = new_token.replace("go-keyring-base64:", "")
            data = json.loads(base64.b64decode(raw))
            acc_tok = data.get("token", {}).get("access_token", "")
            if acc_tok:
                req = urllib.request.Request(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {acc_tok}"}
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    email = json.loads(resp.read().decode()).get("email", "")
            if email:
                data["id_token_email"] = email
                new_token = "go-keyring-base64:" + base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8")
        except Exception:
            pass

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(new_token)
        print("==========================================")
        print(f"🎉 绑定成功！【{slot}号账号】({email or '已授权'}) 已存入保险库:")
        print(f"   {target_path}")
        print("==========================================")
    else:
        print("⚠️ 未检测到新授权凭证，已恢复原账号。")
        if cur_token:
            subprocess.run(["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", cur_token], check=False)
except Exception as e:
    print("❌ 获取凭证失败:", e)
    if cur_token:
        subprocess.run(["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", cur_token], check=False)
