"""无界面刷新接龙额度缓存：python3 refresh_quota.py [槽位号...]，不带参数刷全部。在隔离 HOME 里借各号凭据查 /usage，不动真实登录状态。"""
import os,sys,json,base64,subprocess,time
V=os.path.expanduser("~/.gemini/account_vault"); CACHE=os.path.join(V,"multi_quota_cache.json")
PH=os.path.join(V,".probe_home"); PC=os.path.join(PH,".gemini","antigravity-cli")
slots=sys.argv[1:] or [str(i) for i in range(1,21)]
if os.path.exists(os.path.join(V,".login_lock")) or os.path.exists(os.path.join(V,".rotating")): sys.exit("有登录/轮换锁，退出")
env=dict(os.environ,HOME=PH,BROWSER="/usr/bin/true",DESKTOP_SESSION="",DISPLAY="")
for k in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"): env[k]="http://127.0.0.1:10808"
kc=os.path.join(PH,"Library","Keychains","login.keychain-db")
cache=json.load(open(CACHE))
for s in slots:
    tp=os.path.join(V,f"acc{s}.token")
    if not os.path.exists(tp): continue
    tok=open(tp).read().strip()
    if not tok: continue
    dec=base64.b64decode(tok.replace("go-keyring-base64:","")).decode()
    for n in ("antigravity-oauth-token","jetski-standalone-oauth-token"):
        open(os.path.join(PC,n),"w").write(dec)
    subprocess.run(["security","unlock-keychain","-p","",kc],env=env,stderr=subprocess.DEVNULL)
    subprocess.run(["security","add-generic-password","-U","-s","gemini","-a","antigravity","-w",tok],env=env,check=True,stderr=subprocess.DEVNULL)
    t=time.time()
    try: r=subprocess.run(["/Users/weinliu/.local/bin/agy","--output-format","json","-p","/usage"],capture_output=True,text=True,timeout=40,env=env)
    except subprocess.TimeoutExpired: print(s,"超时"); continue
    if r.returncode!=0:
        msg=(r.stderr or r.stdout)
        if "not eligible" in msg:
            import re as _re
            m=_re.search(r"https://accounts\.google\.com\S+",msg)
            vf=os.path.join(V,f".verify_url_{s}")
            if m: open(vf,"w").write(m.group(0).replace("\\n","").rstrip('"}'))
            print(f"{s} 未通过资格验证 NOT_ELIGIBLE {vf if m else ''}",flush=True)
        else: print(s,"失败 rc",r.returncode,msg.strip()[-200:].replace("\n"," "))
        continue
    try: groups=json.loads(r.stdout).get("command",{}).get("data",{}).get("groups",[])
    except Exception as e: print(s,"解析失败",r.stdout[:200]); continue
    if not groups: print(s,"无额度数据",r.stdout[:200].replace("\n"," ")); continue
    d={k:(None if k.endswith("pct") else "") for k in ("c5h_pct","c5h_reset","cwk_pct","cwk_reset","g5h_pct","g5h_reset","gwk_pct","gwk_reset","argon_pct","argon_reset")}
    for g in groups:
        gn=g.get("name","").lower()
        for b in g.get("buckets",[]):
            bid=b.get("id","").lower(); dis=b.get("disabled",False); fr=b.get("remaining_fraction",0.0)*100.0; rt=b.get("reset_time","")
            if "argon" in bid or "argon" in gn: d["argon_pct"],d["argon_reset"]=fr,rt
            elif "3p" in bid or "claude" in gn:
                if "5h" in bid or "five" in bid: d["c5h_pct"],d["c5h_reset"]=(0.0,"DISABLED") if dis else (fr,rt)
                else: d["cwk_pct"],d["cwk_reset"]=fr,rt
            elif "gemini" in gn:
                if "5h" in bid or "five" in bid: d["g5h_pct"],d["g5h_reset"]=fr,rt
                else: d["gwk_pct"],d["gwk_reset"]=fr,rt
    if d["cwk_pct"] is not None and d["cwk_pct"]<=0.01: d["c5h_pct"],d["c5h_reset"]=0.0,"DISABLED"
    old=cache.get(s,{}); cache[s]=d
    f=lambda x: "—" if x is None else f"{x:.0f}%"
    print(f"{s}号 {time.time()-t:.0f}s | Claude周 {f(old.get('cwk_pct'))}→{f(d['cwk_pct'])} 5h {f(old.get('c5h_pct'))}→{f(d['c5h_pct'])} | GPT周 {f(old.get('gwk_pct'))}→{f(d['gwk_pct'])} 5h {f(old.get('g5h_pct'))}→{f(d['g5h_pct'])} | Argon {f(old.get('argon_pct'))}→{f(d['argon_pct'])}",flush=True)
    tmp=CACHE+".tmp"; json.dump(cache,open(tmp,"w")); os.chmod(tmp,0o600); os.replace(tmp,CACHE)
