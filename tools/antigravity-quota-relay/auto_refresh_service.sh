#!/bin/bash
# 自动静默刷新接龙账号额度缓存（物理阻断浏览器弹窗 + 自动隔离失效/未验证号 + 低频省电）
export HOME="/Users/weinliu"
export PATH="/Users/weinliu/.gemini/account_vault/.no_browser_bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/Users/weinliu/.local/bin"

DIR="/Users/weinliu/tools/antigravity-quota-relay"
VAULT="/Users/weinliu/.gemini/account_vault"
LOCK="$VAULT/.auto_refresh_lock"
LOG="$VAULT/auto_refresh.log"

if ! mkdir "$LOCK" 2>/dev/null; then
    if [ $(($(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0))) -gt 180 ]; then
        rm -rf "$LOCK"
        mkdir "$LOCK" 2>/dev/null || exit 0
    else
        exit 0
    fi
fi
trap 'rm -rf "$LOCK"' EXIT

ACTIVE_FILE="$VAULT/.active_slot"
ACTIVE_SLOT="19"
if [ -f "$ACTIVE_FILE" ]; then
    ACTIVE_SLOT=$(cat "$ACTIVE_FILE" | tr -d '[:space:]')
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Refresh tick (active=$ACTIVE_SLOT)" >> "$LOG"

# 1. 每次必刷：当前活跃号 + 0号(19) + P1(1) + P2(2) + P3(3)，严格跳过 .verify_url_* 与 .auth_expired_*
PRIORITY_SLOTS=("$ACTIVE_SLOT" "19" "1" "2" "3")
SEEN=" "
BATCH=()
for S in "${PRIORITY_SLOTS[@]}"; do
    if [[ "$SEEN" != *" $S "* ]] && [ -f "$VAULT/acc${S}.token" ] && [ ! -f "$VAULT/.verify_url_${S}" ] && [ ! -f "$VAULT/.auth_expired_${S}" ]; then
        SEEN="$SEEN $S "
        BATCH+=("$S")
    fi
done

# 2. 闲置 S 号仅在缓存超过 1 小时未更新时才顺带刷 1 个（且自动跳过 .verify_url_* 与 .auth_expired_*）
STALE_S=$(/opt/homebrew/bin/python3 - <<'PY'
import os, json, datetime, time
v = os.path.expanduser("~/.gemini/account_vault")
try:
    c = json.load(open(os.path.join(v, "multi_quota_cache.json")))
except Exception:
    c = {}
now = time.time()
best_s, best_age = "", 0
for i in range(4, 22):
    s = str(i)
    if s == "19":
        continue
    if not os.path.exists(os.path.join(v, f"acc{s}.token")):
        continue
    if os.path.exists(os.path.join(v, f".verify_url_{s}")) or os.path.exists(os.path.join(v, f".auth_expired_{s}")):
        continue
    u = c.get(s, {}).get("updated_at", "")
    try:
        ts = datetime.datetime.fromisoformat(u.replace("Z", "+00:00")).timestamp() if u else 0
    except Exception:
        ts = 0
    age = now - ts
    if age > 3600 and age > best_age:
        best_s, best_age = s, age
print(best_s)
PY
)

if [ -n "$STALE_S" ] && [[ "$SEEN" != *" $STALE_S "* ]]; then
    BATCH+=("$STALE_S")
fi

if [ ${#BATCH[@]} -gt 0 ]; then
    /opt/homebrew/bin/python3 "$DIR/refresh_quota.py" "${BATCH[@]}" >> "$LOG" 2>&1
fi

# 保持日志精简（只留最近 300 行）
if [ -f "$LOG" ] && [ $(wc -l < "$LOG") -gt 400 ]; then
    tail -n 300 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
