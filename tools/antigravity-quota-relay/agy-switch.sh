#!/usr/bin/env bash
# Antigravity CLI & Desktop Multi-Account Relay Hot-Switcher
set -e

SLOT="$1"
VAULT_DIR="$HOME/.gemini/account_vault"

if [ -z "$SLOT" ]; then
    echo "Usage: agy-switch <1|2|...>"
    exit 1
fi

TOKEN_FILE="$VAULT_DIR/acc${SLOT}.token"
if [ ! -f "$TOKEN_FILE" ]; then
    echo "❌ Error: Account slot ${SLOT} not found in $VAULT_DIR"
    exit 1
fi

RAW=$(cat "$TOKEN_FILE")
security add-generic-password -U -s "gemini" -a "antigravity" -w "$RAW"
echo "✅ Switched system Keychain credentials to slot [${SLOT}]!"

# Optional: Fast reload desktop app if running
if pgrep -x "Antigravity" > /dev/null; then
    echo "🔄 Reloading Antigravity desktop app..."
    osascript -e 'tell application "Antigravity" to quit'
    sleep 1.2
    open -a "Antigravity"
    echo "🎉 Desktop app & CLI are now running on slot [${SLOT}]!"
fi
