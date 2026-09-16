#!/usr/bin/env python3
"""菜单栏 widget：3 个 Codex 账号的剩余用量 + 实时倒计时。

标题栏:  1:26/64  2:⚠️  3:97/100        (数字 = 5小时剩% / 周剩%)
下拉每行: <编号> <邮箱>  5h余26% ⏳2h13m41s   1w余64% ⏳5d18h   ·Xm前快照

**按账号固定显示**，不随你 `codex login` 切换而变：
  1 = xiaoshow@gmail.com   2 = hoyingse@gmail.com   3 = g56320935@gmail.com
每个账号有独立 home（~/.codex-1 / -2 / -3，用 codex1/codex2/codex3 跑）；
widget 扫这 3 个 + 共享的 ~/.codex（Codex Desktop / 裸 codex 用的），
对每个账号取"当前登着它、且快照最新"的那份数据。某账号哪个 home 都没登 → 显示未登录。

数据每 60s 拉一次 `codex-usage.py --json`；倒计时每 1s 重算（一直在动）。零 token、不联网。
跑: ~/.codex-widget-venv/bin/python ~/codex-widget.py
"""
import os, json, subprocess, time, shutil, threading
from concurrent.futures import ThreadPoolExecutor
import rumps
from AppKit import NSFont, NSAttributedString, NSFontAttributeName

DATA_REFRESH = 60          # 秒 —— 多久重读一次本地快照
TICK = 1                   # 秒 —— 倒计时重画
PROBE_EVERY = 60           # 秒 —— 多久主动跑一次 codex 拉最新用量（实测单次 ≈0% 额度）
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


def probe_one(home):
    """跑一个极小的 codex 回合，逼服务端回一份最新 rate_limits 写进日志。单次成本≈0%。
    credits 耗尽的号会秒失败，无害。卡住的 90s 后杀掉。

    输入**必须每次不同**——2026-09-16 实测固定用 "." 时，1 分钟节奏下同一输入被后端当缓存
    命中，跳过真实回合，回合里压根没有 token_count 事件，读不到新额度（还误判成"探测成本
    ≈0%"，其实是没真的跑）。换成带时间戳的 nonce 之后每次都能拿到真实 rate_limits。"""
    env = dict(os.environ)
    env["CODEX_HOME"] = os.path.expanduser(home)
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


def remain(w):
    return 100 - (w.get("used_percent") or 0) if w else None


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
    pct = f"{rr:>3.0f}%" if rr is not None else " -%"
    flag = "🔴" if rr is not None and rr < LOW_PCT else "  "
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
        self.rows = {num: rumps.MenuItem("…") for num, _ in KNOWN}
        self.i_upd = rumps.MenuItem("—")
        self.menu = [
            *self.rows.values(),
            None,
            self.i_upd,
            rumps.MenuItem("立即刷新(读本地)", callback=self.pull),
            rumps.MenuItem("现在探测所有账号(≈0额度)", callback=self.on_probe),
            None,
            rumps.MenuItem("退出", callback=rumps.quit_application),
        ]
        self.last_probe = time.time() - PROBE_EVERY + 90   # 启动 90s 后先探一次
        rumps.Timer(self.tick, TICK).start()
        self.pull(None)

    def probe(self):
        """三个号**必须串行探测**，不能像之前那样同时起 3 个线程并发跑
        `codex exec`——2026-09-16 实测：三个探测一旦同时起跑，哪怕每个的输入都不同（nonce），
        全部拿不到 token_count（本地并发互相顶掉了，具体机制不明，但现象很稳定可复现）；
        改成一个后台线程里挨个跑，各账号都能稳定拿到真实 rate_limits。串行下 3 个号总共约
        30-60s 跑完，仍在一个 PROBE_EVERY 周期内。"""
        self.last_probe = time.time()

        def run_all():
            for h in PROBE_HOMES:
                probe_one(h)

        threading.Thread(target=run_all, daemon=True).start()

    def on_probe(self, _):
        self.probe()
        # 探测要 ~40s 才有结果，排个延迟 pull
        threading.Timer(45, lambda: self.pull(None)).start()

    @staticmethod
    def _usable(d):
        """有真实窗口数据才算"好"快照。codex-usage.py 在最新一条是"credits 耗尽被拒"记录时会
        故意原样返回它（不去翻旧数据），primary/secondary 都是 null 但没有 error 字段——
        这种记录不能当成"最近一次成功"存起来，否则会把之前真实的百分比覆盖掉。"""
        return isinstance(d, dict) and not d.get("error") and (d.get("primary") or d.get("secondary"))

    def pull(self, _):
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
        self.homes = homes
        self.last_pull = now
        self.render()

    def tick(self, _):
        if time.time() - self.last_probe >= PROBE_EVERY:
            self.probe()
        if time.time() - self.last_pull >= DATA_REFRESH:
            self.pull(None)
        else:
            self.render()

    def _set_row(self, num, text):
        """设置下拉行文字，并额外套一份等宽字体的 attributedTitle 让多行按字段对齐
        （菜单默认系统字体是比例字体，光靠空格补不齐）。"""
        item = self.rows[num]
        item.title = text
        try:
            font = NSFont.monospacedSystemFontOfSize_weight_(13, 0)
            attr = NSAttributedString.alloc().initWithString_attributes_(
                text, {NSFontAttributeName: font})
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
        title = []
        for num, email in KNOWN:
            short = email.split("@")[0]
            d = self._pick(email)
            if d is None:
                # 分清"哪都没登" vs "登了但还没跑过(无快照)"
                logged_in = any(isinstance(x, dict) and x.get("email") == email
                                for x in self.homes.values())
                if logged_in:
                    title.append(f"{num}:·")
                    self._set_row(num, f"{num} {short:<{NAME_W}}已登录·还没跑过（codex{num} 跑一次即出数据）")
                else:
                    title.append(f"{num}:—")
                    self._set_row(num, f"{num} {short:<{NAME_W}}未登录（用 codex{num} 登）")
                continue
            p, s = d.get("primary"), d.get("secondary")
            cr = d.get("credits") or {}
            reached = d.get("reached") or ""
            if not p and not s:
                if cr.get("has_credits") and cr.get("balance") is not None:
                    title.append(f"{num}:${cr['balance']}")
                    self._set_row(num, f"{num} {short:<{NAME_W}}信用额度余 {cr['balance']}")
                elif cr.get("has_credits") is False or "credits" in reached:
                    title.append(f"{num}:⚠️")
                    self._set_row(num, f"{num} {short:<{NAME_W}}⚠️ 用量已尽 + credits=0"
                                       f"（等窗口重置或充值；本地只有被拒记录，跑通一次后显示窗口%）")
                else:
                    title.append(f"{num}:—")
                    self._set_row(num, f"{num} {short:<{NAME_W}}(暂无窗口数据)")
                continue
            r5, rw = remain(p), remain(s)
            f5 = f"{r5:.0f}" if r5 is not None else "-"
            fw = f"{rw:.0f}" if rw is not None else "-"
            title.append(f"{num}:{f5}/{fw}")
            bits = [b for b in (win_bit(p, "5h"), win_bit(s, "1w")) if b]
            age = d.get("snapshot_age_sec")
            agestr = f"  ·{dur(age)}前快照" if age and age > 300 else ""
            self._set_row(num, f"{num} {short:<{NAME_W}}" + "  ".join(bits) + agestr)

        self.title = "  ".join(title)
        self.i_upd.title = ("数据 " + time.strftime("%H:%M:%S", time.localtime(self.last_pull))
                            + " 拉取 · 倒计时每秒实时")


if __name__ == "__main__":
    CodexWidget().run()
