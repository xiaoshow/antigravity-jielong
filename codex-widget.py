#!/usr/bin/env python3
"""菜单栏 widget：3 个 Codex 账号的剩余用量 + 实时倒计时。

标题栏:  1:26/64  2:⚠️  3:97/100        (数字 = 5小时剩% / 周剩%)
下拉每行: <编号> <邮箱>  5h余26% ⏳2h13m41s   1w余64% ⏳5d18h   ·Xm前快照

**按账号固定显示**，不随你 `codex login` 切换而变：
  1 = xiaoshow@gmail.com   2 = hoyingse@gmail.com   3 = g56320935@gmail.com
每个账号有独立 home（~/.codex-1 / -2 / -3，用 codex1/codex2/codex3 跑）；
再加上共享的 ~/.codex（Codex Desktop / 裸 codex 用的）里"切换登录之后"写出的快照——
比 auth.json 旧的快照可能属于上一个号，一律不用。某账号自己的目录没登 → 显示未登录。

共享的 ~/.codex 当前登的是哪个账号，那一行会加粗、变蓝、并在行尾加 "★本机当前登录"——
那是裸 codex / Codex Desktop 现在实际会用的账号（三个 .codex-N 都是各自常驻登录，不存在
"现在登的是谁"这回事，只有这个共享 home 同一时刻只能是一个账号，标出来才有意义）。

数据每 60s 从现有会话日志读取一次；倒计时每 1s 重算（一直在动）。自动刷新零 token、不联网。
只有手动点击“现在探测所有账号”才会发起真实 `codex exec` 并消耗额度。
跑: ~/.codex-widget-venv/bin/python ~/codex-widget.py
"""
import os, json, subprocess, time, shutil, threading
from concurrent.futures import ThreadPoolExecutor
import rumps
import objc
import importlib.util
from AppKit import (NSFont, NSAttributedString, NSFontAttributeName,
                     NSColor, NSForegroundColorAttributeName)

DATA_REFRESH = 60          # 秒 —— 多久重读一次本地快照
TICK = 5                   # 秒 —— 倒计时重画，减少菜单原生对象创建
LOW_PCT = 20
CODEX_BIN = shutil.which("codex") or "/opt/homebrew/bin/codex"
PROBE_HOMES = ["~/.codex-1", "~/.codex-2", "~/.codex-3"]

# 账号 -> 固定编号（顺序即显示顺序）
KNOWN = [
    ("1", "xiaoshow@gmail.com"),
    ("2", "hoyingse@gmail.com"),
    ("3", "g56320935@gmail.com"),
]
SCAN_HOMES = ["~/.codex", "~/.codex-1", "~/.codex-2", "~/.codex-3"]
NAME_W = max(len(email.split("@")[0]) for _, email in KNOWN) + 1   # 账号名对齐宽度
_usage_api = None

def usage_api():
    """只加载一次，保留接口模块的刷新冷却与锁。"""
    global _usage_api
    if _usage_api is None:
        spec = importlib.util.spec_from_file_location("codex_usage_api", os.path.expanduser("~/codex_usage_api.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _usage_api = module
    return _usage_api


def fetch(home, deep=False):
    """deep=True 加 --deep：忽略"最新一条是 credits 拒绝就直接返回"的近路，一路翻到带真实
    窗口的快照为止。只用来一次性给从没缓存过数据的账号找"最近一次真实读数"垫底，不用于
    日常轮询（日常要的是"现在卡没卡住"这个实时判断，不能被垫底逻辑糊弄过去）。"""
    env = dict(os.environ)
    env["CODEX_HOME"] = os.path.expanduser(home)
    args = ["python3", os.path.expanduser("~/codex-usage.py"), "--json"]
    if deep:
        args.append("--deep")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=12, env=env)
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        return {"error": str(e)[:40]}


PROXY_URL = "http://127.0.0.1:10808"        # ~/.zshrc 里配的本机 Xray/PassWall2 代理端口
SOCKS_PROXY_URL = "socks5://127.0.0.1:10808"


def probe_one(home):
    """跑一个极小的 codex 回合，逼服务端回一份最新 rate_limits 写进日志。单次成本≈0%。
    credits 耗尽的号会秒失败，无害。卡住的 90s 后杀掉。

    输入**必须每次不同**——2026-09-16 实测固定用 "." 时，1 分钟节奏下同一输入被后端当缓存
    命中，跳过真实回合，回合里压根没有 token_count 事件，读不到新额度（还误判成"探测成本
    ≈0%"，其实是没真的跑）。换成带时间戳的 nonce 之后每次都能拿到真实 rate_limits。

    env 必须手动补代理变量——同一天又踩了一个坑：LaunchAgent 跑起来的进程**不会**
    source `~/.zshrc`，所以 `os.environ` 里根本没有 `~/.zshrc` 里配的 HTTP_PROXY/
    ALL_PROXY 这几个代理变量（本机翻墙靠它们）。实测复现：把 PATH 砍到 launchd 那么瘦
    还不够触发，只有再把代理变量也去掉才会卡在 "ERROR: Reconnecting... 2/5" 一直到
    90s 超时、且日志里连 token_count 都没有——补回代理变量后同一个请求 9 秒就回来了。
    这才是"自动探测经常空转"真正的根因，不是之前以为的"并发互相顶掉"或"输入被去重"
    （那两个也是真问题，但只是次要因素）。"""
    env = dict(os.environ)
    env["CODEX_HOME"] = os.path.expanduser(home)
    env["PATH"] = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:" + env.get("PATH", "")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.setdefault(k, PROXY_URL)
    for k in ("ALL_PROXY", "all_proxy"):
        env.setdefault(k, SOCKS_PROXY_URL)
    nonce = f"probe {time.time():.3f}"
    try:
        subprocess.run([CODEX_BIN, "exec", "--skip-git-repo-check", "-c", "mcp_servers={}", nonce],
                       capture_output=True, timeout=90, env=env,
                       cwd=os.path.expanduser("~"))
    except Exception:
        pass


def dur(sec):
    sec = int(sec)
    if sec <= 0:
        return "0s"
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def expired(w):
    ra = w.get("resets_at") if w else None
    return bool(ra) and ra <= time.time()


def remain(w):
    if not w:
        return None
    if expired(w):
        return 100.0        # 重置点已过：窗口早已回满，快照里的 used_percent 是旧的
    return 100 - (w.get("used_percent") or 0)


def abs_reset(ts):
    """本地时间的绝对重置日期，如 09/19 09:15。"""
    return time.strftime("%m/%d %H:%M", time.localtime(ts))


def win_bit(w, short):
    """短窗口(5h)/长窗口(1w)一段文本。字段定宽，供等宽字体对齐。
    倒计时（相对剩余时间）之外，两个窗口都再附上绝对重置时刻：
    5h 窗口当天就会重置，只写 HH:MM；1w 窗口隔了几天，写 MM/DD HH:MM。"""
    if not w:
        return None
    rr = remain(w)
    resets_at = w.get("resets_at")
    cd = dur(resets_at - time.time()) if resets_at else "?"
    cd_w = 9 if short == "5h" else 6          # "4h58m33s"≈9 / "6d23h"≈6
    gone = expired(w)
    if gone:
        cd = "—"
    pct = f"{rr:>3.0f}%" if rr is not None else " -%"
    flag = "🔴" if rr is not None and rr < LOW_PCT else "  "
    if gone:
        # 重置点已过：不再写过去的时刻（看着像没更新），倒计时列放"—"，重置列写"已重置"。
        # "HH:MM重置"占 9 格、"MM/DD HH:MM重置"占 15 格，"已重置"占 6 格，补空格保持列宽
        reset_str = "已重置" + " " * (3 if short == "5h" else 9)
        return f"{short}余{pct} ⏳{cd:<{cd_w}}{flag} {reset_str}"
    if resets_at:
        reset_str = (time.strftime("%H:%M", time.localtime(resets_at)) if short == "5h"
                     else abs_reset(resets_at))
    else:
        reset_str = "--:--" if short == "5h" else "--/-- --:--"
    return f"{short}余{pct} ⏳{cd:<{cd_w}}{flag} {reset_str}重置"


class CodexWidget(rumps.App):
    def __init__(self):
        super().__init__("Codex …", quit_button=None)
        self.homes = {}
        self.last_good = {}        # home -> 最近一次成功(无 error)的快照，探测没拉到新数据时兜底
        self.last_pull = 0.0
        self.pulling = False
        self.pending = None        # 后台线程读完的数据，交给主线程的 tick 去应用
        self.rows = {num: rumps.MenuItem("…") for num, _ in KNOWN}
        self.i_upd = rumps.MenuItem("—")
        self.switch_items = {
            num: rumps.MenuItem(f"切换到账号 {num}", callback=lambda _, n=num: self.on_switch(n))
            for num, _ in KNOWN
        }
        self.menu = [
            *self.rows.values(),
            None,
            self.i_upd,
            rumps.MenuItem("立即刷新(读本地)", callback=lambda _: self.pull_async()),
            rumps.MenuItem("现在探测所有账号（会消耗额度）", callback=self.on_probe),
            None,
            rumps.MenuItem("切换账号", submenu=[*self.switch_items.values()]),
            rumps.MenuItem("退出", callback=rumps.quit_application),
        ]
        rumps.Timer(self.tick, TICK).start()
        self.pull_async()

    def probe(self):
        """三个号**必须串行探测**，不能像之前那样同时起 3 个线程并发跑
        `codex exec`——2026-09-16 实测：三个探测一旦同时起跑，哪怕每个的输入都不同（nonce），
        全部拿不到 token_count（本地并发互相顶掉了，具体机制不明，但现象很稳定可复现）；
        改成一个后台线程里挨个跑，各账号都能稳定拿到真实 rate_limits。串行下 3 个号总共约
        30-60s 跑完。"""
        def run_all():
            for h in PROBE_HOMES:
                probe_one(h)

        threading.Thread(target=run_all, daemon=True).start()

    def on_probe(self, _):
        self.probe()
        # 探测要 ~40s 才有结果，排个延迟 pull
        threading.Timer(45, self.pull_async).start()

    def on_switch(self, num):
        """切换到指定账号（在共享的 ~/.codex 里）。"""
        email = None
        for n, e in KNOWN:
            if n == num:
                email = e
                break
        if not email:
            return

        def run():
            env = dict(os.environ)
            # 注意：这里切换的是共享的 ~/.codex，不是 ~/.codex-N
            env["CODEX_HOME"] = os.path.expanduser("~/.codex")
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env.setdefault(k, PROXY_URL)
            for k in ("ALL_PROXY", "all_proxy"):
                env.setdefault(k, SOCKS_PROXY_URL)
            try:
                subprocess.run([CODEX_BIN, "login", email], env=env, timeout=30)
            except Exception:
                pass
            # 登录完立即刷新展示
            threading.Timer(2, self.pull_async).start()

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _usable(d):
        """有真实窗口数据才算"好"快照。codex-usage.py 在最新一条是"credits 耗尽被拒"记录时会
        故意原样返回它（不去翻旧数据），primary/secondary 都是 null 但没有 error 字段——
        这种记录不能当成"最近一次成功"存起来，否则会把之前真实的百分比覆盖掉。"""
        return isinstance(d, dict) and not d.get("error") and (d.get("primary") or d.get("secondary"))

    def pull_async(self):
        """读数据放后台线程。以前 pull 直接跑在主线程（Timer 回调），读日志 + 起 4 个子进程
        期间菜单栏点不开；探测完成后还会从 threading.Timer 线程直接调 pull 去改 AppKit 控件，
        非主线程动 UI 会让菜单卡死。现在后台只算数据，UI 一律由主线程的 tick 应用。"""
        if self.pulling:
            return
        self.pulling = True
        self.last_pull = time.time()

        def run():
            try:
                self.pending = self.collect()
            finally:
                self.pulling = False
        threading.Thread(target=run, daemon=True).start()

    def collect(self):
        try:
            with ThreadPoolExecutor(max_workers=len(SCAN_HOMES)) as ex:
                fresh = dict(ex.map(lambda h: (h, fetch(h)), SCAN_HOMES))
        except Exception as e:
            fresh = {h: {"error": str(e)[:40]} for h in SCAN_HOMES}
        # 有可用新数据就用新的，并连同"拿到的那一刻"存为最近一次成功快照；这次没拿到可用数据
        # （探测出错、no-logs、或 credits 耗尽被拒且没有窗口数据）就退回它，而不是显示成空白/警告——
        # snapshot_age_sec 要按存入以来又过了多久补齐，不然"前快照"的年龄会卡住不动。
        now = time.time()
        for h, d in fresh.items():
            if self._usable(d):
                self.last_good[h] = (d, now)
            elif h not in self.last_good:
                # 从没给这个 home 缓存过真实数据（刚重启，或它这轮第一次撞上 credits 拒绝）——
                # 用 --deep 兜底翻一次旧快照，避免直接秀"用量已尽"警告而不是它最近的真实百分比。
                deep = fetch(h, deep=True)
                if self._usable(deep):
                    self.last_good[h] = (deep, now)
        homes = {}
        for h in SCAN_HOMES:
            d = fresh.get(h)
            if self._usable(d):
                homes[h] = d
                continue
            cached = self.last_good.get(h)
            if not cached:
                homes[h] = d
                continue
            cd, captured_at = cached
            cd2 = dict(cd)
            if cd2.get("snapshot_age_sec") is not None:
                cd2["snapshot_age_sec"] += int(now - captured_at)
            homes[h] = cd2
        self._label_shared(homes)
        # 以官方接口为准（实时、零额度）。本地日志只在接口失败时兜底——日志只有在
        # ~/.codex-N 里跑过任务才更新，在 Desktop 里用的号会显示成十几个小时前的旧数。
        try:
            live = usage_api().fetch_all()
        except Exception:
            live = {"accounts": []}
        for a in live["accounts"]:
            if a.get("error") or not a.get("five"):
                continue
            for h in list(homes):
                if isinstance(homes[h], dict) and homes[h].get("email") == a["email"]:
                    homes[h] = dict(homes[h], email=None)   # 本地旧快照不再参与该号取数
            homes[f"api#{a['num']}"] = {
                "email": a["email"],
                "primary": {"used_percent": a["five"]["used"], "window_minutes": 300, "resets_at": a["five"]["reset_at"]},
                "secondary": {"used_percent": a["week"]["used"], "window_minutes": 10080, "resets_at": a["week"]["reset_at"]},
                "credits": {}, "reached": "", "snapshot_age_sec": 0,
            }
        return homes

    @staticmethod
    def _label_shared(homes):
        """共享 ~/.codex（Codex Desktop 用的）：只认"切换登录之后"写出来的快照。
        每次切号 auth.json 都会被重写，所以快照所在文件比 auth.json 新，才一定属于当前登录的号；
        比它旧的可能是上一个号留下的，不用（2026-09-30 切到 g56320935 后，xiaoshow 的 5h 余 15%
        曾被安到 3 号头上）。auth.json 不存在（Desktop 登出）也不用。
        这样正在用的号零成本实时准确，另外两个号显示各自 ~/.codex-N 里的上次读数。
        原邮箱另存 auth_email 给 ★ 用。"""
        shared = homes.get("~/.codex")
        if not isinstance(shared, dict):
            return
        shared["auth_email"] = shared.get("email")
        try:
            auth_mtime = os.path.getmtime(os.path.expanduser("~/.codex/auth.json"))
        except OSError:
            shared["email"] = None
            return
        age = shared.get("snapshot_age_sec")
        if age is None or time.time() - age <= auth_mtime:
            shared["email"] = None

    def tick(self, _):
        if self.pending is not None:
            self.homes, self.pending = self.pending, None
        elif time.time() - self.last_pull >= DATA_REFRESH:
            self.pull_async()
        # PyObjC 定时回调中创建的临时 Cocoa 对象及时释放。
        with objc.autorelease_pool():
            self.render()

    def _set_row(self, num, text, highlight=False):
        """设置下拉行文字，并额外套一份等宽字体的 attributedTitle 让多行按字段对齐
        （菜单默认系统字体是比例字体，光靠空格补不齐）。
        highlight=True：本机共享的 ~/.codex（裸 codex / Codex Desktop 用的那份登录态）
        当前正是这个账号——加粗 + 强调色，一眼看出"现在登录的是哪个"。"""
        item = self.rows[num]
        if getattr(item, "_claude_last", None) == (text, highlight):
            return
        item._claude_last = (text, highlight)
        item.title = text
        try:
            # weight 0 = regular, 0.3 ≈ semibold（NSFontWeight 的取值范围是 -1~1）
            mono = NSFont.monospacedSystemFontOfSize_weight_(13, 0.3 if highlight else 0)
            attrs = {NSFontAttributeName: mono}
            if highlight:
                attrs[NSForegroundColorAttributeName] = NSColor.systemBlueColor()
            attr = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
            item._menuitem.setAttributedTitle_(attr)
        except Exception:
            pass   # 等宽字体设置失败就退回普通 title，不影响可用性

    def _pick(self, email):
        """该账号：在所有当前登着它的 home 里，挑快照最新的一份。"""
        cands = [d for d in self.homes.values()
                 if isinstance(d, dict) and not d.get("error") and d.get("email") == email]
        if not cands:
            return None
        return min(cands, key=lambda d: d.get("snapshot_age_sec")
                   if d.get("snapshot_age_sec") is not None else 1e12)

    def render(self):
        # 本机"现在登录哪个"= 共享的 ~/.codex（裸 codex / Codex Desktop 读的那份登录态）
        # 当前的 auth 是哪个邮箱。三个 .codex-N 是各自独立常驻登录，不存在"现在登的是谁"这个
        # 问题——只有这个共享 home 在任意时刻只能是一个账号，才有意义标出来。
        shared = self.homes.get("~/.codex")
        current_email = (shared.get("auth_email", shared.get("email"))
                          if isinstance(shared, dict) and not shared.get("error") else None)

        title = []
        for num, email in KNOWN:
            short = email.split("@")[0]
            hl = (email == current_email)
            mark = " ★本机当前登录" if hl else ""
            d = self._pick(email)
            if d is None:
                # 分清"哪都没登" vs "登了但还没跑过(无快照)"
                logged_in = any(isinstance(x, dict) and x.get("email") == email
                                for x in self.homes.values())
                if logged_in:
                    title.append(f"{num}:·")
                    self._set_row(num, f"{num} {short:<{NAME_W}}已登录·还没跑过（codex{num} 跑一次即出数据）{mark}", hl)
                else:
                    title.append(f"{num}:—")
                    self._set_row(num, f"{num} {short:<{NAME_W}}未登录（用 codex{num} 登）{mark}", hl)
                continue
            p, s = d.get("primary"), d.get("secondary")
            cr = d.get("credits") or {}
            reached = d.get("reached") or ""
            if not p and not s:
                if cr.get("has_credits") and cr.get("balance") is not None:
                    title.append(f"{num}:${cr['balance']}")
                    self._set_row(num, f"{num} {short:<{NAME_W}}信用额度余 {cr['balance']}{mark}", hl)
                elif cr.get("has_credits") is False or "credits" in reached:
                    title.append(f"{num}:⚠️")
                    self._set_row(num, f"{num} {short:<{NAME_W}}⚠️ 用量已尽 + credits=0"
                                       f"（等窗口重置或充值；本地只有被拒记录，跑通一次后显示窗口%）{mark}", hl)
                else:
                    title.append(f"{num}:—")
                    self._set_row(num, f"{num} {short:<{NAME_W}}(暂无窗口数据){mark}", hl)
                continue
            r5, rw = remain(p), remain(s)
            f5 = f"{r5:.0f}" if r5 is not None else "-"
            fw = f"{rw:.0f}" if rw is not None else "-"
            title.append(f"{num}:{f5}/{fw}")
            bits = [b for b in (win_bit(p, "5h"), win_bit(s, "1w")) if b]
            age = d.get("snapshot_age_sec")
            agestr = f"  ·{dur(age)}前快照" if age and age > 300 else ""
            self._set_row(num, f"{num} {short:<{NAME_W}}" + "  ".join(bits) + agestr + mark, hl)

        new_title = "  ".join(title)
        if self.title != new_title:
            self.title = new_title
        update_title = ("数据 " + time.strftime("%H:%M:%S", time.localtime(self.last_pull))
                        + " 本地读取 · 自动刷新零 Token")
        if self.i_upd.title != update_title:
            self.i_upd.title = update_title


if __name__ == "__main__":
    CodexWidget().run()
