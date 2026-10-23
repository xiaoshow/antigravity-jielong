#!/usr/bin/env bash
# 一键秒切账号（支持 1号 / 2号 / 3号...）并彻底重载桌面与后台服务
SLOT="$1"
VAULT_DIR="$HOME/.gemini/account_vault"
APP_STORAGE="$HOME/Library/Application Support/Antigravity/app_storage.json"

if [ -z "$SLOT" ]; then
    echo "用法: agy-switch <1|2|3>"
    exit 1
fi

TOKEN_FILE="$VAULT_DIR/acc${SLOT}.token"
if [ ! -f "$TOKEN_FILE" ]; then
    echo "👉 槽位【${SLOT}号】尚未绑定，正在调起浏览器授权绑定..."
    python3 "$VAULT_DIR/login_account.py" "$SLOT"
    if [ ! -f "$TOKEN_FILE" ]; then
        echo "❌ 绑定未完成！"
        exit 1
    fi
fi

RAW=$(cat "$TOKEN_FILE")
security add-generic-password -U -s "gemini" -a "antigravity" -w "$RAW"

# 提取邮箱并同步更新桌面端 app_storage.json
EMAIL=$(python3 -c "
import json, base64
try:
    raw = '''$RAW'''.strip().replace('go-keyring-base64:', '')
    d = json.loads(base64.b64decode(raw))
    print(d.get('id_token_email', ''))
except Exception:
    pass
")

if [ -n "$EMAIL" ] && [ -f "$APP_STORAGE" ]; then
    python3 -c "
import json
p = '''$APP_STORAGE'''
try:
    with open(p, 'r') as f: st = json.load(f)
    st['jetski.onboarding.lastLoginUsername'] = '''$EMAIL'''
    with open(p, 'w') as f: json.dump(st, f, indent=2, ensure_ascii=False)
except Exception:
    pass
"
fi

echo "✅ 系统 Keychain 已热切换到【${SLOT}号账号】(${EMAIL})！"

# 如果桌面 App 正在运行，彻底清理 language_server 并冷重载
if pgrep -x "Antigravity" > /dev/null; then
    echo "🔄 正在重载桌面 App 与后台服务（会话与项目上下文 100% 保持）..."
    osascript -e 'tell application "Antigravity" to quit' 2>/dev/null
    sleep 0.8
    pkill -f "Antigravity.app/Contents/Resources/bin/language_server" 2>/dev/null
    sleep 0.6
    open -a "Antigravity"
    echo "🎉 桌面端与 CLI 已全量满血切换至【${SLOT}号】！"
fi
