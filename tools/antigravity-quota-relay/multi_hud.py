#!/usr/bin/env python3
"""
Antigravity Multi-Account Quota & Switcher HUD (可折叠多账号接龙版)
- 支持 1~5+ 个多账号接龙管理
- 支持【全局折叠】与【单卡折叠】：多账号时一键折叠为超薄矩阵（每账号仅占一行，极度轻巧省空间）
- 加上 Argon 6天倒计时 (如: 6d23h)
- 互斥锁保护，彻底杜绝切号与后台轮询冲突
- 支持未绑定槽位点击直接调起无痕模式选择绑定
"""

import hashlib, sys, os, re, time, datetime, json, base64, subprocess, threading, glob, urllib.request
import tkinter as tk

MAX_SLOTS = 20
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
# 模型各自的企业色：Claude 陶土橙、GPT(OpenAI) 绿、Argon(Google) 蓝
BRAND_COLORS = {"Claude": "#d97757", "GPT": "#10a37f", "Argon": "#4285f4"}
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
    def __init__(self, root, parent=None, read_only=False):
        self.read_only = read_only
        self.root = root
        self.parent = parent
        self.is_standalone = (parent is None)
        self.container = parent if parent is not None else root
        
        if self.is_standalone:
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
        else:
            self.collapsed_mode = False
            self.w_normal, self.h_normal = 276, 440
            self.w_collapsed, self.h_collapsed = 276, 240
        
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
        
        if not self.read_only:
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

    def recent_agy_refresh(self, within=900):
        """终端里常驻的 agy 会话每小时刷新一次令牌，并把它内存里那个号的凭据写回钥匙串（2026-10-26 实测：整点后 20-45 秒钥匙串变回 1 号）。
        刚刷新过就说明钥匙串的变化是回写，不是用户切号。"""
        try:
            logs = glob.glob(os.path.expanduser("~/.gemini/antigravity-cli/log/cli-*.log"))
            if not logs:
                return False
            with open(max(logs, key=os.path.getmtime), "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 60000))
                lines = [l for l in f.read().decode("utf-8", "ignore").splitlines() if "token refreshed" in l]
            if not lines:
                return False
            m = re.match(r"I(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})", lines[-1])
            if not m:
                return False
            mo, dd, hh, mi, ss = map(int, m.groups())
            ts = datetime.datetime(datetime.datetime.now().year, mo, dd, hh, mi, ss).timestamp()
            return 0 <= time.time() - ts <= within
        except Exception:
            return False

    def adopt_live(self):
        """以系统中实际生效的凭据/状态为准判定当前账号（异步跟随 CLI / 桌面 App / 钥匙串），
        返回当前活跃账号的凭据快照。"""
        match = None

        # 1. 优先检查 .active_slot 文件（由 switch.py / agy-switch 显式切换写入）
        try:
            if os.path.exists(self.ACTIVE_FILE):
                v = open(self.ACTIVE_FILE).read().strip()
                if v in ALL_SLOTS and v != self.active_slot and os.path.exists(os.path.join(VAULT_DIR, f"acc{v}.token")):
                    match = v
        except Exception:
            pass

        # 不看 jetski-standalone-oauth-token：它跟着钥匙串里被借用的凭据一起变（轮换时依次变成 1、2、3…），
        # 刷新开始前读到的往往是上一轮借用的残留，会把刚切好的 8 号改回 1/2（2026-10-26 实测）。

        # 2. 检查钥匙串凭据
        live = self.read_live_blob()
        if not match and live:
            h = self._refresh_hash(live)
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
                email = self.extract_email_from_token(live)
                if email:
                    for slot, e in self.account_emails.items():
                        if e and e.lower() == email.lower():
                            match = slot
                            break

        if match and match != self.active_slot and not os.path.exists(os.path.join(VAULT_DIR, ".rotating")) and self.recent_agy_refresh():
            # 钥匙串在 agy 刷新令牌后变了、.active_slot 没动：是 agy 回写，不是切号——把当前号的凭据写回去
            self.trace_write(f"guard_restore(live={match})", self.active_slot)
            self.apply_slot_credentials(self.active_slot)
            match = None
        if match and match != self.active_slot:
            self.active_slot = match
            self.save_active_slot(match)
            self.apply_slot_credentials(match)
            self.root.after(0, self.setup_ui)

        tpath = os.path.join(VAULT_DIR, f"acc{self.active_slot}.token")
        if os.path.exists(tpath):
            try:
                with open(tpath) as f:
                    return f.read().strip()
            except Exception:
                pass
        return live or ""

    def external_switch(self):
        """CLI/switch.py 在轮换途中切号：.active_slot 被写成了别的号，返回新槽位，否则 None"""
        try:
            v = open(self.ACTIVE_FILE).read().strip()
        except OSError:
            return None
        if v in ALL_SLOTS and v != self.active_slot and os.path.exists(os.path.join(VAULT_DIR, f"acc{v}.token")):
            return v
        return None

    def emails_changed(self):
        sig = tuple(os.path.getmtime(os.path.join(VAULT_DIR, f"acc{n}.token"))
                    if os.path.exists(os.path.join(VAULT_DIR, f"acc{n}.token")) else 0 for n in ALL_SLOTS)
        if getattr(self, "_mt_sig", sig) != sig:
            self._mt_sig = sig
            return True
        self._mt_sig = sig
        return False

    def trace_write(self, what, slot):
        """谁在改活跃号：记录调用栈，排查凭据被改回的问题"""
        try:
            import traceback
            stack = " < ".join(f"{f.name}:{f.lineno}" for f in traceback.extract_stack()[-5:-1][::-1])
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud.log"), "a") as lf:
                lf.write(f"{time.strftime('%m-%d %H:%M:%S')} {what} slot={slot} via {stack}\n")
        except Exception:
            pass

    def apply_slot_credentials(self, slot):
        self.trace_write("apply_creds", slot)
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
        self.save_active_slot(slot)
        return True

    def sync_app_login(self, slot):
        """客户端读 app_storage 里的 lastLoginUsername 判断账号；借号轮询会把它留在最后借的槽位上，这里始终对齐到活跃号"""
        email = self.account_emails.get(slot, "")
        if not email or not os.path.exists(APP_STORAGE):
            return
        try:
            with open(APP_STORAGE, "r", encoding="utf-8") as af:
                st = json.load(af)
            if st.get("jetski.onboarding.lastLoginUsername") == email:
                return
            st["jetski.onboarding.lastLoginUsername"] = email
            with open(APP_STORAGE, "w", encoding="utf-8") as af:
                json.dump(st, af, indent=2, ensure_ascii=False)
        except Exception:
            pass

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
        if getattr(self, "on_resize", None) and callable(self.on_resize):
            self.on_resize(h)
        elif getattr(self, "is_standalone", True):
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

    FOLD_FILE = os.path.join(os.path.expanduser("~/.gemini/account_vault"), ".folded_slots")

    def load_folded(self):
        try:
            return {x for x in open(self.FOLD_FILE).read().split() if x in ALL_SLOTS}
        except OSError:
            return set()

    def save_folded(self, folded):
        try:
            with open(self.FOLD_FILE, "w") as f:
                f.write(" ".join(sorted(folded, key=int)))
        except OSError:
            pass

    def toggle_fold(self, slot):
        folded = self.load_folded()
        folded.symmetric_difference_update({slot})
        self.save_folded(folded)
        self.setup_ui()

    def build_names(self, slots):
        """显示用的两条序列：Pro 叫 P1 P2…，其余（Plus/Free/还没数据）叫 S1 S2…，各自按槽位号从小到大排。
        只是显示名，底层 accN 槽位号不变；档位变了显示名会跟着变。未绑定的槽位叫「新号」。"""
        names, p, s_ = {}, 0, 0
        for slot in sorted(slots, key=int):
            if not os.path.exists(os.path.join(VAULT_DIR, f"acc{slot}.token")):
                names[slot] = "新号"
            elif self.tier_name(self.account_data.get(slot, {})) == "Pro":
                p += 1; names[slot] = f"P{p}"
            else:
                s_ += 1; names[slot] = f"S{s_}"
        self.names = names

    def nm(self, slot):
        return getattr(self, "names", {}).get(slot) or f"{slot}号"

    def group_rank(self, slot):
        n = self.nm(slot)
        return 0 if n.startswith("P") else (1 if n.startswith("S") else 2)

    def nonpro_slots(self):
        """已绑定且档位不是 Pro 的号（Plus / Free）；还没拉到额度数据的号不算"""
        return [s for s in ALL_SLOTS if os.path.exists(os.path.join(VAULT_DIR, f"acc{s}.token"))
                and self.tier_name(self.account_data.get(s, {})) in ("Plus", "Free")]

    HIDE_S_FILE = os.path.join(os.path.expanduser("~/.gemini/account_vault"), ".hide_s")

    def toggle_hide_s(self):
        """S 号整组隐藏/显示：隐藏时连卡片和底部按钮都不画（当前在用的号和「新号」绑定卡始终保留）"""
        try:
            if os.path.exists(self.HIDE_S_FILE):
                os.remove(self.HIDE_S_FILE)
            else:
                open(self.HIDE_S_FILE, "w").close()
                self.save_folded(self.load_folded() - set(self.nonpro_slots()))  # 清掉旧的「只留一行」状态
        except OSError:
            pass
        self.page_idx = 0
        self.setup_ui()

    def toggle_fold_nonpro(self):
        """非 Pro 的号全都收起了就全部展开，否则全部收起"""
        nonpro = set(self.nonpro_slots())
        if not nonpro:
            return
        folded = self.load_folded()
        folded = (folded - nonpro) if nonpro <= folded else (folded | nonpro)
        self.save_folded(folded)
        self.page_idx = 0
        self.setup_ui()

    def save_active_slot(self, slot):
        self.trace_write("save_active", slot)
        try:
            with open(self.ACTIVE_FILE, "w") as f:
                f.write(str(slot))
        except OSError:
            pass

    def detect_current_slot(self):
        if os.path.exists(LOCK_FILE):
            return self.active_slot or "1"
        # 不读独立 token 文件：借用轮换时它会变成被借的号（启动时恰好撞上轮换就会认错号，2026-10-26）
        # 2. 检查钥匙串
        try:
            cmd = ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"]
            cur = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
            cur_email = self.extract_email_from_token(cur)
            if cur_email:
                for slot in ALL_SLOTS:
                    if self.account_emails.get(slot, "").lower() == cur_email.lower():
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

    def on_double_click(self, event):
        """双击任意位置切换折叠/展开；带手型光标的可点控件（X号标签、No.X 按钮、⟳、视图开关）除外，免得和「点两下确认切号」冲突"""
        try:
            if str(event.widget.cget("cursor")) == "pointinghand":
                return
        except Exception:
            pass
        self.root.after(0, self.toggle_collapse)

    def setup_ui(self):
        target = self.container
        if self.is_standalone and not getattr(self, "_dbl_bound", False):
            self.root.bind_all("<Double-Button-1>", self.on_double_click)
            self._dbl_bound = True
        for widget in target.winfo_children():
            widget.destroy()

        self.time_labels = []
        self.active_model_group = self.detect_active_model_group()
        slots = self.get_available_slots()
        self.build_names(slots)
        slots = sorted(slots, key=lambda x: (self.group_rank(x), int(x)))  # P 组在前，S 组在后
        s_all = [x for x in slots if self.group_rank(x) == 1]
        hide_s = os.path.exists(self.HIDE_S_FILE)
        if hide_s:
            slots = [x for x in slots if self.group_rank(x) != 1 or x == self.active_slot]

        if getattr(self, "sort_g_week", False):
            def weekly_key(slot):
                reset = self.account_data.get(slot, {}).get("gwk_reset")
                bound = os.path.exists(os.path.join(VAULT_DIR, f"acc{slot}.token"))
                try:
                    target = datetime.datetime.fromisoformat(reset.replace("Z", "+00:00"))
                    seconds = max(0, target.timestamp() - time.time())
                except (AttributeError, TypeError, ValueError, OverflowError):
                    seconds = float("inf")
                return (not bound, seconds, int(slot))
            slots = sorted(slots, key=weekly_key)

        # 1. Header
        header = tk.Frame(target, bg="#11141a", height=34, padx=10)
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
            rec_text = f" · 正在切换至 {self.nm(self.active_slot)}..."
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
            rec_text = f" · 建议切{self.nm(best_slot)}!" if best_slot and best_slot != self.active_slot else " · Claude已耗尽"
        elif best_slot and best_slot != self.active_slot and (self.account_data.get(best_slot, {}).get("c5h_pct", 0) > cur_c5 + 30):
            rec_text = f" · 建议切{self.nm(best_slot)}"
        elif cur_c5 < 15:
            rec_text = f" · 建议切{self.nm(best_slot)}" if best_slot and best_slot != self.active_slot else " · 配额紧张"
        else:
            rec_text = " · 状态极佳"
        title_text = f"桌面 {self.nm(self.active_slot)}{rec_text}"

        lbl_title = tk.Label(header, text=title_text, fg=TEXT_PRIMARY, bg="#11141a", font=("SF Pro Text", 9, "bold"))
        lbl_title.pack(side=tk.LEFT, pady=6)
        self.bind_drag(lbl_title)

        btn_ref = tk.Label(header, text="⟳", fg=TEXT_MUTED, bg="#11141a", font=("SF Mono", 12, "bold"), cursor="pointinghand")
        btn_ref.pack(side=tk.RIGHT, pady=4)
        btn_ref.bind("<Button-1>", lambda e: (self.load_cache(), self.setup_ui()) if self.read_only else self.trigger_refresh())
        btn_ref.bind("<Enter>", lambda e: btn_ref.configure(fg=COLOR_CYAN))
        btn_ref.bind("<Leave>", lambda e: btn_ref.configure(fg=TEXT_MUTED))

        sort_bar = tk.Frame(target, bg="#11141a", padx=10)
        sort_bar.pack(fill=tk.X)
        sort_btn = tk.Label(sort_bar, text="G 周复位 ↑ ✓" if getattr(self, "sort_g_week", False) else "G 周复位 ↑", fg=COLOR_CYAN if getattr(self, "sort_g_week", False) else TEXT_MUTED, bg="#11141a", font=("SF Mono", 9, "bold"), cursor="pointinghand")
        sort_btn.pack(side=tk.RIGHT, pady=(0, 4))
        def toggle_week_sort(event=None):
            self.sort_g_week = not getattr(self, "sort_g_week", False)
            self.setup_ui()
        sort_btn.bind("<Button-1>", toggle_week_sort)

        # 一键隐藏/显示整组 S 号（隐藏后完全不画，不留一行）
        if s_all:
            np_btn = tk.Label(sort_bar, text=f"▸ 显示 S 号 ({len(s_all)})" if hide_s else "▾ 收起 S 号", fg=COLOR_CYAN, bg="#11141a",
                              font=("SF Mono", 11, "bold"), cursor="pointinghand")
            np_btn.pack(side=tk.LEFT, pady=(0, 4))
            np_btn.bind("<Button-1>", lambda e: self.toggle_hide_s())

        # 2. Body
        # 先建不 pack：底部切号按钮和页码条先占位，账号再多也不会把它们挤出窗口（2026-11-11）
        self.body = tk.Frame(target, bg=BG_COLOR, padx=8, pady=4)
        self.bind_drag(self.body)

        if self.collapsed_mode:
            self.render_collapsed_matrix(slots)
        else:
            self.render_expanded_cards(slots)
        self._cards = list(self.body.winfo_children())

        # 3. Bottom Switch Bar（每行最多 4 个按钮，超出另起一行；点一次变「确认?」，再点一次才切换）
        PER_ROW = 6
        bound = [x for x in slots if os.path.exists(os.path.join(VAULT_DIR, f"acc{x}.token"))]  # 未绑定的由占位卡片负责，不占按钮位
        chunks = [bound[i:i + PER_ROW] for i in range(0, len(bound), PER_ROW)]
        self._slot_btns = {}
        for ci in reversed(range(len(chunks))):  # side=BOTTOM 先 pack 的在最下面，所以倒着来
            bar = tk.Frame(target, bg="#11141a", height=34, padx=4)
            bar.pack(fill=tk.X, side=tk.BOTTOM)
            bar.pack_propagate(False)
            self.bind_drag(bar)
            for slot in chunks[ci]:
                has_tok = os.path.exists(os.path.join(VAULT_DIR, f"acc{slot}.token"))
                is_active = (slot == self.active_slot) and has_tok
                bg = "#1e293b" if is_active else "#151922"
                if not has_tok:
                    fg = COLOR_CYAN
                    txt = "+新号"
                else:
                    fg = COLOR_GREEN if is_active else (TIER_PALETTE["Pro"][4] if self.group_rank(slot) == 0 else TIER_PALETTE["Plus"][4])
                    txt = f"{self.nm(slot)}✓" if is_active else self.nm(slot)
                border = "#3b82f6" if is_active else BORDER_INACTIVE

                btn = tk.Label(bar, text=txt, fg=fg, bg=bg, font=("SF Mono", 9, "bold"), padx=2, pady=2, cursor="pointinghand", highlightbackground=border, highlightthickness=1)
                btn.pack(side=tk.LEFT, padx=1, pady=4)
                self._slot_btns[slot] = (btn, txt, fg, bg)
                btn.bind("<Button-1>", lambda e, s=slot, b=btn, t=txt, f=fg, bg0=bg: self.confirm_switch(s, b, t, f, bg0))

        # 4. 页码条（账号一页放不下才出现）+ 账号区
        self.pager = tk.Frame(target, bg="#11141a")
        self.body.pack(fill=tk.BOTH, expand=True)
        self.layout_pages()
        if not self.is_standalone and not getattr(self, "_cfg_bound", False):
            self.container.bind("<Configure>", self.on_container_resize)
            self._cfg_bound = True

        self.root.after_idle(self.fit_height)

    def page_budget(self, reserve_pager):
        """账号区能用的高度：容器高度减去标题栏、排序栏、底部切号按钮（和页码条）"""
        target = self.container
        h = 0
        if not self.is_standalone:
            try:
                target.update_idletasks()
                h = target.winfo_height() if target.winfo_ismapped() else 0
            except tk.TclError:
                h = 0
            if h < 150:
                rh = self.root.winfo_height()
                h = (rh - 77) if rh > 250 else self.root.winfo_screenheight() - 200
        else:
            h = self.root.winfo_screenheight() - 110   # 独立窗口会按内容自适应高度，只需别超出屏幕
        self._last_container_h = h
        other = sum(w.winfo_reqheight() for w in target.winfo_children() if w is not self.body and w is not self.pager)
        return max(120, h - other - 10 - (46 if reserve_pager else 0))

    def layout_pages(self):
        """按卡片实际高度把账号分页，只显示当前页，底部给出 1 2 3 4 页码按钮"""
        cards = [c for c in getattr(self, "_cards", []) if c.winfo_exists()]
        if not cards:
            return
        self.body.update_idletasks()
        heights = [c.winfo_reqheight() + 4 for c in cards]

        def split(budget):
            pages, used = [[]], 0
            for c, h in zip(cards, heights):
                if pages[-1] and used + h > budget:
                    pages.append([])
                    used = 0
                pages[-1].append(c)
                used += h
            return pages

        pages = split(self.page_budget(False))
        if len(pages) > 1:
            pages = split(self.page_budget(True))
        self.page_idx = max(0, min(getattr(self, "page_idx", 0), len(pages) - 1))
        for c in cards:
            c.pack_forget()
        for c in pages[self.page_idx]:
            c.pack(fill=tk.X, pady=2)

        for w in self.pager.winfo_children():
            w.destroy()
        if len(pages) <= 1:
            self.pager.pack_forget()
            return
        self.pager.pack(fill=tk.X, side=tk.BOTTOM, before=self.body)
        inner = tk.Frame(self.pager, bg="#11141a")
        inner.pack(pady=5)
        for i in range(len(pages)):
            on = (i == self.page_idx)
            b = tk.Label(inner, text=str(i + 1), width=3, pady=3, font=("SF Mono", 13, "bold"), cursor="pointinghand",
                         fg="#0c0e12" if on else TEXT_PRIMARY, bg=COLOR_CYAN if on else "#1e293b",
                         highlightthickness=1, highlightbackground=COLOR_CYAN if on else "#334155")
            b.pack(side=tk.LEFT, padx=4)
            b.bind("<Button-1>", lambda e, i=i: self.goto_page(i))

    def goto_page(self, i):
        self.page_idx = i
        self.layout_pages()

    def on_container_resize(self, event):
        """嵌在调度舱里时窗口高度变了就重新分页（防抖，避免连续触发）"""
        if event.widget is not self.container or abs(event.height - getattr(self, "_last_container_h", 0)) < 20:
            return
        if getattr(self, "_relayout_job", None):
            try:
                self.root.after_cancel(self._relayout_job)
            except Exception:
                pass
        self._relayout_job = self.root.after(150, self.layout_pages)

    def render_collapsed_matrix(self, slots):
        """紧凑折叠视图：每账号一行，超薄卡片，支持 5 个账号仅占微小屏幕"""
        for slot in slots:
            tpath = os.path.join(VAULT_DIR, f"acc{slot}.token")
            has_token = os.path.exists(tpath)
            is_active = (slot == self.active_slot) and has_token
            data = self.account_data.get(slot, {})
            email = self.account_emails.get(slot, "")

            bg, border, accent = self.card_colors(data, has_token, is_active)

            card = tk.Frame(self.body, bg=bg, highlightbackground=border, highlightthickness=1, padx=8, pady=3)
            card.pack(fill=tk.X, pady=2)
            self.bind_drag(card)
            row = tk.Frame(card, bg=bg)   # 左边是「X号」，右边 lines 里放 C / G 两行，纵向左对齐
            row.pack(fill=tk.X)
            self.bind_drag(row)
            lines = tk.Frame(row, bg=bg)

            tier = self.tier_of(data) if has_token else None
            l_slot = tk.Label(row, text=self.nm(slot), fg=accent, bg=bg, font=("SF Mono", 9, "bold"))
            l_slot.pack(side=tk.LEFT)
            if has_token:
                l_slot.configure(cursor="pointinghand")
                l_slot.bind("<Button-1>", lambda e, s=slot: self.confirm_switch(s))
            else:
                self.bind_drag(l_slot)

            if not has_token:
                btn_b = tk.Label(row, text="+ 点击绑定此账号", fg=COLOR_CYAN, bg=bg, font=("SF Pro Text", 8, "bold"), cursor="pointinghand")
                btn_b.pack(side=tk.LEFT, padx=(8, 0))
                btn_b.bind("<Button-1>", lambda e, s=slot: self.bind_new_slot(s))
                continue

            # 一行里 5h 和 1w 都显示：Pro（有 Argon）两行 = C 行 + G 行；其余一行只有 G（没有 5h 额度的 Free 只显示周）
            def q_line(parent, name, segs):
                """固定列宽：C 行和 G 行的 5h / 1w 上下对齐；缺 5h 的号留空占位"""
                f = tk.Frame(lines, bg=bg)
                f.pack(fill=tk.X)
                self.bind_drag(f)
                def lbl(text, w, fg, font, anchor="w", padx=0):
                    l = tk.Label(f, text=text, width=w, anchor=anchor, fg=fg, bg=bg, font=font)
                    l.pack(side=tk.LEFT, padx=padx)
                    self.bind_drag(l)
                    return l
                lbl(name, 6, BRAND_COLORS.get(name, self._th["muted"]), ("SF Mono", 8, "bold"), padx=(2, 0))
                for seg in segs:
                    if seg is None:
                        lbl("", 15, self._th["muted"], ("SF Mono", 8))
                        continue
                    lab, pct, reset = seg
                    txt = "周耗" if reset == "DISABLED" else ("--" if pct is None else f"{pct:.0f}%")
                    lbl(lab, max(2, len(lab)), self._th["muted"], ("SF Mono", 7))
                    lbl(txt, 4, self.pct_color(pct), ("SF Mono", 8, "bold"), anchor="e")
                    t = lbl("" if (not reset or reset == "DISABLED") else self.format_countdown(reset), 5, self._th["muted"], ("SF Mono", 7), padx=(2, 4))
                    if reset and reset != "DISABLED":
                        self.time_labels.append((t, reset))

            g_segs = [("1w", data.get("gwk_pct"), data.get("gwk_reset")),
                      ("5h", data.get("g5h_pct"), data.get("g5h_reset")) if data.get("g5h_pct") is not None else None]
            lines.pack(side=tk.LEFT, fill=tk.X, expand=True)
            if tier == "full":
                q_line(row, "Claude", [("1w", data.get("cwk_pct"), data.get("cwk_reset")), ("5h", data.get("c5h_pct"), data.get("c5h_reset"))])
                q_line(row, "GPT", g_segs)
                q_line(row, "Argon", [("", data.get("argon_pct"), data.get("argon_reset"))])   # Pro：第三行 Argon
            else:
                q_line(row, "GPT", g_segs)

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

            l_slot = tk.Label(header_row, text=self.nm(slot), fg=accent, bg=bg, font=("SF Pro Display", 10, "bold"))
            l_slot.pack(side=tk.LEFT)
            if has_token:
                l_slot.configure(cursor="pointinghand")
                l_slot.bind("<Button-1>", lambda e, s=slot: self.confirm_switch(s))
            else:
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
                    text="尚未绑定 · 点击此处绑定新账号",
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
                l_slot.configure(text=f"▸{self.nm(slot)}")

            is_folded = slot in self.load_folded()
            fold_btn = tk.Label(header_row, text="＋" if is_folded else "－", fg=self._th["muted"], bg=bg,
                                font=("SF Mono", 10, "bold"), cursor="pointinghand", padx=4)
            fold_btn.pack(side=tk.RIGHT)
            fold_btn.bind("<Button-1>", lambda e, sl=slot: self.toggle_fold(sl))
            if is_folded:
                continue

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

            # 2~4. 三行用同一套固定列：模型全名（企业色） | 5h 段 | Wk 段；Argon 按周复位，落在 Wk 列、5h 列留空
            def exp_row(name, seg5, segwk, none_txt="--"):
                f = tk.Frame(card, bg=bg, pady=1)
                f.pack(fill=tk.X)
                self.bind_drag(f)
                def lbl(text, w, fg, font, anchor="w", padx=0):
                    l = tk.Label(f, text=text, width=w, anchor=anchor, fg=fg, bg=bg, font=font)
                    l.pack(side=tk.LEFT, padx=padx)
                    self.bind_drag(l)
                    return l
                lbl(name, 6, BRAND_COLORS.get(name, self._th["label"]), ("SF Mono", 8, "bold"))
                for lab, seg in (("Wk", segwk), ("5h", seg5)):   # 左周右5h：只有周额度的 Argon 自然落在左列
                    if seg is None:   # 占位：和有数据的段用完全相同的三个控件和间距，Wk 列才能上下对齐
                        lbl("", 2, self._th["label"], ("SF Mono", 8, "bold"))
                        lbl("", 4, self._th["muted"], ("SF Mono", 8, "bold"), padx=(3, 3))
                        lbl("", 5, self._th["muted"], ("SF Mono", 8), padx=(0, 12))
                        continue
                    pct, reset = seg
                    lbl(lab, 2, self._th["label"], ("SF Mono", 8, "bold"))
                    lbl(none_txt if pct is None else f"{pct:.0f}%", 4, self.pct_color(pct) if pct is not None else self._th["muted"], ("SF Mono", 8, "bold"), anchor="e", padx=(3, 3))
                    t = lbl("--" if not reset else self.format_countdown(reset), 5, self._th["muted"], ("SF Mono", 8), padx=(0, 12))
                    if reset:
                        self.time_labels.append((t, reset))

            if self.has_claude(data) and (cwk_pct is not None or c5h_pct is not None):
                exp_row("Claude", (c5h_pct, c5h_reset) if c5h_pct is not None else None, (cwk_pct, cwk_reset), none_txt="未开通")
            exp_row("GPT", (g5h_pct, g5h_reset) if g5h_pct is not None else None, (gwk_pct, gwk_reset))
            if argon_pct is not None:  # 不支持 Argon 的账号不显示该行
                exp_row("Argon", None, (argon_pct, argon_reset))

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

    def confirm_switch(self, slot, btn=None, txt=None, fg=None, bg=None):
        """第一次点击只把底部按钮变成「确认?」，4 秒内在同一位置再点一次才真正切换。
        点卡片上的「N号」也走这里，借用该号底部按钮显示「确认?」（2026-10-26 恢复：曾被改成点一下直接切）"""
        if slot == self.active_slot:
            return
        if btn is None:
            btn, txt, fg, bg = getattr(self, "_slot_btns", {}).get(slot, (None, None, None, None))
        now = time.time()
        pend = getattr(self, "_pending_switch", None)
        if pend and pend[0] == slot and now - pend[1] < 4:
            self._pending_switch = None
            self.switch_to_slot(slot)
            return
        self._pending_switch = (slot, now)
        if btn is not None:
            btn.configure(text="确认?", fg="#fbbf24", bg="#3b2a0a")

        def revert():
            cur = getattr(self, "_pending_switch", None)
            if cur and cur[0] == slot and cur[1] == now:
                self._pending_switch = None
            try:
                if btn is not None:
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
                    pids = subprocess.getoutput("pgrep -f Antigravity").strip().split()
                    if pids and pids[0]:
                        subprocess.run(["pkill", "-9", "-f", "Antigravity.app/Contents/Resources/bin/language_server"], check=False)
                        time.sleep(0.5)
                        self.apply_slot_credentials(slot)
            finally:
                self.switching_slot = False
                self.root.after(0, self.setup_ui)
                self.root.after(300, self.trigger_refresh)

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

                            # 在隔离的 HOME 里查该槽位额度：agy 读 HOME 下的凭据文件，不碰真实钥匙串、
                            # 客户端凭据文件和 app_storage，所以刷新期间 CLI/App 始终停在用户选的号（2026-10-26）
                            probe_home = os.path.join(VAULT_DIR, ".probe_home")
                            probe_cli = os.path.join(probe_home, ".gemini", "antigravity-cli")
                            os.makedirs(probe_cli, mode=0o700, exist_ok=True)
                            dec_tok = base64.b64decode(slot_token.replace("go-keyring-base64:", "")).decode("utf-8")
                            with open(os.path.join(probe_cli, "antigravity-oauth-token"), "w", encoding="utf-8") as pf:
                                pf.write(dec_tok)
                            with open(os.path.join(probe_cli, "jetski-standalone-oauth-token"), "w", encoding="utf-8") as pf:
                                pf.write(dec_tok)
                            os.chmod(os.path.join(probe_cli, "antigravity-oauth-token"), 0o600)
                            cmd = ["/Users/weinliu/.local/bin/agy", "--output-format", "json", "-p", "/usage"]
                            probe_env = dict(os.environ, HOME=probe_home)
                            probe_env["BROWSER"] = "/usr/bin/true"
                            probe_env["DESKTOP_SESSION"] = ""
                            probe_env["DISPLAY"] = ""
                            # agy 优先读钥匙串：隔离 HOME 的钥匙串里也要换成本槽位的凭据，否则会沿用上一个号（2026-10-26 实测：4-7 号全显示成 3 号）
                            probe_kc = os.path.join(probe_home, "Library", "Keychains", "login.keychain-db")
                            if not os.path.exists(probe_kc):
                                os.makedirs(os.path.dirname(probe_kc), mode=0o700, exist_ok=True)
                                subprocess.run(["security", "create-keychain", "-p", "", probe_kc], env=probe_env, check=False, stderr=subprocess.DEVNULL)
                                subprocess.run(["security", "set-keychain-settings", probe_kc], env=probe_env, check=False, stderr=subprocess.DEVNULL)
                                subprocess.run(["security", "default-keychain", "-d", "user", "-s", probe_kc], env=probe_env, check=False, stderr=subprocess.DEVNULL)
                                subprocess.run(["security", "list-keychains", "-d", "user", "-s", probe_kc], env=probe_env, check=False, stderr=subprocess.DEVNULL)
                            subprocess.run(["security", "unlock-keychain", "-p", "", probe_kc], env=probe_env, check=False, stderr=subprocess.DEVNULL)
                            subprocess.run(["security", "add-generic-password", "-U", "-s", "gemini", "-a", "antigravity", "-w", slot_token],
                                           env=probe_env, check=True, stderr=subprocess.DEVNULL)
                            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=probe_env)
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
                self.save_cache()
            except Exception:
                pass
            try:
                os.remove(ROTATING_FILE)
            except OSError:
                pass
            # 额度现在在隔离 HOME 里查，真实凭据整轮没动过；只需采纳期间用户在 CLI 切的号（不再写钥匙串）
            ext = self.external_switch()
            if ext:
                self.active_slot = ext
            self.last_fetch_ts = time.time()
            self.root.after(0, self.on_done)

        threading.Thread(target=worker, daemon=True).start()

    def on_done(self):
        self.is_fetching = False
        self.setup_ui()

    def fit_height(self):
        """按内容实际高度撑开窗口，避免账号变多时底部被截断"""
        if not getattr(self, "is_standalone", True):
            return
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

        # 检查并清理陈旧锁文件（>60s）
        if os.path.exists(ROTATING_FILE):
            try:
                if time.time() - os.path.getmtime(ROTATING_FILE) > 60:
                    os.remove(ROTATING_FILE)
            except OSError:
                pass

        # 每 2 秒检查一次外部切号状态，实时跟随
        now = time.time()
        if (now - getattr(self, "_last_watch", 0) >= 2 and not self.is_fetching and not self.is_binding
                and not self.switching_slot and not os.path.exists(ROTATING_FILE) and not os.path.exists(LOCK_FILE)):
            self._last_watch = now
            try:
                self.adopt_live()
            except Exception:
                pass

        if not self.read_only and not self.is_fetching and not self.is_binding and not self.switching_slot and (time.time() - self.last_fetch_ts >= 30):
            self.trigger_refresh()
        elif self.read_only:
            # 只读模式：缓存文件变了才重读并重画（以前只重读不重画，屏幕上一直是启动时的旧数字，2026-11-10）
            try:
                mt = os.path.getmtime(CACHE_FILE)
            except OSError:
                mt = 0
            if mt != getattr(self, "_cache_mtime", None):
                first = not hasattr(self, "_cache_mtime")
                self._cache_mtime = mt
                self.load_cache()
                self.account_emails = self.load_emails()   # 新绑定的号要带上邮箱
                if not first:
                    self.setup_ui()
            
        self.root.after(1000, self.tick)

if __name__ == "__main__":
    root = tk.Tk()
    app = MultiAccountHUD(root)
    root.mainloop()
