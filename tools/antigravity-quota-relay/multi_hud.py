#!/usr/bin/env python3
"""
Antigravity Multi-Account Quota & Switcher HUD (可折叠多账号接龙版)
- 支持 1~5+ 个多账号接龙管理
- 支持【全局折叠】与【单卡折叠】：多账号时一键折叠为超薄矩阵（每账号仅占一行，极度轻巧省空间）
- 加上 Argon 6天倒计时 (如: 6d23h)
- 互斥锁保护，彻底杜绝切号与后台轮询冲突
- 支持未绑定槽位点击直接调起无痕模式选择绑定
"""

import sys, os, time, datetime, json, base64, subprocess, threading, glob, urllib.request
import tkinter as tk

VAULT_DIR = os.path.expanduser("~/.gemini/account_vault")
CACHE_FILE = os.path.expanduser("~/.gemini/account_vault/multi_quota_cache.json")
CLI_SETTINGS = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
STANDALONE_TOKEN_FILE = os.path.expanduser("~/.gemini/jetski-standalone-oauth-token")
APP_STORAGE = os.path.expanduser("~/Library/Application Support/Antigravity/app_storage.json")
os.makedirs(VAULT_DIR, exist_ok=True)

BG_COLOR = "#0c0e12"
CARD_BG = "#131720"
CARD_ACTIVE_BG = "#161d2a"
BORDER_INACTIVE = "#1f2633"
BORDER_ACTIVE = "#2b5278"

TRACK_COLOR = "#1b212d"
COLOR_GREEN = "#10b981"
COLOR_YELLOW = "#f59e0b"
COLOR_RED = "#ef4444"
COLOR_CYAN = "#06b6d4"

TEXT_PRIMARY = "#f8fafc"
TEXT_SECONDARY = "#94a3b8"
TEXT_MUTED = "#64748b"

class MultiAccountHUD:
    def __init__(self, root):
        self.root = root
        self.root.title("⚡ Antigravity 接龙 HUD")
        
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.configure(bg=BG_COLOR)
        
        # 折叠状态：默认如果账号数 >= 3 自动折叠，否则展开
        self.collapsed_mode = False
        
        self.w_normal, self.h_normal = 370, 440
        self.w_collapsed, self.h_collapsed = 370, 240
        
        screen_w = self.root.winfo_screenwidth()
        x = max(20, screen_w - self.w_normal - 25)
        y = 65
        self.root.geometry(f"{self.w_normal}x{self.h_normal}+{x}+{y}")
        
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.has_moved = False
        
        self.keychain_lock = threading.Lock()
        self.switching_slot = False
        self.is_fetching = False
        self.is_binding = False
        self.last_fetch_ts = 0
        
        self.account_emails = self.load_emails()
        self.active_slot = self.detect_current_slot()
        self.active_model_group = self.detect_active_model_group()
        self.account_data = {}
        
        self.load_cache()
        self.setup_ui()
        
        self.trigger_refresh()
        self.root.after(1000, self.tick)

    def get_available_slots(self):
        """动态检测已配置的账号槽位，并追加一个待绑定的新槽位（最多支持 5 个号）"""
        slots = []
        for i in range(1, 6):
            p = os.path.join(VAULT_DIR, f"acc{i}.token")
            if os.path.exists(p):
                slots.append(str(i))
        next_slot = str(len(slots) + 1)
        if int(next_slot) <= 5:
            slots.append(next_slot)
        return slots or ["1", "2", "3"]

    def detect_active_model_group(self):
        try:
            files = glob.glob(os.path.expanduser("~/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl"))
            if files:
                latest = max(files, key=os.path.getmtime)
                with open(latest, "r") as f:
                    lines = f.readlines()
                    for line in reversed(lines[-25:]):
                        d = json.loads(line)
                        m = str(d.get("model") or "").lower()
                        if "argon" in m: return "argon"
                        if any(k in m for k in ("claude", "opus", "sonnet", "3p")): return "claude"
                        if any(k in m for k in ("gemini", "flash", "pro")): return "gemini"
        except Exception:
            pass
        return "claude"

    def extract_email_from_token(self, blob, save_back_path=None):
        try:
            raw = blob.strip().replace("go-keyring-base64:", "")
            data = json.loads(base64.b64decode(raw))
            if "id_token_email" in data and data["id_token_email"]:
                return data["id_token_email"].lower()
            if "id_token" in data and data["id_token"]:
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
                    email = json.loads(resp.read().decode()).get("email", "").lower()
                if email:
                    data["id_token_email"] = email
                    if save_back_path:
                        new_blob = "go-keyring-base64:" + base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8")
                        with open(save_back_path, "w") as wf:
                            wf.write(new_blob)
                    return email
        except Exception:
            pass
        return ""

    def load_emails(self):
        emails = {}
        for slot in ["1", "2", "3", "4", "5"]:
            p = os.path.join(VAULT_DIR, f"acc{slot}.token")
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        emails[slot] = self.extract_email_from_token(f.read(), save_back_path=p)
                except Exception:
                    emails[slot] = ""
        return emails

    def apply_slot_credentials(self, slot):
        tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
        if not os.path.exists(tpath):
            return False
        with open(tpath) as f:
            raw = f.read().strip()
        if not raw:
            return False

        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", raw],
            check=False, stderr=subprocess.DEVNULL
        )

        try:
            b64_part = raw.replace("go-keyring-base64:", "")
            decoded_json = base64.b64decode(b64_part).decode("utf-8")
            with open(STANDALONE_TOKEN_FILE, "w", encoding="utf-8") as sf:
                sf.write(decoded_json)
        except Exception:
            pass

        email = self.account_emails.get(slot, "")
        if email and os.path.exists(APP_STORAGE):
            try:
                with open(APP_STORAGE, "r", encoding="utf-8") as af:
                    st = json.load(af)
                st["jetski.onboarding.lastLoginUsername"] = email
                with open(APP_STORAGE, "w", encoding="utf-8") as af:
                    json.dump(st, af, indent=2, ensure_ascii=False)
            except Exception:
                pass
        return True

    def on_press(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.has_moved = False

    def on_motion(self, event):
        dx = event.x - self.drag_start_x
        dy = event.y - self.drag_start_y
        if abs(dx) > 3 or abs(dy) > 3:
            self.has_moved = True
            self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")

    def on_release(self, event):
        pass

    def toggle_collapse(self):
        self.collapsed_mode = not self.collapsed_mode
        self.update_geometry()
        self.setup_ui()

    def update_geometry(self):
        slots = self.get_available_slots()
        if self.collapsed_mode:
            h = 42 + (len(slots) * 34) + 48
            w = 370
        else:
            # 展开模式：根据账号数量自适应高度
            h = 42 + (len(slots) * 88) + 48
            w = 370
        self.root.geometry(f"{w}x{h}")

    def bind_drag(self, widget):
        widget.bind("<ButtonPress-1>", self.on_press)
        widget.bind("<B1-Motion>", self.on_motion)
        widget.bind("<ButtonRelease-1>", self.on_release)

    def detect_current_slot(self):
        try:
            cmd = ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"]
            cur = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
            cur_email = self.extract_email_from_token(cur)
            if cur_email:
                for slot in ["1", "2", "3", "4", "5"]:
                    if self.account_emails.get(slot, "").lower() == cur_email:
                        tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
                        try:
                            raw = cur.replace("go-keyring-base64:", "")
                            data = json.loads(base64.b64decode(raw))
                            data["id_token_email"] = cur_email
                            updated = "go-keyring-base64:" + base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8")
                            with open(tpath, "w") as f:
                                f.write(updated)
                        except Exception:
                            pass
                        return slot
        except Exception:
            pass
        return "1"

    def load_cache(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE) as f:
                    self.account_data = json.load(f)
            except Exception:
                pass

    def save_cache(self):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self.account_data, f, indent=2)
        except Exception:
            pass

    def setup_ui(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        self.time_labels = []
        self.active_model_group = self.detect_active_model_group()
        slots = self.get_available_slots()

        # 1. Header
        header = tk.Frame(self.root, bg="#11141a", height=34, padx=10)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)
        self.bind_drag(header)

        mode_badge = "⇕ 展开视图" if self.collapsed_mode else "⇕ 折叠视图"
        lbl_badge = tk.Label(header, text=mode_badge, fg=COLOR_CYAN, bg="#11141a", font=("SF Mono", 9, "bold"), cursor="pointinghand")
        lbl_badge.pack(side=tk.LEFT, padx=(0, 6), pady=6)
        lbl_badge.bind("<Button-1>", lambda e: self.toggle_collapse())

        best_slot = self.calc_recommend_slot()
        cur_d = self.account_data.get(self.active_slot, {})
        cur_cwk = cur_d.get("cwk_pct", 100.0)
        cur_c5 = cur_d.get("c5h_pct", 100.0)
        is_cur_dead = (cur_cwk <= 0.01 or cur_d.get("c5h_reset") == "DISABLED")

        if self.switching_slot:
            rec_text = f" · 正在切换至 {self.active_slot}号..."
        elif self.is_binding:
            rec_text = " · 正在浏览器授权..."
        elif is_cur_dead:
            rec_text = f" · 建议切{best_slot}号!" if best_slot and best_slot != self.active_slot else " · Claude已耗尽"
        elif best_slot and best_slot != self.active_slot and (self.account_data.get(best_slot, {}).get("c5h_pct", 0) > cur_c5 + 30):
            rec_text = f" · 建议切{best_slot}号"
        elif cur_c5 < 15:
            rec_text = f" · 建议切{best_slot}号" if best_slot and best_slot != self.active_slot else " · 配额紧张"
        else:
            rec_text = " · 状态极佳"
        title_text = f"桌面 {self.active_slot}号{rec_text}"

        lbl_title = tk.Label(header, text=title_text, fg=TEXT_PRIMARY, bg="#11141a", font=("SF Pro Text", 9, "bold"))
        lbl_title.pack(side=tk.LEFT, pady=6)
        self.bind_drag(lbl_title)

        btn_ref = tk.Label(header, text="⟳", fg=TEXT_MUTED, bg="#11141a", font=("SF Mono", 12, "bold"), cursor="pointinghand")
        btn_ref.pack(side=tk.RIGHT, pady=4)
        btn_ref.bind("<Button-1>", lambda e: self.trigger_refresh())
        btn_ref.bind("<Enter>", lambda e: btn_ref.configure(fg=COLOR_CYAN))
        btn_ref.bind("<Leave>", lambda e: btn_ref.configure(fg=TEXT_MUTED))

        # 2. Body
        self.body = tk.Frame(self.root, bg=BG_COLOR, padx=8, pady=4)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.bind_drag(self.body)

        if self.collapsed_mode:
            self.render_collapsed_matrix(slots)
        else:
            self.render_expanded_cards(slots)

        # 3. Bottom Switch Bar
        bottom_bar = tk.Frame(self.root, bg="#11141a", height=40, padx=10)
        bottom_bar.pack(fill=tk.X, side=tk.BOTTOM)
        bottom_bar.pack_propagate(False)
        self.bind_drag(bottom_bar)

        lbl_switch = tk.Label(bottom_bar, text="切号接力", fg=TEXT_MUTED, bg="#11141a", font=("SF Pro Text", 9, "bold"))
        lbl_switch.pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self.bind_drag(lbl_switch)

        for slot in slots:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            has_tok = os.path.exists(tpath)
            is_active = (slot == self.active_slot) and has_tok
            bg = "#1e293b" if is_active else "#151922"
            if not has_tok:
                fg = COLOR_CYAN
                txt = f"+ {slot}号"
            else:
                fg = COLOR_GREEN if is_active else TEXT_SECONDARY
                txt = f"{slot}号 ✓" if is_active else f"{slot}号"
            border = "#3b82f6" if is_active else BORDER_INACTIVE

            btn = tk.Label(bottom_bar, text=txt, fg=fg, bg=bg, font=("SF Mono", 9, "bold"), padx=10, pady=3, cursor="pointinghand", highlightbackground=border, highlightthickness=1)
            btn.pack(side=tk.LEFT, padx=3, pady=6)
            btn.bind("<Button-1>", lambda e, s=slot: self.switch_to_slot(s))

    def render_collapsed_matrix(self, slots):
        """紧凑折叠视图：每账号一行，超薄卡片，支持 5 个账号仅占微小屏幕"""
        for slot in slots:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            has_token = os.path.exists(tpath)
            is_active = (slot == self.active_slot) and has_token
            data = self.account_data.get(slot, {})
            email = self.account_emails.get(slot, "")

            bg = CARD_ACTIVE_BG if is_active else CARD_BG
            border = BORDER_ACTIVE if is_active else BORDER_INACTIVE

            row = tk.Frame(self.body, bg=bg, highlightbackground=border, highlightthickness=1, padx=8, pady=3)
            row.pack(fill=tk.X, pady=2)
            self.bind_drag(row)

            l_slot = tk.Label(row, text=f"{slot}号", fg=COLOR_GREEN if is_active else TEXT_PRIMARY, bg=bg, font=("SF Mono", 9, "bold"))
            l_slot.pack(side=tk.LEFT)
            self.bind_drag(l_slot)

            if not has_token:
                btn_b = tk.Label(row, text="+ 点击绑定此账号", fg=COLOR_CYAN, bg=bg, font=("SF Pro Text", 8, "bold"), cursor="pointinghand")
                btn_b.pack(side=tk.LEFT, padx=(8, 0))
                btn_b.bind("<Button-1>", lambda e, s=slot: self.bind_new_slot(s))
                continue

            # 简写 C-5h
            c5 = data.get("c5h_pct", 0.0)
            c5_col = self.get_color(c5)
            tk.Label(row, text="C:", fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT, padx=(6, 1))
            c5_text = "周耗" if data.get("c5h_reset") == "DISABLED" else f"{c5:4.0f}%"
            tk.Label(row, text=c5_text, fg=c5_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)

            # 简写 G-5h
            g5 = data.get("g5h_pct", 0.0)
            g5_col = self.get_color(g5)
            tk.Label(row, text="G:", fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT, padx=(6, 1))
            tk.Label(row, text=f"{g5:4.0f}%", fg=g5_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)

            # 简写 Argon
            arg = data.get("argon_pct", 0.0)
            arg_reset = data.get("argon_reset", "")
            arg_col = self.get_color(arg)
            tk.Label(row, text="Arg:", fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT, padx=(6, 1))
            tk.Label(row, text=f"{arg:4.0f}%", fg=arg_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)

            # 倒计时
            t_cnt = tk.Label(row, text=self.format_countdown(arg_reset), fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
            t_cnt.pack(side=tk.RIGHT)
            self.bind_drag(t_cnt)
            self.time_labels.append((t_cnt, arg_reset))

    def render_expanded_cards(self, slots):
        """展开模式：完整卡片、进度条与详细倒计时"""
        for slot in slots:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            has_token = os.path.exists(tpath)
            is_active = (slot == self.active_slot) and has_token
            data = self.account_data.get(slot, {})
            email = self.account_emails.get(slot, "")

            bg = CARD_ACTIVE_BG if is_active else CARD_BG
            border = BORDER_ACTIVE if is_active else BORDER_INACTIVE
            
            card = tk.Frame(self.body, bg=bg, highlightbackground=border, highlightthickness=1, padx=10, pady=4)
            card.pack(fill=tk.X, pady=3)
            self.bind_drag(card)

            # 1. 头部
            header_row = tk.Frame(card, bg=bg)
            header_row.pack(fill=tk.X, pady=(0, 2))
            self.bind_drag(header_row)

            l_slot = tk.Label(header_row, text=f"{slot}号", fg=TEXT_PRIMARY, bg=bg, font=("SF Pro Display", 10, "bold"))
            l_slot.pack(side=tk.LEFT)
            self.bind_drag(l_slot)

            if not has_token:
                btn_bind = tk.Label(
                    header_row,
                    text=f"尚未绑定 · 点击此处绑定【{slot}号】",
                    fg=COLOR_CYAN,
                    bg=bg,
                    font=("SF Pro Text", 8, "bold"),
                    cursor="pointinghand"
                )
                btn_bind.pack(side=tk.LEFT, padx=(8, 0))
                btn_bind.bind("<Button-1>", lambda e, s=slot: self.bind_new_slot(s))
                continue

            if email:
                mail_short = email if len(email) <= 24 else email.split("@")[0] + "@..."
                l_mail = tk.Label(header_row, text=mail_short, fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
                l_mail.pack(side=tk.LEFT, padx=(6, 0))
                self.bind_drag(l_mail)

            if is_active:
                badge = tk.Label(header_row, text="● 活跃中", fg=COLOR_GREEN, bg=bg, font=("SF Pro Text", 8, "bold"))
                badge.pack(side=tk.RIGHT)
                self.bind_drag(badge)

            c5h_pct = data.get("c5h_pct", 0.0)
            c5h_reset = data.get("c5h_reset", "")
            cwk_pct = data.get("cwk_pct", 0.0)
            cwk_reset = data.get("cwk_reset", "")
            
            g5h_pct = data.get("g5h_pct", 0.0)
            g5h_reset = data.get("g5h_reset", "")
            gwk_pct = data.get("gwk_pct", 0.0)
            gwk_reset = data.get("gwk_reset", "")
            
            argon_pct = data.get("argon_pct", 0.0)
            argon_reset = data.get("argon_reset", "")

            # 2. Claude 行
            c_row = tk.Frame(card, bg=bg, pady=1)
            c_row.pack(fill=tk.X)
            self.bind_drag(c_row)

            is_c_active = (self.active_model_group == "claude")
            c5h_col = self.get_color(c5h_pct)
            c_tag_col = COLOR_CYAN if is_c_active else TEXT_SECONDARY
            
            tk.Label(c_row, text="C-5h", fg=c_tag_col, bg=bg, font=("SF Mono", 8, "bold" if is_c_active else "normal")).pack(side=tk.LEFT)
            tk.Label(c_row, text=f"{c5h_pct:5.1f}%", fg=c5h_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
            
            t1 = tk.Label(c_row, text=self.format_countdown(c5h_reset), fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
            t1.pack(side=tk.LEFT, padx=(0, 10))
            self.bind_drag(t1)
            self.time_labels.append((t1, c5h_reset))

            cwk_col = self.get_color(cwk_pct)
            tk.Label(c_row, text="C-Wk", fg=TEXT_SECONDARY, bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT)
            tk.Label(c_row, text=f"{cwk_pct:5.1f}%", fg=cwk_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
            t2 = tk.Label(c_row, text=self.format_countdown(cwk_reset), fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
            t2.pack(side=tk.LEFT)
            self.bind_drag(t2)
            self.time_labels.append((t2, cwk_reset))

            # 3. Gemini 行
            g_row = tk.Frame(card, bg=bg, pady=1)
            g_row.pack(fill=tk.X)
            self.bind_drag(g_row)

            is_g_active = (self.active_model_group == "gemini")
            g5h_col = self.get_color(g5h_pct)
            g_tag_col = COLOR_CYAN if is_g_active else TEXT_SECONDARY
            tk.Label(g_row, text="G-5h", fg=g_tag_col, bg=bg, font=("SF Mono", 8, "bold" if is_g_active else "normal")).pack(side=tk.LEFT)
            tk.Label(g_row, text=f"{g5h_pct:5.1f}%", fg=g5h_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))

            t_g5 = tk.Label(g_row, text=self.format_countdown(g5h_reset), fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
            t_g5.pack(side=tk.LEFT, padx=(0, 10))
            self.bind_drag(t_g5)
            self.time_labels.append((t_g5, g5h_reset))

            gwk_col = self.get_color(gwk_pct)
            tk.Label(g_row, text="G-Wk", fg=TEXT_SECONDARY, bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT)
            tk.Label(g_row, text=f"{gwk_pct:5.1f}%", fg=gwk_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
            t_gwk = tk.Label(g_row, text=self.format_countdown(gwk_reset), fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
            t_gwk.pack(side=tk.LEFT)
            self.bind_drag(t_gwk)
            self.time_labels.append((t_gwk, gwk_reset))

            # 4. Argon
            arg_row = tk.Frame(card, bg=bg, pady=1)
            arg_row.pack(fill=tk.X)
            self.bind_drag(arg_row)

            is_a_active = (self.active_model_group == "argon")
            arg_col = self.get_color(argon_pct)
            a_tag_col = COLOR_CYAN if is_a_active else TEXT_SECONDARY
            tk.Label(arg_row, text="Argon", fg=a_tag_col, bg=bg, font=("SF Mono", 8, "bold" if is_a_active else "normal")).pack(side=tk.LEFT)
            tk.Label(arg_row, text=f"{argon_pct:5.1f}%", fg=arg_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 6))

            cv_a = tk.Canvas(arg_row, bg=TRACK_COLOR, width=54, height=4, highlightthickness=0)
            cv_a.pack(side=tk.LEFT, padx=(0, 8))
            w_a = max(0, min(54, int(54 * (argon_pct / 100.0))))
            if w_a > 0: cv_a.create_rectangle(0, 0, w_a, 4, fill=arg_col, width=0)
            self.bind_drag(cv_a)

            tk.Label(arg_row, text="复位:", fg=TEXT_MUTED, bg=bg, font=("SF Pro Text", 8)).pack(side=tk.LEFT)
            t_arg = tk.Label(arg_row, text=self.format_countdown(argon_reset), fg=COLOR_CYAN if argon_pct < 20 else TEXT_MUTED, bg=bg, font=("SF Mono", 8, "bold"))
            t_arg.pack(side=tk.LEFT, padx=(2, 0))
            self.bind_drag(t_arg)
            self.time_labels.append((t_arg, argon_reset))

    def get_color(self, pct):
        if pct < 15:
            return COLOR_RED
        if pct < 45:
            return COLOR_YELLOW
        return COLOR_GREEN

    def format_countdown(self, iso_str):
        if not iso_str:
            return "--"
        if iso_str == "DISABLED":
            return "周耗尽"
        try:
            target = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            delta = target - datetime.datetime.now(datetime.timezone.utc)
            s = int(delta.total_seconds())
            if s <= 0:
                return "READY"
            h = s // 3600
            m = (s % 3600) // 60
            if h >= 24:
                return f"{h // 24}d{h % 24:02d}h"
            if h > 0:
                return f"{h}h{m:02d}m"
            return f"{m}m{s % 60:02d}s"
        except Exception:
            return "--"

    def calc_recommend_slot(self):
        best_slot = None
        best_score = -999.0
        for slot in ["1", "2", "3", "4", "5"]:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            if not os.path.exists(tpath):
                continue
            d = self.account_data.get(slot, {})
            cwk = d.get("cwk_pct", 0.0)
            c5 = d.get("c5h_pct", 0.0)
            score = -100.0 if (cwk <= 0.01 or d.get("c5h_reset") == "DISABLED") else c5
            if score > best_score:
                best_score = score
                best_slot = slot
        return best_slot

    def bind_new_slot(self, slot):
        if self.is_binding or self.switching_slot:
            return
        self.is_binding = True
        self.setup_ui()

        def worker():
            try:
                with self.keychain_lock:
                    script_path = os.path.join(VAULT_DIR, "login_account.py")
                    subprocess.run(["python3", script_path, str(slot)], check=False)
                    self.account_emails = self.load_emails()
                    self.active_slot = slot
                    self.apply_slot_credentials(slot)
            except Exception:
                pass
            finally:
                self.is_binding = False
                self.root.after(0, self.setup_ui)
                self.root.after(100, self.trigger_refresh)

        threading.Thread(target=worker, daemon=True).start()

    def switch_to_slot(self, slot):
        tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
        if not os.path.exists(tpath):
            self.bind_new_slot(slot)
            return
        if self.switching_slot:
            return

        self.active_slot = slot
        self.switching_slot = True
        self.setup_ui()

        def reload_app():
            try:
                with self.keychain_lock:
                    self.apply_slot_credentials(slot)
                    subprocess.run(["osascript", "-e", 'tell application "Antigravity" to quit'], check=False)
                    time.sleep(0.9)
                    subprocess.run(["pkill", "-9", "-f", "Antigravity.app/Contents/Resources/bin/language_server"], check=False)
                    subprocess.run(["pkill", "-9", "-f", "Antigravity.app/Contents/MacOS/Antigravity"], check=False)
                    time.sleep(0.5)
                    self.apply_slot_credentials(slot)
                    subprocess.run(["open", "-a", "Antigravity"], check=False)
                    time.sleep(2.5)
            finally:
                self.switching_slot = False
                self.root.after(0, self.setup_ui)
                self.root.after(500, self.trigger_refresh)

        threading.Thread(target=reload_app, daemon=True).start()

    def trigger_refresh(self):
        if self.is_fetching or self.is_binding or self.switching_slot:
            return
        self.is_fetching = True

        def worker():
            try:
                for s in ["1", "2", "3", "4", "5"]:
                    if self.switching_slot:
                        break
                    tok_path = os.path.join(VAULT_DIR, f"acc{s}.token")
                    if not os.path.exists(tok_path):
                        continue
                    with self.keychain_lock:
                        if self.switching_slot:
                            break
                        try:
                            with open(tok_path) as tf:
                                slot_token = tf.read().strip()
                            if not slot_token:
                                continue

                            subprocess.run(
                                ["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", slot_token],
                                check=True, stderr=subprocess.DEVNULL
                            )
                            cmd = ["/Users/weinliu/.local/bin/agy", "--output-format", "json", "-p", "/usage"]
                            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
                            if res.returncode == 0:
                                d = json.loads(res.stdout)
                                groups = d.get("command", {}).get("data", {}).get("groups", [])
                                if s not in self.account_data:
                                    self.account_data[s] = {}

                                for g in groups:
                                    gname = g.get("name", "").lower()
                                    for b in g.get("buckets", []):
                                        bid = b.get("id", "").lower()
                                        disabled = b.get("disabled", False)
                                        frac = b.get("remaining_fraction", 0.0) * 100.0
                                        rtime = b.get("reset_time", "")
                                        
                                        if "argon" in bid or "argon" in gname:
                                            self.account_data[s]["argon_pct"] = frac
                                            self.account_data[s]["argon_reset"] = rtime
                                        elif "3p" in bid or "claude" in gname:
                                            if "5h" in bid or "five" in bid:
                                                if disabled:
                                                    self.account_data[s]["c5h_pct"] = 0.0
                                                    self.account_data[s]["c5h_reset"] = "DISABLED"
                                                else:
                                                    self.account_data[s]["c5h_pct"] = frac
                                                    self.account_data[s]["c5h_reset"] = rtime
                                            else:
                                                self.account_data[s]["cwk_pct"] = frac
                                                self.account_data[s]["cwk_reset"] = rtime
                                        elif "gemini" in gname:
                                            if "5h" in bid or "five" in bid:
                                                self.account_data[s]["g5h_pct"] = frac
                                                self.account_data[s]["g5h_reset"] = rtime
                                            else:
                                                self.account_data[s]["gwk_pct"] = frac
                                                self.account_data[s]["gwk_reset"] = rtime
                                
                                if self.account_data[s].get("cwk_pct", 100.0) <= 0.01:
                                    self.account_data[s]["c5h_pct"] = 0.0
                                    self.account_data[s]["c5h_reset"] = "DISABLED"
                        except Exception:
                            pass
                        finally:
                            self.apply_slot_credentials(self.active_slot)
                self.save_cache()
            except Exception:
                pass
            self.last_fetch_ts = time.time()
            self.root.after(0, self.on_done)

        threading.Thread(target=worker, daemon=True).start()

    def on_done(self):
        self.is_fetching = False
        self.setup_ui()

    def tick(self):
        for lbl, rtime in getattr(self, "time_labels", []):
            lbl.configure(text=self.format_countdown(rtime))
            
        if not self.is_fetching and not self.is_binding and not self.switching_slot and (time.time() - self.last_fetch_ts >= 30):
            self.trigger_refresh()
            
        self.root.after(1000, self.tick)

if __name__ == "__main__":
    root = tk.Tk()
    app = MultiAccountHUD(root)
    root.mainloop()
