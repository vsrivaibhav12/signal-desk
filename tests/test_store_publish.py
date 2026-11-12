"""Checks that two jobs publishing to the same data branch never overwrite each other's files.  Run:  python -m tests.test_store_publish   (needs git, no internet)"""
import os, subprocess, sys, tempfile, shutil, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.store import Store

root = tempfile.mkdtemp()
g = lambda cwd, *a: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
remote = os.path.join(root, "remote.git")
subprocess.run(["git", "init", "--bare", "-b", "data", remote], capture_output=True)


def clone(name):
    d = os.path.join(root, name)
    os.makedirs(d)
    g(d, "init", "-b", "data")
    g(d, "config", "user.email", "t@t"); g(d, "config", "user.name", "t")
    g(d, "remote", "add", "origin", remote)
    return d


def write(d, name, obj):
    json.dump(obj, open(os.path.join(d, name), "w"))


ok = True
def check(label, cond):
    global ok
    print(("  ok   " if cond else "  FAIL ") + label)
    ok = ok and cond


# 1. the live engine publishes its files, including chart files
eng = clone("engine")
write(eng, "feed.json", {"v": 1}); write(eng, "strategies.json", {"v": 1})
write(eng, "chartlive_orb.json", {"trade": "morning"})
Store(eng, "x").publish(own={"feed.json", "strategies.json"}, own_prefix=("chartlive_",))

# 2. the backtest job started earlier, so its copy of the live files is stale. It publishes its own files.
bt = clone("backtest")
g(bt, "fetch", "origin", "data"); g(bt, "reset", "--hard", "FETCH_HEAD")
write(eng, "chartlive_orb.json", {"trade": "afternoon"}); write(eng, "strategies.json", {"v": 2})
Store(eng, "x").publish(own={"feed.json", "strategies.json"}, own_prefix=("chartlive_",))   # live engine moves on
write(bt, "strategies_backtest.json", {"bt": 1}); write(bt, "chartbt_orb.json", {"bt": "chart"})
Store(bt, "x").publish(own={"strategies_backtest.json"}, own_prefix=("chartbt_",))

# 3. what is on the remote now
chk = clone("check")
g(chk, "fetch", "origin", "data"); g(chk, "reset", "--hard", "FETCH_HEAD")
rd = lambda n: json.load(open(os.path.join(chk, n)))
check("the backtest's own files were published", rd("strategies_backtest.json") == {"bt": 1} and rd("chartbt_orb.json") == {"bt": "chart"})
check("the live engine's newer chart file was NOT overwritten by the backtest's stale copy", rd("chartlive_orb.json") == {"trade": "afternoon"})
check("the live engine's newer strategies file was not overwritten", rd("strategies.json") == {"v": 2})

# 4. and the other way round: the live engine must not wipe the backtest's chart files
write(eng, "strategies.json", {"v": 3})
Store(eng, "x").publish(own={"feed.json", "strategies.json"}, own_prefix=("chartlive_",))
g(chk, "fetch", "origin", "data"); g(chk, "reset", "--hard", "FETCH_HEAD")
check("the live engine did not remove the backtest's files", rd("chartbt_orb.json") == {"bt": "chart"} and rd("strategies_backtest.json") == {"bt": 1})
check("and its own update went through", rd("strategies.json") == {"v": 3})

# 5. history stays small: many publishes in one day make one commit, not one per publish
count = lambda: int(g(chk, "rev-list", "--count", "FETCH_HEAD").stdout.strip())
for n in range(5):
    write(eng, "strategies.json", {"v": 10 + n})
    Store(eng, "x").publish(own={"feed.json", "strategies.json"}, own_prefix=("chartlive_",))
g(chk, "fetch", "origin", "data"); g(chk, "reset", "--hard", "FETCH_HEAD")
check("a day's publishes are folded into one commit (%d)" % count(), count() == 1)
check("and the latest content is there", rd("strategies.json") == {"v": 14})

# 6. a job that pushes between our read and our push is not overwritten: the commit is rebuilt and retried
import engine.store as store_mod
real_run, fired = store_mod.subprocess.run, {"done": False}

def rival(*a, **k):
    cmd = a[0] if a else k.get("args")
    if cmd and cmd[0] == "git" and "push" in cmd and not fired["done"] and k.get("cwd") == eng:
        fired["done"] = True
        write(bt, "strategies_backtest.json", {"bt": 2})
        store_mod.subprocess.run = real_run                 # the rival publishes normally
        Store(bt, "x").publish(own={"strategies_backtest.json"}, own_prefix=("chartbt_",))
        store_mod.subprocess.run = rival
    return real_run(*a, **k)

store_mod.subprocess.run = rival
write(eng, "strategies.json", {"v": 99})
Store(eng, "x").publish(own={"feed.json", "strategies.json"}, own_prefix=("chartlive_",))
store_mod.subprocess.run = real_run
g(chk, "fetch", "origin", "data"); g(chk, "reset", "--hard", "FETCH_HEAD")
check("the rival push really happened mid-publish", fired["done"])
check("the rival's file survived our retry", rd("strategies_backtest.json") == {"bt": 2})
check("and our own update went through too", rd("strategies.json") == {"v": 99})
check("still one commit for the day", count() == 1)

# 7. a persistent VPS shares the same checkout between trade/news/supervisor processes.
# The engine must not refresh an intelligence file that the local news worker just wrote.
write(eng, "intelligence.json", {"seq": 1})
Store(eng, "x").publish(own={"feed.json", "strategies.json"}, own_prefix=("chartlive_",))
write(eng, "intelligence.json", {"seq": 99})
open(os.path.join(eng, "trading-supervisor.lock"), "w").write("12345")
Store(eng, "x").publish(own={"feed.json", "strategies.json"}, own_prefix=("chartlive_",), preserve_local={"intelligence.json"})
g(chk, "fetch", "origin", "data"); g(chk, "reset", "--hard", "FETCH_HEAD")
check("persistent publisher preserved the other service's local intelligence file", rd("intelligence.json") == {"seq": 99})
check("runtime lock files are excluded from the public data branch", "trading-supervisor.lock" not in g(chk, "ls-tree", "-r", "--name-only", "FETCH_HEAD").stdout.splitlines())
shutil.rmtree(root, ignore_errors=True)
print("\nALL PUBLISH CHECKS PASSED" if ok else "\nPUBLISH CHECKS FAILED")
