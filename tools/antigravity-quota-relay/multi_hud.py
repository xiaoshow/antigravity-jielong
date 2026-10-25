#!/usr/bin/env python3
"""
Antigravity Multi-Account Quota & Switcher HUD (可折叠多账号接龙版)
- 支持 1~5+ 个多账号接龙管理
- 支持【全局折叠】与【单卡折叠】：多账号时一键折叠为超薄矩阵（每账号仅占一行，极度轻巧省空间）
- 加上 Argon 6天倒计时 (如: 6d23h)
- 互斥锁保护，彻底杜绝切号与后台轮询冲突
- 支持未绑定槽位点击直接调起无痕模式选择绑定
"""

import hashlib, sys, os, time, datetime, json, base64, subprocess, threading, glob, urllib.request
import tkinter as tk

MAX_SLOTS = 8
ROTATING_FILE = os.path.expanduser("~/.gemini/account_vault/.rotating")
ALL_SLOTS = [str(i) for i in range(1, MAX_SLOTS + 1)]
VAULT_DIR = os.path.expanduser("~/.gemini/account_vault")
CACHE_FILE = os.path.expanduser("~/.gemini/account_vault/multi_quota_cache.json")
CLI_SETTINGS = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
CLI_TOKEN_FILE = os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token")
STANDALONE_TOKEN_FILE = os.path.expanduser("~/.gemini/jetski-standalone-oauth-token")
APP_STORAGE = os.path.expanduser("~/Library/Application Support/Antigravity/app_storage.json")
LOCK_FILE = os.path.join(VAULT_DIR, ".login_lock")
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
COLOR_FULL = "#34d399"   # Pro（有 Argon 额度）
COLOR_LITE = "#94a3b8"   # Free（无 Argon、无 5h 额度）：灰
BORDER_LITE = "#2c333f"
COLOR_PLUS = "#38bdf8"   # Plus（无 Argon，但 Gemini 有 5h 额度）
BORDER_PLUS = "#1c3d5a"
BORDER_FULL = "#1d4a3a"
# 每档一个色调，整块卡片的字、边框、底色都用同一色系
# (底色, 底色-活跃, 边框, 边框-活跃, 主色, 标签色, 弱化色)
TIER_PALETTE = {
    "Pro":  ("#1a1608", "#241d0a", "#4a3a12", "#fbbf24", "#fbbf24", "#d9ad3c", "#8f7a3a"),   # 金黄
    "Plus": ("#0e1822", "#122030", "#1c3d5a", "#38bdf8", "#38bdf8", "#6fb9dd", "#4f7f9a"),   # 天蓝
    "Free": ("#15181e", "#1a1e27", "#2c333f", "#94a3b8", "#cbd5e1", "#94a3b8", "#64748b"),   # 灰
}


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
        
        self.w_normal, self.h_normal = 276, 440
        self.w_collapsed, self.h_collapsed = 276, 240
        
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
        self.active_slot = self.load_active_slot() or self.detect_current_slot()
        if os.path.exists(ROTATING_FILE):
            # 上一次在借用各号凭据拉额度的途中被杀掉，钥匙串里留的是中途的号，不可信：按记住的号还原
            self.apply_slot_credentials(self.active_slot)
            try:
                os.remove(ROTATING_FILE)
            except OSError:
                pass
        self.active_model_group = self.detect_active_model_group()
        self.account_data = {}
        
        self.load_cache()
        self.setup_ui()
        
        self.trigger_refresh()
        self.root.after(1000, self.tick)

    def get_available_slots(self):
        """动态检测已配置的账号槽位，并追加一个待绑定的新槽位（最多支持 8 个号）"""
        slots = []
        for i in range(1, MAX_SLOTS + 1):
            p = os.path.join(VAULT_DIR, f"acc{i}.token")
            if os.path.exists(p):
                slots.append(str(i))
        next_slot = str(len(slots) + 1)
        if int(next_slot) <= MAX_SLOTS:
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
        for slot in ALL_SLOTS:
            p = os.path.join(VAULT_DIR, f"acc{slot}.token")
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        emails[slot] = self.extract_email_from_token(f.read(), save_back_path=p)
                except Exception:
                    emails[slot] = ""
        return emails


    @staticmethod
    def _refresh_hash(blob):
        try:
            d = json.loads(base64.b64decode(blob.strip().replace("go-keyring-base64:", "")))
            rt = d.get("token", {}).get("refresh_token", "")
            return hashlib.sha1(rt.encode()).hexdigest() if rt else ""
        except Exception:
            return ""

    def read_live_blob(self):
        try:
            return subprocess.check_output(
                ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            return ""

    def write_live_blob(self, blob):
        """把一份完整凭据写回钥匙串和 CLI/standalone 令牌文件"""
        subprocess.run(["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", blob],
                       check=False, stderr=subprocess.DEVNULL)
        try:
            dec = base64.b64decode(blob.replace("go-keyring-base64:", "")).decode("utf-8")
            for path in (STANDALONE_TOKEN_FILE, CLI_TOKEN_FILE):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(dec)
        except Exception:
            pass

    def adopt_live(self):
        """以钥匙串里正在用的凭据为准判定当前账号（异步跟随 CLI/App 里的登录、切换），
        而不是把保险库推过去覆盖。返回需要在刷新结束后还原的快照。"""
        live = self.read_live_blob()
        if not live:
            return ""
        h = self._refresh_hash(live)
        match = None
        if h:
            for slot in ALL_SLOTS:
                try:
                    with open(os.path.join(VAULT_DIR, f"acc{slot}.token")) as f:
                        if self._refresh_hash(f.read()) == h:
                            match = slot
                            break
                except OSError:
                    pass
        if not match:
            # 新登录/换了号：按邮箱找槽位
            email = self.extract_email_from_token(live)
            if email:
                for slot, e in self.account_emails.items():
                    if e.lower() == email:
                        match = slot
                        break
        if match:
            # 实际在用的凭据比保险库新（刷新了 access token，或重新登录换了 refresh token）：写回保险库，并保留缓存的邮箱
            try:
                tpath = os.path.join(VAULT_DIR, f"acc{match}.token")
                with open(tpath) as f:
                    old = f.read().strip()
                if old != live:
                    new_d = json.loads(base64.b64decode(live.replace("go-keyring-base64:", "")))
                    try:
                        old_d = json.loads(base64.b64decode(old.replace("go-keyring-base64:", "")))
                    except Exception:
                        old_d = {}
                    em = new_d.get("id_token_email") or old_d.get("id_token_email") or self.account_emails.get(match, "")
                    if em:
                        new_d["id_token_email"] = em
                    with open(tpath, "w") as f:
                        f.write("go-keyring-base64:" + base64.b64encode(json.dumps(new_d).encode("utf-8")).decode("utf-8"))
                    self.account_emails[match] = em
            except (OSError, ValueError):
                pass
            if match != self.active_slot:
                self.active_slot = match
                self.save_active_slot(match)
                self.root.after(0, self.setup_ui)
        return live

    def emails_changed(self):
        sig = tuple(os.path.getmtime(os.path.join(VAULT_DIR, f"acc{n}.token"))
                    if os.path.exists(os.path.join(VAULT_DIR, f"acc{n}.token")) else 0 for n in ALL_SLOTS)
        if getattr(self, "_mt_sig", sig) != sig:
            self._mt_sig = sig
            return True
        self._mt_sig = sig
        return False

    def apply_slot_credentials(self, slot):
        if os.path.exists(LOCK_FILE):
            return False
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
            with open(CLI_TOKEN_FILE, "w", encoding="utf-8") as cf:
                cf.write(decoded_json)
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
            h = 42 + (len(slots) * 34) + 48 + (34 if len(slots) > 7 else 0)
            w = 276
        else:
            # 展开模式：根据账号数量自适应高度
            h = 42 + (len(slots) * 74) + 48 + (34 if len(slots) > 7 else 0)
            w = 276
        self.root.geometry(f"{w}x{h}")

    def bind_drag(self, widget):
        widget.bind("<ButtonPress-1>", self.on_press)
        widget.bind("<B1-Motion>", self.on_motion)
        widget.bind("<ButtonRelease-1>", self.on_release)

    ACTIVE_FILE = os.path.join(os.path.expanduser("~/.gemini/account_vault"), ".active_slot")

    def load_active_slot(self):
        """用户上一次明确选择/绑定的号。优先于钥匙串检测：每轮拉额度会临时轮换钥匙串里的凭据，
        在轮换途中启动（或重启）会把轮换到的号误判成当前号，之后又被它「还原」覆盖用户真正在用的号。"""
        try:
            v = open(self.ACTIVE_FILE).read().strip()
            return v if v in tuple(ALL_SLOTS) else None
        except OSError:
            return None

    def save_active_slot(self, slot):
        try:
            with open(self.ACTIVE_FILE, "w") as f:
                f.write(str(slot))
        except OSError:
            pass

    def detect_current_slot(self):
        if os.path.exists(LOCK_FILE):
            return self.active_slot or "1"
        try:
            cmd = ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"]
            cur = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
            cur_email = self.extract_email_from_token(cur)
            if cur_email:
                for slot in ALL_SLOTS:
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

        cur_tier = self.tier_of(cur_d)
        if self.switching_slot:
            rec_text = f" · 正在切换至 {self.active_slot}号..."
        elif self.is_binding:
            rec_text = " · 正在浏览器授权..."
        elif not self.has_claude(cur_d):
            cur_g5 = cur_d.get("g5h_pct")
            cur_gwk = cur_d.get("gwk_pct", 100.0)
            if cur_gwk <= 0.01:
                rec_text = " · 配额耗尽"
            elif cur_g5 is not None and cur_g5 < 15:
                rec_text = " · 配额紧张"
            else:
                rec_text = " · 状态极佳"
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

        # 3. Bottom Switch Bar（每行最多 4 个按钮，超出另起一行；点一次变「确认?」，再点一次才切换）
        PER_ROW = 6
        bound = [x for x in slots if os.path.exists(os.path.join(VAULT_DIR, f"acc{x}.token"))]  # 未绑定的由占位卡片负责，不占按钮位
        chunks = [bound[i:i + PER_ROW] for i in range(0, len(bound), PER_ROW)]
        for ci in reversed(range(len(chunks))):  # side=BOTTOM 先 pack 的在最下面，所以倒着来
            bar = tk.Frame(self.root, bg="#11141a", height=34, padx=4)
            bar.pack(fill=tk.X, side=tk.BOTTOM)
            bar.pack_propagate(False)
            self.bind_drag(bar)
            for slot in chunks[ci]:
                has_tok = os.path.exists(os.path.join(VAULT_DIR, f"acc{slot}.token"))
                is_active = (slot == self.active_slot) and has_tok
                bg = "#1e293b" if is_active else "#151922"
                if not has_tok:
                    fg = COLOR_CYAN
                    txt = f"+No.{slot}"
                else:
                    fg = COLOR_GREEN if is_active else TEXT_SECONDARY
                    txt = f"No.{slot}✓" if is_active else f"No.{slot}"
                border = "#3b82f6" if is_active else BORDER_INACTIVE

                btn = tk.Label(bar, text=txt, fg=fg, bg=bg, font=("SF Mono", 9, "bold"), padx=2, pady=2, cursor="pointinghand", highlightbackground=border, highlightthickness=1)
                btn.pack(side=tk.LEFT, padx=1, pady=4)
                btn.bind("<Button-1>", lambda e, s=slot, b=btn, t=txt, f=fg, bg0=bg: self.confirm_switch(s, b, t, f, bg0))

        self.root.after_idle(self.fit_height)

    def render_collapsed_matrix(self, slots):
        """紧凑折叠视图：每账号一行，超薄卡片，支持 5 个账号仅占微小屏幕"""
        for slot in slots:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            has_token = os.path.exists(tpath)
            is_active = (slot == self.active_slot) and has_token
            data = self.account_data.get(slot, {})
            email = self.account_emails.get(slot, "")

            bg, border, accent = self.card_colors(data, has_token, is_active)

            row = tk.Frame(self.body, bg=bg, highlightbackground=border, highlightthickness=1, padx=8, pady=3)
            row.pack(fill=tk.X, pady=2)
            self.bind_drag(row)

            tier = self.tier_of(data) if has_token else None
            l_slot = tk.Label(row, text=f"{slot}号", fg=accent, bg=bg, font=("SF Mono", 9, "bold"))
            l_slot.pack(side=tk.LEFT)
            self.bind_drag(l_slot)

            if not has_token:
                btn_b = tk.Label(row, text="+ 点击绑定此账号", fg=COLOR_CYAN, bg=bg, font=("SF Pro Text", 8, "bold"), cursor="pointinghand")
                btn_b.pack(side=tk.LEFT, padx=(8, 0))
                btn_b.bind("<Button-1>", lambda e, s=slot: self.bind_new_slot(s))
                continue

            # 简写 C-5h（Pro / Plus 显示）
            if self.has_claude(data):
                c5 = data.get("c5h_pct")
                c5_col = self.pct_color(c5)
                tk.Label(row, text="C:", fg=self._th["muted"], bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT, padx=(6, 1))
                c5_text = "周耗" if data.get("c5h_reset") == "DISABLED" else ("  --" if c5 is None else f"{c5:4.0f}%")
                tk.Label(row, text=c5_text, fg=c5_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)

            # 简写 G-5h
            g5 = data.get("g5h_pct")
            if g5 is not None:
                tk.Label(row, text="G:", fg=self._th["muted"], bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT, padx=(6, 1))
                tk.Label(row, text=f"{g5:4.0f}%", fg=self.pct_color(g5), bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)
            else:   # 没有 5 小时额度（Free）：折叠行显示周额度
                gw = data.get("gwk_pct")
                tk.Label(row, text="G周:", fg=self._th["muted"], bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT, padx=(6, 1))
                tk.Label(row, text="  --" if gw is None else f"{gw:4.0f}%", fg=self.pct_color(gw), bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)

            # 简写 Argon
            arg = data.get("argon_pct")
            arg_reset = data.get("argon_reset", "")
            arg_col = self.pct_color(arg)
            if arg is not None:
                tk.Label(row, text="Arg:", fg=self._th["muted"], bg=bg, font=("SF Mono", 8)).pack(side=tk.LEFT, padx=(6, 1))
                tk.Label(row, text=f"{arg:4.0f}%", fg=arg_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)

            # 倒计时: 有 Argon 优先显示 Argon 倒计时；无 Argon 显示 G-Wk / C-Wk 周倒计时
            main_reset = arg_reset or data.get("gwk_reset") or data.get("cwk_reset")
            if main_reset:
                t_cnt = tk.Label(row, text=self.format_countdown(main_reset), fg=self._th["muted"], bg=bg, font=("SF Mono", 8))
                t_cnt.pack(side=tk.RIGHT)
                self.bind_drag(t_cnt)
                self.time_labels.append((t_cnt, main_reset))

    def render_expanded_cards(self, slots):
        """展开模式：完整卡片、进度条与详细倒计时"""
        for slot in slots:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            has_token = os.path.exists(tpath)
            is_active = (slot == self.active_slot) and has_token
            data = self.account_data.get(slot, {})
            email = self.account_emails.get(slot, "")

            bg, border, accent = self.card_colors(data, has_token, is_active)
            tier = self.tier_of(data) if has_token else None
            card = tk.Frame(self.body, bg=bg, highlightbackground=border, highlightthickness=1, padx=7, pady=2)
            card.pack(fill=tk.X, pady=2)
            self.bind_drag(card)

            # 1. 头部
            header_row = tk.Frame(card, bg=bg)
            header_row.pack(fill=tk.X, pady=(0, 0))
            self.bind_drag(header_row)

            l_slot = tk.Label(header_row, text=f"{slot}号", fg=accent, bg=bg, font=("SF Pro Display", 10, "bold"))
            l_slot.pack(side=tk.LEFT)
            self.bind_drag(l_slot)
            if tier:
                tname = self.tier_name(data)
                l_tier = tk.Label(header_row, text=tname,
                                  fg=accent, bg=bg, font=("SF Pro Text", 8, "bold"))
                l_tier.pack(side=tk.LEFT, padx=(4, 0))
                self.bind_drag(l_tier)

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
                l_mail = tk.Label(header_row, text=mail_short, fg=self._th["muted"], bg=bg, font=("SF Mono", 8))
                l_mail.pack(side=tk.LEFT, padx=(6, 0))
                self.bind_drag(l_mail)

            if is_active:
                l_slot.configure(text=f"▸{slot}号")

            c5h_pct = data.get("c5h_pct")
            c5h_reset = data.get("c5h_reset", "")
            cwk_pct = data.get("cwk_pct")
            cwk_reset = data.get("cwk_reset", "")
            
            g5h_pct = data.get("g5h_pct")
            g5h_reset = data.get("g5h_reset", "")
            gwk_pct = data.get("gwk_pct")
            gwk_reset = data.get("gwk_reset", "")
            
            argon_pct = data.get("argon_pct")
            argon_reset = data.get("argon_reset", "")

            # 2. Claude 行（仅满血账号显示，残血账号隐藏）
            if self.has_claude(data) and (cwk_pct is not None or c5h_pct is not None):
                c_row = tk.Frame(card, bg=bg, pady=1)
                c_row.pack(fill=tk.X)
                self.bind_drag(c_row)

                if c5h_pct is not None:
                    c5h_col = self.pct_color(c5h_pct)
                    c5h_txt = f"{c5h_pct:3.0f}%"
                    tk.Label(c_row, text="C-5h", fg=self._th["label"], bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)
                    tk.Label(c_row, text=c5h_txt, fg=c5h_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
                    
                    t1_txt = self.format_countdown(c5h_reset)
                    t1 = tk.Label(c_row, text=t1_txt, fg=self._th["muted"], bg=bg, font=("SF Mono", 8))
                    t1.pack(side=tk.LEFT, padx=(0, 10))
                    self.bind_drag(t1)
                    if c5h_reset:
                        self.time_labels.append((t1, c5h_reset))

                cwk_col = self.pct_color(cwk_pct)
                if cwk_pct is None:
                    cwk_txt = "未开通"
                    cwk_col = self._th["muted"]
                else:
                    cwk_txt = f"{cwk_pct:3.0f}%"
                tk.Label(c_row, text="C-Wk", fg=self._th["label"], bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)
                tk.Label(c_row, text=cwk_txt, fg=cwk_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
                t2_txt = "--" if not cwk_reset else self.format_countdown(cwk_reset)
                t2 = tk.Label(c_row, text=t2_txt, fg=self._th["muted"], bg=bg, font=("SF Mono", 8))
                t2.pack(side=tk.LEFT)
                self.bind_drag(t2)
                if cwk_reset:
                    self.time_labels.append((t2, cwk_reset))

            # 3. Gemini 行（残血账号仅保留此行：G-5h 与 G-Wk，2号和4号结构完全一致）
            g_row = tk.Frame(card, bg=bg, pady=1)
            g_row.pack(fill=tk.X)
            self.bind_drag(g_row)

            if g5h_pct is not None:  # 没有 5 小时额度的账号（Free）不显示 G-5h
                g5h_col = self.pct_color(g5h_pct)
                g5h_txt = f"{g5h_pct:3.0f}%"
                tk.Label(g_row, text="G-5h", fg=self._th["label"], bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)
                tk.Label(g_row, text=g5h_txt, fg=g5h_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))

                t_g5_txt = "--" if not g5h_reset else self.format_countdown(g5h_reset)
                t_g5 = tk.Label(g_row, text=t_g5_txt, fg=self._th["muted"], bg=bg, font=("SF Mono", 8))
                t_g5.pack(side=tk.LEFT, padx=(0, 10))
                self.bind_drag(t_g5)
                if g5h_reset:
                    self.time_labels.append((t_g5, g5h_reset))

            gwk_col = self.pct_color(gwk_pct)
            gwk_txt = "  --" if gwk_pct is None else f"{gwk_pct:3.0f}%"
            tk.Label(g_row, text="G-Wk", fg=self._th["label"], bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)
            tk.Label(g_row, text=gwk_txt, fg=gwk_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 3))
            t_gwk = tk.Label(g_row, text=self.format_countdown(gwk_reset), fg=self._th["muted"], bg=bg, font=("SF Mono", 8))
            t_gwk.pack(side=tk.LEFT)
            self.bind_drag(t_gwk)
            self.time_labels.append((t_gwk, gwk_reset))

            # 4. Argon
            if argon_pct is not None:  # 不支持 Argon 的账号不显示该行
                arg_row = tk.Frame(card, bg=bg, pady=1)
                arg_row.pack(fill=tk.X)
                self.bind_drag(arg_row)

                arg_col = self.pct_color(argon_pct)
                arg_txt = "不支持" if argon_pct is None else f"{argon_pct:3.0f}%"
                tk.Label(arg_row, text="Argon", fg=self._th["label"], bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT)
                tk.Label(arg_row, text=arg_txt, fg=arg_col, bg=bg, font=("SF Mono", 8, "bold")).pack(side=tk.LEFT, padx=(3, 14))

                tk.Label(arg_row, text="复位:", fg=self._th["muted"], bg=bg, font=("SF Pro Text", 8)).pack(side=tk.LEFT)
                t_arg_txt = "--" if argon_pct is None else self.format_countdown(argon_reset)
                t_arg = tk.Label(arg_row, text=t_arg_txt, fg=self._th["hi"] if (argon_pct and argon_pct < 20) else self._th["muted"], bg=bg, font=("SF Mono", 8, "bold"))
                t_arg.pack(side=tk.LEFT, padx=(2, 0))
                self.bind_drag(t_arg)
                if argon_pct is not None:
                    self.time_labels.append((t_arg, argon_reset))

    def tier_name(self, data):
        """Pro=有 Argon（1/3号）；Plus=无 Argon 但 Gemini 有 5h 复位（4号）；Free=只有 Gemini 周额度（2号）；无数据返回 None"""
        if not data:
            return None
        if data.get("argon_pct") is not None:
            return "Pro"
        return "Plus" if data.get("g5h_pct") is not None else "Free"

    def card_colors(self, data, has_token, is_active):
        """按档位给整块卡片配色：返回 (底色, 边框, 主色)，并把本卡的字色存到 self._th"""
        name = self.tier_name(data) if has_token else None
        if not name:
            self._th = {"label": TEXT_SECONDARY, "muted": TEXT_MUTED, "hi": COLOR_CYAN, "ok": COLOR_GREEN}
            return CARD_BG, BORDER_INACTIVE, TEXT_PRIMARY
        bg, bg_a, bd, bd_a, accent, label, muted = TIER_PALETTE[name]
        self._th = {"label": label, "muted": muted, "hi": accent, "ok": accent}
        return (bg_a if is_active else bg), (bd_a if is_active else bd), accent

    def pct_color(self, pct):
        """百分比：健康用本档主色，偏低才用橙/红提醒"""
        if pct is None:
            return self._th["muted"]
        if pct < 15:
            return COLOR_RED
        if pct < 45:
            return "#fb923c"
        return self._th["ok"]

    def has_claude(self, data):
        """是否显示/计入 Claude 额度：Pro 有；Plus（有 Gemini 5h）也有 Claude 额度（2026-10-25 用户确认）；Free 没有"""
        return self.tier_of(data) != "lite" or self.tier_name(data) == "Plus"

    def tier_of(self, data):
        """满血=有 Argon 额度；残血=没有；尚无数据返回 None"""
        if not data:
            return None
        return "full" if data.get("argon_pct") is not None else "lite"

    def get_color(self, pct):
        if pct is None:
            return TEXT_MUTED
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
            if s >= 86400:
                return f"{s / 86400:.1f}d"
            if s >= 3600:
                return f"{s / 3600:.1f}h"
            if s >= 60:
                return f"{s // 60}m"
            return f"{s}s"
        except Exception:
            return "--"

    def calc_recommend_slot(self):
        best_slot = None
        best_score = -999.0
        for slot in ALL_SLOTS:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            if not os.path.exists(tpath):
                continue
            d = self.account_data.get(slot, {})
            cwk = d.get("cwk_pct") or 0.0
            c5 = d.get("c5h_pct") or 0.0
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
                    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "login_account.py")
                    subprocess.run(["python3", script_path, str(slot)], check=False)
                    self.account_emails = self.load_emails()
                    self.active_slot = slot
                    self.save_active_slot(slot)
                    self.apply_slot_credentials(slot)
            except Exception:
                pass
            finally:
                self.is_binding = False
                self.root.after(0, self.setup_ui)
                self.root.after(100, self.trigger_refresh)

        threading.Thread(target=worker, daemon=True).start()

    def confirm_switch(self, slot, btn, txt, fg, bg):
        """第一次点击只把按钮变成「确认?」，4 秒内在同一位置再点一次才真正切换"""
        now = time.time()
        pend = getattr(self, "_pending_switch", None)
        if pend and pend[0] == slot and now - pend[1] < 4:
            self._pending_switch = None
            self.switch_to_slot(slot)
            return
        self._pending_switch = (slot, now)
        btn.configure(text="确认?", fg="#fbbf24", bg="#3b2a0a")

        def revert():
            cur = getattr(self, "_pending_switch", None)
            if cur and cur[0] == slot and cur[1] == now:
                self._pending_switch = None
            try:
                btn.configure(text=txt, fg=fg, bg=bg)
            except Exception:
                pass
        self.root.after(4000, revert)

    def switch_to_slot(self, slot):
        tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
        if not os.path.exists(tpath):
            self.bind_new_slot(slot)
            return
        if self.switching_slot:
            return

        self.active_slot = slot
        self.save_active_slot(slot)
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
        if self.is_fetching or self.is_binding or self.switching_slot or os.path.exists(LOCK_FILE):
            return
        # 账号槽位增减（例如在终端里新绑定了账号）时自动重建界面，无需重启
        sig = tuple(os.path.exists(os.path.join(VAULT_DIR, f"acc{n}.token")) for n in ALL_SLOTS)
        if getattr(self, "_tok_sig", sig) != sig or self.emails_changed():
            self.account_emails = self.load_emails()
            self.root.after(0, self.setup_ui)
        self._tok_sig = sig
        self.is_fetching = True

        def worker():
            live_snapshot = ""
            try:
                with self.keychain_lock:
                    live_snapshot = self.adopt_live()
                    open(ROTATING_FILE, "w").close()
                for s in ALL_SLOTS:
                    if self.switching_slot or os.path.exists(LOCK_FILE):
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

                            # 同步更新 standalone 与 cli token，确保 agy 真实拉取该槽位
                            try:
                                raw_tok = slot_token.replace("go-keyring-base64:", "")
                                dec_tok = base64.b64decode(raw_tok).decode("utf-8")
                                with open(STANDALONE_TOKEN_FILE, "w", encoding="utf-8") as sf:
                                    sf.write(dec_tok)
                                with open(CLI_TOKEN_FILE, "w", encoding="utf-8") as cf:
                                    cf.write(dec_tok)
                            except Exception:
                                pass

                            subprocess.run(
                                ["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", slot_token],
                                check=True, stderr=subprocess.DEVNULL
                            )
                            cmd = ["/Users/weinliu/.local/bin/agy", "--output-format", "json", "-p", "/usage"]
                            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
                            if res.returncode == 0:
                                d = json.loads(res.stdout)
                                groups = d.get("command", {}).get("data", {}).get("groups", [])
                                # 每次拉取前重置槽位数据，避免不同套餐账号残留脏缓存
                                slot_dict = {
                                    "c5h_pct": None,
                                    "c5h_reset": "",
                                    "cwk_pct": None,
                                    "cwk_reset": "",
                                    "g5h_pct": None,
                                    "g5h_reset": "",
                                    "gwk_pct": None,
                                    "gwk_reset": "",
                                    "argon_pct": None,
                                    "argon_reset": ""
                                }

                                for g in groups:
                                    gname = g.get("name", "").lower()
                                    for b in g.get("buckets", []):
                                        bid = b.get("id", "").lower()
                                        disabled = b.get("disabled", False)
                                        frac = b.get("remaining_fraction", 0.0) * 100.0
                                        rtime = b.get("reset_time", "")
                                        
                                        if "argon" in bid or "argon" in gname:
                                            slot_dict["argon_pct"] = frac
                                            slot_dict["argon_reset"] = rtime
                                        elif "3p" in bid or "claude" in gname:
                                            if "5h" in bid or "five" in bid:
                                                if disabled:
                                                    slot_dict["c5h_pct"] = 0.0
                                                    slot_dict["c5h_reset"] = "DISABLED"
                                                else:
                                                    slot_dict["c5h_pct"] = frac
                                                    slot_dict["c5h_reset"] = rtime
                                            else:
                                                slot_dict["cwk_pct"] = frac
                                                slot_dict["cwk_reset"] = rtime
                                        elif "gemini" in gname:
                                            if "5h" in bid or "five" in bid:
                                                slot_dict["g5h_pct"] = frac
                                                slot_dict["g5h_reset"] = rtime
                                            else:
                                                slot_dict["gwk_pct"] = frac
                                                slot_dict["gwk_reset"] = rtime
                                
                                if slot_dict.get("cwk_pct") is not None and slot_dict["cwk_pct"] <= 0.01:
                                    slot_dict["c5h_pct"] = 0.0
                                    slot_dict["c5h_reset"] = "DISABLED"

                                self.account_data[s] = slot_dict
                        except Exception:
                            pass
                        finally:
                            if live_snapshot:
                                self.write_live_blob(live_snapshot)
                            else:
                                self.apply_slot_credentials(self.active_slot)
                self.save_cache()
            except Exception:
                pass
            try:
                os.remove(ROTATING_FILE)
            except OSError:
                pass
            self.last_fetch_ts = time.time()
            self.root.after(0, self.on_done)

        threading.Thread(target=worker, daemon=True).start()

    def on_done(self):
        self.is_fetching = False
        self.setup_ui()

    def fit_height(self):
        """按内容实际高度撑开窗口，避免账号变多时底部被截断"""
        try:
            self.root.update_idletasks()
            h = self.root.winfo_reqheight()
            w = self.root.winfo_width()
            if h > 100 and abs(h - self.root.winfo_height()) > 2:
                self.root.geometry(f"{w}x{h}")
        except Exception:
            pass

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
