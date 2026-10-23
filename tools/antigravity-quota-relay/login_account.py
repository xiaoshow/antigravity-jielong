#!/usr/bin/env python3
"""
Interactive Account Adder for Antigravity Quota Relay
"""
import sys, os, time, subprocess, json, base64

VAULT_DIR = os.path.expanduser("~/.gemini/account_vault")
os.makedirs(VAULT_DIR, exist_ok=True)

if len(sys.argv) < 2:
    print("用法: python3 login_account.py <槽位编号 (如 1, 2, 3...)>")
    sys.exit(1)

slot = sys.argv[1]
target_path = os.path.join(VAULT_DIR, f"acc{slot}.token")

print("==========================================")
print(f"👉 准备添加/更新【{slot}号账号】")
print("系统即将调起官方登录流程...")
print("请在浏览器中登录你想绑定的 Google 账号并授权")
print("==========================================")
time.sleep(1)

# 保存当前账号以防万一
cur_token = None
try:
    cur_token = subprocess.check_output(["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"], stderr=subprocess.DEVNULL).decode().strip()
except Exception:
    pass

subprocess.run(["security", "delete-generic-password", "-s", "gemini", "-a", "antigravity"], stderr=subprocess.DEVNULL)

try:
    subprocess.run(["agy", "-p", "hi"], check=False)
except Exception:
    pass

try:
    new_token = subprocess.check_output(["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"], stderr=subprocess.DEVNULL).decode().strip()
    if new_token:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(new_token)
        print(f"🎉 成功！新账号凭证已安全存入槽位【{slot}号】({target_path})")
    else:
        print("⚠️ 未检测到新授权凭证")
except Exception as e:
    print("❌ 获取凭证失败:", e)
finally:
    if cur_token:
        subprocess.run(["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", cur_token], check=True)
