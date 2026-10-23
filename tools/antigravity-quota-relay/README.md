# Antigravity Quota Relay & Multi-Account HUD ⚡

A lightweight, zero-token desktop HUD and CLI hot-switcher for Google Antigravity.

Designed for developers running multi-account setups to relay and rotate through daily/5-hour quota limits seamlessly without losing workspace context, active tasks, or conversation history.

---

## ✨ Features

- **0 Token Consumption**: Reads internal RPC quota metrics without sending LLM prompts.
- **macOS Keychain Hot-Swapping**: Switches OAuth credentials in milliseconds without logging out or re-authenticating.
- **Dual-Mode Floating HUD**:
  - **Dense Matrix View**: Monitors multiple accounts simultaneously (Claude 5h/Weekly, Gemini 5h/Weekly, Argon).
  - **Detailed Gauge View**: Single-click anywhere on the HUD to toggle full 5-bar progress gauge view.
- **Model-Aware Dynamic Attachment**: Progress bars automatically attach to whichever model is actively being queried (`Claude`, `Gemini`, or `Argon`).
- **Seamless Desktop App + CLI Handoff**: Auto-reloads Antigravity Desktop App while preserving all active panes, projects, and conversation state.

---

## 🚀 Quick Start

### 1. Requirements
- macOS (Sonoma, Sequoia, or later)
- Python 3.9+ (Native `tkinter` included with system/Homebrew Python)
- Antigravity CLI (`agy`) & Desktop App

### 2. Launching the HUD
```bash
python3 tools/antigravity-quota-relay/quota_relay_hud.py
```

### 3. Switching Accounts via Command Line
```bash
./tools/antigravity-quota-relay/agy-switch.sh 1   # Switch to Account 1
./tools/antigravity-quota-relay/agy-switch.sh 2   # Switch to Account 2
```

---

## 🔒 Security & Privacy

This project strictly stores credentials in `~/.gemini/account_vault/` and macOS Keychain. 
All `.token` and `*_cache.json` files are excluded from Git version control. No telemetry or tokens are ever transmitted externally.
