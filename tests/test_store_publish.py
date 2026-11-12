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
shutil.rmtree(root, ignore_errors=True)
print("\nALL PUBLISH CHECKS PASSED" if ok else "\nPUBLISH CHECKS FAILED")
