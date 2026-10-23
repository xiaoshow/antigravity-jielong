#!/usr/bin/env python3
"""
Antigravity Multi-Account Quota & Switcher HUD (1号 / 2号 / 3号 三号接龙版)
- 支持 1号、2号、3号 三账号同屏监控与一键热切
- 加上 Argon 6天倒计时 (如: 6d23h)
- 实时高频多账号轮询 (自动轮询所有已绑定槽位)
- 支持未绑定槽位直接点击悬浮窗一键调起浏览器授权绑定
"""

import sys, os, time, datetime, json, base64, subprocess, threading, glob, urllib.request
import tkinter as tk

VAULT_DIR = os.path.expanduser("~/.gemini/account_vault")
CACHE_FILE = os.path.expanduser("~/.gemini/account_vault/multi_quota_cache.json")
CLI_SETTINGS = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
APP_STORAGE = os.path.expanduser("~/Library/Application Support/Antigravity/app_storage.json")
os.makedirs(VAULT_DIR, exist_ok=True)

SLOTS = ["1", "2", "3"]

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
        
        self.detailed_mode = False
        
        self.w_compact, self.h_compact = 365, 435
        self.w_detail, self.h_detail = 310, 250
        
        screen_w = self.root.winfo_screenwidth()
        x = max(20, screen_w - self.w_compact - 25)
        y = 65
        self.root.geometry(f"{self.w_compact}x{self.h_compact}+{x}+{y}")
        
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.has_moved = False
        
        self.account_emails = self.load_emails()
        self.active_slot = self.detect_current_slot()
        self.active_model_group = self.detect_active_model_group()
        self.account_data = {}
        self.is_fetching = False
        self.is_binding = False
        self.last_fetch_ts = 0
        
        self.load_cache()
        self.setup_ui()
        
        self.trigger_refresh()
        self.root.after(1000, self.tick)

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

        try:
            if os.path.exists(CLI_SETTINGS):
                with open(CLI_SETTINGS) as f:
                    cfg = json.load(f)
                    m = str(cfg.get("model", "")).lower()
                    if "argon" in m: return "argon"
                    if any(k in m for k in ("claude", "opus", "sonnet")): return "claude"
                    if any(k in m for k in ("gemini", "flash")): return "gemini"
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
            # Fallback: query Google userinfo using access_token
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
        for slot in SLOTS:
            p = os.path.join(VAULT_DIR, f"acc{slot}.token")
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        emails[slot] = self.extract_email_from_token(f.read(), save_back_path=p)
                except Exception:
                    emails[slot] = ""
        return emails

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
        if not self.has_moved:
            self.toggle_mode()

    def toggle_mode(self):
        self.detailed_mode = not self.detailed_mode
        w = self.w_detail if self.detailed_mode else self.w_compact
        h = self.h_detail if self.detailed_mode else self.h_compact
        self.root.geometry(f"{w}x{h}")
        self.setup_ui()

    def bind_toggle(self, widget):
        widget.bind("<ButtonPress-1>", self.on_press)
        widget.bind("<B1-Motion>", self.on_motion)
        widget.bind("<ButtonRelease-1>", self.on_release)

    def detect_current_slot(self):
        try:
            cmd = ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"]
            cur = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
            cur_email = self.extract_email_from_token(cur)
            if cur_email:
                for slot in SLOTS:
                    if self.account_emails.get(slot, "").lower() == cur_email:
                        # Inject id_token_email and keep vault token fresh
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

        # 1. Header
        header = tk.Frame(self.root, bg="#11141a", height=34, padx=10)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)
        self.bind_toggle(header)

        mode_badge = "⤢ 完整单号" if not self.detailed_mode else "⤡ 三号矩阵"
        lbl_badge = tk.Label(header, text=mode_badge, fg=COLOR_CYAN, bg="#11141a", font=("SF Mono", 9, "bold"), cursor="pointinghand")
        lbl_badge.pack(side=tk.LEFT, padx=(0, 6), pady=6)
        self.bind_toggle(lbl_badge)

        best_slot = self.calc_recommend_slot()
        cur_d = self.account_data.get(self.active_slot, {})
        cur_cwk = cur_d.get("cwk_pct", 100.0)
        cur_c5 = cur_d.get("c5h_pct", 100.0)
        is_cur_dead = (cur_cwk <= 0.01 or cur_d.get("c5h_reset") == "DISABLED")

        if self.is_binding:
            rec_text = " · 正在浏览器授权绑定..."
        elif is_cur_dead:
            rec_text = f" · 建议切{best_slot}号!" if best_slot and best_slot != self.active_slot else " · Claude已耗尽"
        elif best_slot and best_slot != self.active_slot and (self.account_data.get(best_slot, {}).get("c5h_pct", 0) > cur_c5 + 30):
            rec_text = f" · 建议切{best_slot}号"
        elif cur_c5 < 15:
            rec_text = f" · 建议切{best_slot}号" if best_slot and best_slot != self.active_slot else " · 配额紧张"
        else:
            rec_text = " · 状态极佳"
        title_text = f"桌面 {self.active_slot}号{rec_text}" if not self.detailed_mode else f"{self.active_slot}号全部额度"

        lbl_title = tk.Label(header, text=title_text, fg=TEXT_PRIMARY, bg="#11141a", font=("SF Pro Text", 9, "bold"))
        lbl_title.pack(side=tk.LEFT, pady=6)
        self.bind_toggle(lbl_title)

        btn_ref = tk.Label(header, text="⟳", fg=TEXT_MUTED, bg="#11141a", font=("SF Mono", 12, "bold"), cursor="pointinghand")
        btn_ref.pack(side=tk.RIGHT, pady=4)
        btn_ref.bind("<Button-1>", lambda e: self.trigger_refresh())
        btn_ref.bind("<Enter>", lambda e: btn_ref.configure(fg=COLOR_CYAN))
        btn_ref.bind("<Leave>", lambda e: btn_ref.configure(fg=TEXT_MUTED))

        # 2. Body
        self.body = tk.Frame(self.root, bg=BG_COLOR, padx=8, pady=4)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.bind_toggle(self.body)

        if self.detailed_mode:
            self.render_detailed_view()
        else:
            self.render_dense_view()

        # 3. Bottom Switch Bar
        bottom_bar = tk.Frame(self.root, bg="#11141a", height=40, padx=10)
        bottom_bar.pack(fill=tk.X, side=tk.BOTTOM)
        bottom_bar.pack_propagate(False)
        self.bind_toggle(bottom_bar)

        lbl_switch = tk.Label(bottom_bar, text="切号接力", fg=TEXT_MUTED, bg="#11141a", font=("SF Pro Text", 9, "bold"))
        lbl_switch.pack(side=tk.LEFT, padx=(0, 8), pady=8)
        self.bind_toggle(lbl_switch)

        for slot in SLOTS:
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

            btn = tk.Label(bottom_bar, text=txt, fg=fg, bg=bg, font=("SF Mono", 9, "bold"), padx=12, pady=3, cursor="pointinghand", highlightbackground=border, highlightthickness=1)
            btn.pack(side=tk.LEFT, padx=3, pady=6)
            btn.bind("<Button-1>", lambda e, s=slot: self.switch_to_slot(s))

    def render_dense_view(self):
        for slot in SLOTS:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            has_token = os.path.exists(tpath)
            is_active = (slot == self.active_slot) and has_token
            data = self.account_data.get(slot, {})
            email = self.account_emails.get(slot, "")

            bg = CARD_ACTIVE_BG if is_active else CARD_BG
            border = BORDER_ACTIVE if is_active else BORDER_INACTIVE
            
            card = tk.Frame(self.body, bg=bg, highlightbackground=border, highlightthickness=1, padx=10, pady=5)
            card.pack(fill=tk.X, pady=3)
            self.bind_toggle(card)

            # 1. 头部
            header_row = tk.Frame(card, bg=bg)
            header_row.pack(fill=tk.X, pady=(0, 2))
            self.bind_toggle(header_row)

            l_slot = tk.Label(header_row, text=f"{slot}号", fg=TEXT_PRIMARY, bg=bg, font=("SF Pro Display", 10, "bold"))
            l_slot.pack(side=tk.LEFT)
            self.bind_toggle(l_slot)

            if not has_token:
                btn_bind = tk.Label(
                    header_row,
                    text="尚未绑定 · 点击此处登录绑定第3账号",
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
                self.bind_toggle(l_mail)

            if is_active:
                badge = tk.Label(header_row, text="● 活跃中", fg=COLOR_GREEN, bg=bg, font=("SF Pro Text", 8, "bold"))
                badge.pack(side=tk.RIGHT)
                self.bind_toggle(badge)

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
            self.bind_toggle(c_row)

            is_c_active = (self.active_model_group == "claude")
            c5h_col = self.get_color(c5h_pct)
            c_tag_col = COLOR_CYAN if is_c_active else TEXT_SECONDARY
            
            tk.Label(c_row, text="C-5h", fg=c_tag_col, bg=bg, font=("SF Mono", 8, "bold" if is_c_active else "normal")).pack(side=tk.LEFT)
            tk.Label(c_row, text=f"{c5h_pct:5.1f}%", fg=c5h_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
            
            if is_c_active:
                cv_c = tk.Canvas(c_row, bg=TRACK_COLOR, width=34, height=4, highlightthickness=0)
                cv_c.pack(side=tk.LEFT, padx=(0, 4))
                w_c = max(0, min(34, int(34 * (c5h_pct / 100.0))))
                if w_c > 0: cv_c.create_rectangle(0, 0, w_c, 4, fill=c5h_col, width=0)
                self.bind_toggle(cv_c)

            t1 = tk.Label(c_row, text=self.format_countdown(c5h_reset), fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
            t1.pack(side=tk.LEFT, padx=(0, 10))
            self.bind_toggle(t1)
            self.time_labels.append((t1, c5h_reset))

            cwk_col = self.get_color(cwk_pct)
            tk.Label(c_row, text="C-Wk", fg=TEXT_SECONDARY, bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT)
            tk.Label(c_row, text=f"{cwk_pct:5.1f}%", fg=cwk_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
            t2 = tk.Label(c_row, text=self.format_countdown(cwk_reset), fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
            t2.pack(side=tk.LEFT)
            self.bind_toggle(t2)
            self.time_labels.append((t2, cwk_reset))

            # 3. Gemini 行
            g_row = tk.Frame(card, bg=bg, pady=1)
            g_row.pack(fill=tk.X)
            self.bind_toggle(g_row)

            is_g_active = (self.active_model_group == "gemini")
            g5h_col = self.get_color(g5h_pct)
            g_tag_col = COLOR_CYAN if is_g_active else TEXT_SECONDARY
            tk.Label(g_row, text="G-5h", fg=g_tag_col, bg=bg, font=("SF Mono", 8, "bold" if is_g_active else "normal")).pack(side=tk.LEFT)
            tk.Label(g_row, text=f"{g5h_pct:5.1f}%", fg=g5h_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
            
            if is_g_active:
                cv_g = tk.Canvas(g_row, bg=TRACK_COLOR, width=34, height=4, highlightthickness=0)
                cv_g.pack(side=tk.LEFT, padx=(0, 4))
                w_g = max(0, min(34, int(34 * (g5h_pct / 100.0))))
                if w_g > 0: cv_g.create_rectangle(0, 0, w_g, 4, fill=g5h_col, width=0)
                self.bind_toggle(cv_g)

            t_g5 = tk.Label(g_row, text=self.format_countdown(g5h_reset), fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
            t_g5.pack(side=tk.LEFT, padx=(0, 10))
            self.bind_toggle(t_g5)
            self.time_labels.append((t_g5, g5h_reset))

            gwk_col = self.get_color(gwk_pct)
            tk.Label(g_row, text="G-Wk", fg=TEXT_SECONDARY, bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT)
            tk.Label(g_row, text=f"{gwk_pct:5.1f}%", fg=gwk_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
            t_gwk = tk.Label(g_row, text=self.format_countdown(gwk_reset), fg=TEXT_MUTED, bg=bg, font=("SF Mono", 8))
            t_gwk.pack(side=tk.LEFT)
            self.bind_toggle(t_gwk)
            self.time_labels.append((t_gwk, gwk_reset))

            # 4. Argon (阿公专用行 - 电量 + 进度条 + 6天复位倒计时)
            arg_row = tk.Frame(card, bg=bg, pady=1)
            arg_row.pack(fill=tk.X)
            self.bind_toggle(arg_row)

            is_a_active = (self.active_model_group == "argon")
            arg_col = self.get_color(argon_pct)
            a_tag_col = COLOR_CYAN if is_a_active else TEXT_SECONDARY
            tk.Label(arg_row, text="Argon", fg=a_tag_col, bg=bg, font=("SF Mono", 8, "bold" if is_a_active else "normal")).pack(side=tk.LEFT)
            tk.Label(arg_row, text=f"{argon_pct:5.1f}%", fg=arg_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 6))

            cv_a = tk.Canvas(arg_row, bg=TRACK_COLOR, width=54, height=4, highlightthickness=0)
            cv_a.pack(side=tk.LEFT, padx=(0, 8))
            w_a = max(0, min(54, int(54 * (argon_pct / 100.0))))
            if w_a > 0: cv_a.create_rectangle(0, 0, w_a, 4, fill=arg_col, width=0)
            self.bind_toggle(cv_a)

            tk.Label(arg_row, text="复位:", fg=TEXT_MUTED, bg=bg, font=("SF Pro Text", 8)).pack(side=tk.LEFT)
            t_arg = tk.Label(arg_row, text=self.format_countdown(argon_reset), fg=COLOR_CYAN if argon_pct < 20 else TEXT_MUTED, bg=bg, font=("SF Mono", 8, "bold"))
            t_arg.pack(side=tk.LEFT, padx=(2, 0))
            self.bind_toggle(t_arg)
            self.time_labels.append((t_arg, argon_reset))

    def render_detailed_view(self):
        cur_data = self.account_data.get(self.active_slot, {})
        email = self.account_emails.get(self.active_slot, "")

        if email:
            email_row = tk.Frame(self.body, bg=BG_COLOR, height=18)
            email_row.pack(fill=tk.X, pady=(0, 4))
            email_row.pack_propagate(False)
            self.bind_toggle(email_row)
            lbl_e = tk.Label(email_row, text=f"• 当前活跃账号: {email}", fg=TEXT_MUTED, bg=BG_COLOR, font=("SF Mono", 8))
            lbl_e.pack(side=tk.LEFT)
            self.bind_toggle(lbl_e)

        items = [
            ("G-5h", cur_data.get("g5h_pct", 100.0), cur_data.get("g5h_reset", "")),
            ("G-Wk", cur_data.get("gwk_pct", 100.0), cur_data.get("gwk_reset", "")),
            ("C-5h", cur_data.get("c5h_pct", 100.0), cur_data.get("c5h_reset", "")),
            ("C-Wk", cur_data.get("cwk_pct", 100.0), cur_data.get("cwk_reset", "")),
            ("Argon", cur_data.get("argon_pct", 100.0), cur_data.get("argon_reset", ""))
        ]

        for abbr, pct, rtime in items:
            color = self.get_color(pct)
            row = tk.Frame(self.body, bg=BG_COLOR, height=27)
            row.pack(fill=tk.X, pady=1)
            row.pack_propagate(False)
            self.bind_toggle(row)

            l1 = tk.Label(row, text=f"{abbr:<5}", fg="#cbd5e1", bg=BG_COLOR, font=("SF Mono", 9, "bold"), anchor="w", width=5)
            l1.pack(side=tk.LEFT)
            self.bind_toggle(l1)

            l2 = tk.Label(row, text=f"{pct:5.1f}%", fg=color, bg=BG_COLOR, font=("SF Mono", 9, "bold"), anchor="e", width=6)
            l2.pack(side=tk.LEFT, padx=(0, 6))
            self.bind_toggle(l2)

            canvas_w, canvas_h = 76, 5
            cv = tk.Canvas(row, bg=TRACK_COLOR, width=canvas_w, height=canvas_h, highlightthickness=0)
            cv.pack(side=tk.LEFT, padx=3)
            fill_w = max(0, min(canvas_w, int(canvas_w * (pct / 100.0))))
            if fill_w > 0:
                cv.create_rectangle(0, 0, fill_w, canvas_h, fill=color, width=0)
            self.bind_toggle(cv)

            lbl_time = tk.Label(row, text=self.format_countdown(rtime), fg=TEXT_MUTED, bg=BG_COLOR, font=("SF Mono", 8), anchor="e", width=7)
            lbl_time.pack(side=tk.RIGHT)
            self.bind_toggle(lbl_time)
            self.time_labels.append((lbl_time, rtime))

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
        for slot in SLOTS:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            if not os.path.exists(tpath):
                continue
            d = self.account_data.get(slot, {})
            cwk = d.get("cwk_pct", 0.0)
            c5 = d.get("c5h_pct", 0.0)
            if cwk <= 0.01 or d.get("c5h_reset") == "DISABLED":
                score = -100.0
            else:
                score = c5
            if score > best_score:
                best_score = score
                best_slot = slot
        return best_slot

    def bind_new_slot(self, slot):
        if self.is_binding:
            return
        self.is_binding = True
        self.setup_ui()

        def worker():
            try:
                script_path = os.path.join(VAULT_DIR, "login_account.py")
                subprocess.run(["python3", script_path, str(slot)], check=False)
                self.account_emails = self.load_emails()
                self.active_slot = slot
            except Exception:
                pass
            finally:
                self.is_binding = False
                self.root.after(0, self.setup_ui)
                self.root.after(100, self.trigger_refresh)

        threading.Thread(target=worker, daemon=True).start()

    def update_app_storage_email(self, email):
        if not email or not os.path.exists(APP_STORAGE):
            return
        try:
            with open(APP_STORAGE, "r", encoding="utf-8") as f:
                st = json.load(f)
            st["jetski.onboarding.lastLoginUsername"] = email
            with open(APP_STORAGE, "w", encoding="utf-8") as f:
                json.dump(st, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def switch_to_slot(self, slot):
        tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
        if not os.path.exists(tpath):
            self.bind_new_slot(slot)
            return

        with open(tpath) as f:
            raw = f.read().strip()

        subprocess.run(["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", raw], check=True)
        self.active_slot = slot
        email = self.account_emails.get(slot, "")
        self.update_app_storage_email(email)
        self.setup_ui()

        def reload_app():
            subprocess.run(["osascript", "-e", 'tell application "Antigravity" to quit'], check=False)
            time.sleep(0.8)
            subprocess.run(["pkill", "-f", "Antigravity.app/Contents/Resources/bin/language_server"], check=False)
            time.sleep(0.6)
            subprocess.run(["open", "-a", "Antigravity"], check=False)
            self.trigger_refresh()

        threading.Thread(target=reload_app, daemon=True).start()

    def trigger_refresh(self):
        if self.is_fetching or self.is_binding:
            return
        self.is_fetching = True

        def worker():
            try:
                cur_keychain_tok = None
                try:
                    cur_keychain_tok = subprocess.check_output(
                        ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                        stderr=subprocess.DEVNULL
                    ).decode().strip()
                except Exception:
                    pass

                for s in SLOTS:
                    tok_path = os.path.join(VAULT_DIR, f"acc{s}.token")
                    if not os.path.exists(tok_path):
                        continue
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
                        if cur_keychain_tok:
                            subprocess.run(
                                ["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", cur_keychain_tok],
                                check=False, stderr=subprocess.DEVNULL
                            )
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
            
        if not self.is_fetching and not self.is_binding and (time.time() - self.last_fetch_ts >= 25):
            self.trigger_refresh()
            
        self.root.after(1000, self.tick)

if __name__ == "__main__":
    root = tk.Tk()
    app = MultiAccountHUD(root)
    root.mainloop()
