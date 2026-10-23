#!/usr/bin/env bash
# 一键秒切账号（支持 1号 / 2号 / 3号...）并彻底重载桌面与后台服务
SLOT="$1"
VAULT_DIR="$HOME/.gemini/account_vault"
STANDALONE_TOKEN_FILE="$HOME/.gemini/jetski-standalone-oauth-token"
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

apply_creds() {
    RAW=$(cat "$TOKEN_FILE")
    security add-generic-password -U -s "gemini" -a "antigravity" -w "$RAW"
    python3 -c "
import json, base64, os
raw = '''$RAW'''.strip().replace('go-keyring-base64:', '')
decoded = base64.b64decode(raw).decode('utf-8')
with open(os.path.expanduser('$STANDALONE_TOKEN_FILE'), 'w', encoding='utf-8') as f:
    f.write(decoded)
d = json.loads(decoded)
email = d.get('id_token_email', '')
app_st = os.path.expanduser('$APP_STORAGE')
if email and os.path.exists(app_st):
    with open(app_st, 'r', encoding='utf-8') as f: st = json.load(f)
    st['jetski.onboarding.lastLoginUsername'] = email
    with open(app_st, 'w', encoding='utf-8') as f: json.dump(st, f, indent=2, ensure_ascii=False)
print(email)
"
}

EMAIL=$(apply_creds)
echo "✅ 系统凭证已热切换到【${SLOT}号账号】(${EMAIL})！"

# 如果桌面 App 正在运行，彻底清理 language_server 并冷重载
if pgrep -x "Antigravity" > /dev/null; then
    echo "🔄 正在重载桌面 App 与后台服务（会话与项目上下文 100% 保持）..."
    osascript -e 'tell application "Antigravity" to quit' 2>/dev/null
    sleep 0.9
    pkill -9 -f "Antigravity.app/Contents/Resources/bin/language_server" 2>/dev/null
    pkill -9 -f "Antigravity.app/Contents/MacOS/Antigravity" 2>/dev/null
    sleep 0.5
    apply_creds > /dev/null
    open -a "Antigravity"
    echo "🎉 桌面端与 CLI 已全量满血切换至【${SLOT}号】(${EMAIL})！"
fi
