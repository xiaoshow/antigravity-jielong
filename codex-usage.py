#!/usr/bin/env python3
"""剩余用量 — 读本地 ~/.codex 会话日志里 Codex 记录的额度快照，零 token、不联网。
同时显示 5小时 和 1周 两个窗口各自的剩余%（分别带各自的重置时间）。 Ctrl-C 退出。
用法: python3 codex-usage.py [刷新秒数=10]
"""
import os, sys, glob, json, time, datetime, base64

CODEX_HOME = os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex")
SESS = os.path.join(CODEX_HOME, "sessions")
AUTH = os.path.join(CODEX_HOME, "auth.json")
INTERVAL = 10.0
for _a in sys.argv[1:]:
    try: INTERVAL = float(_a); break
    except ValueError: pass

def account_email():
    """当前这台机器上 codex login 的账号邮箱 —— 从 auth.json 的 id_token(JWT) 里解出来，
    不校验签名，只读本地已经登录成功的 claim，不联网。"""
    try:
        auth = json.load(open(AUTH))
        tok = auth["tokens"]["id_token"]
        payload_b64 = tok.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("email") or "?"
    except Exception:
        return "?"

WIN = {300: "5小时", 10080: "1周", 43800: "1个月", 1440: "1天"}
def wlabel(m):
    if m in WIN: return WIN[m]
    if m and m % 43200 == 0: return f"{m // 43200}个月"
    if m and m % 1440 == 0:  return f"{m // 1440}天"
    if m and m % 60 == 0:    return f"{m // 60}小时"
    return f"{m}分"

def latest_ratelimit(prefer_window=False):
    """返回 (rate_limits dict, 该快照所在文件的 mtime)。
    优先返回带真实窗口(primary/secondary 非 null)的最新快照；都没有才退回最新的(可能是被拒记录)。

    prefer_window=True（--deep）：跳过"最新一条如果是 credits 拒绝就直接返回"这条近路，
    一路找到带真实窗口的最近快照为止。默认调用方（实时显示）要的就是"现在到底卡没卡住"，
    近路是故意的；只有 widget 给"额度耗尽时兜底缓存最近一次真实读数"这种场景才要 prefer_window。"""
    files = glob.glob(os.path.join(SESS, "**", "rollout-*.jsonl"), recursive=True)
    if not files: return None, None
    files.sort(key=os.path.getmtime, reverse=True)
    fallback = None
    # 探测越勤，越容易连续产出一串没有 token_count 的"空"探测文件（缓存命中/未触发真实回合），
    # 把真正带额度信息的快照挤出窗口。40 在 1 分钟探测节奏下扛不住，放宽到 300。
    for p in files[:300]:
        try:
            size = os.path.getsize(p)
            with open(p, "rb") as f:
                f.seek(max(0, size - 512 * 1024))
                chunk = f.read().decode("utf-8", "ignore")
        except OSError:
            continue
        last = None
        for line in chunk.splitlines():
            if '"token_count"' not in line: continue
            try: j = json.loads(line)
            except ValueError: continue
            pl = j.get("payload", j)
            if pl.get("type") == "token_count":
                last = pl.get("rate_limits") or {}
        if last is None:
            continue
        mt = os.path.getmtime(p)
        reached = last.get("rate_limit_reached_type") or ""
        cr = last.get("credits") or {}
        if not prefer_window and fallback is None:
            # 最新那条：如果是"没 credits 被拒"，直接返回它（当前就是卡住的，别拿旧数据糊弄）
            if "credits" in reached or (cr.get("has_credits") is False and not last.get("primary")):
                return last, mt
        if fallback is None:
            fallback = (last, mt)
        if last.get("primary") or last.get("secondary"):
            return last, mt          # 有真实窗口，直接用
    return fallback if fallback else (None, None)

def _cd(ra):
    if not ra: return ""
    d = int(ra - time.time())
    if d <= 0: return "已重置"
    dd, d = divmod(d, 86400); hh, d = divmod(d, 3600); mm = d // 60
    return f"{dd}天{hh}小时" if dd else (f"{hh}小时{mm}分" if hh else f"{mm}分")

def render():
    acct = account_email()
    tag = f"[{acct}]  "
    rl, mt = latest_ratelimit()
    if rl is None: return f"{tag}剩余用量  (没找到 ~/.codex 会话日志)"
    parts = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not w: continue
        remain = 100.0 - (w.get("used_percent") or 0.0)
        lab = wlabel(w.get("window_minutes"))
        cd = _cd(w.get("resets_at"))
        parts.append(f"{lab} 余{remain:.0f}%" + (f" ({cd}后重置)" if cd else ""))
    if not parts:
        reached = rl.get("rate_limit_reached_type") or ""
        cr = rl.get("credits") or {}
        if cr.get("has_credits") and cr.get("balance") is not None:
            return f"{tag}剩余用量  信用额度余 {cr['balance']}"
        if "credits" in reached or cr.get("has_credits") is False:
            return f"{tag}剩余用量  ⚠️ 用量已尽 + credits=0（等窗口重置或充值；本地日志只有被拒记录，跑通一次后才有窗口%）"
        return f"{tag}剩余用量  (日志里暂无额度信息)"
    return f"{tag}剩余用量  " + "  ".join(parts)

def render_json(prefer_window=False):
    rl, mt = latest_ratelimit(prefer_window=prefer_window)
    out = {"email": account_email()}
    if rl is None:
        out["error"] = "no-logs"
    else:
        out["primary"] = rl.get("primary")        # {used_percent, window_minutes, resets_at} or null
        out["secondary"] = rl.get("secondary")
        out["credits"] = rl.get("credits") or {}
        out["reached"] = rl.get("rate_limit_reached_type") or ""
        out["snapshot_age_sec"] = int(time.time() - mt) if mt else None
    return json.dumps(out, ensure_ascii=False)

def main():
    if "--json" in sys.argv:
        print(render_json(prefer_window="--deep" in sys.argv)); return
    if "--once" in sys.argv:
        print(render()); return
    try:
        while True:
            sys.stdout.write("\r\033[K" + render())
            sys.stdout.flush()
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        sys.stdout.write("\n")

if __name__ == "__main__":
    main()
