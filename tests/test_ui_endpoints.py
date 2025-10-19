import requests, json, time

BASE = "http://127.0.0.1:5005/ui/api"

print("\n🔍 Teste da API UI MLSL Bridge\n")

def check(label, r):
    try:
        j = r.json()
    except Exception:
        print(f"❌ {label}: resposta não JSON ({r.status_code})")
        return False

    ok = j.get("ok", True if r.status_code == 200 else False)
    print(f"{'✅' if ok else '❌'} {label}: {r.status_code} {j}")
    return ok

# --- Health ---
for url in [f"{BASE}/health", "http://127.0.0.1:5005/health"]:
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 404:
            check("Health", r)
            break
    except Exception as e:
        print(f"❌ Health ({url}): {e}")

# --- Estratégias e estados ---
check("Strategies", requests.get(f"{BASE}/strategies", timeout=10))
check("Executor", requests.get(f"{BASE}/executor_state", timeout=10))
check("Scheduler", requests.get(f"{BASE}/scheduler_state", timeout=10))

# --- Retrain endpoints ---
for label, path in [
    ("Run Due", f"{BASE}/retrain_run_due"),
    ("Run All", f"{BASE}/retrain_run_all"),
    ("Rebuild", f"{BASE}/retrain_rebuild"),
]:
    check(label, requests.post(path, timeout=60))

# --- Process control endpoints ---
for tgt in ["bridge", "executor", "scheduler"]:
    for act in ["start", "stop", "restart"]:
        url = f"{BASE}/proc_run"
        payload = {"target": tgt, "action": act}
        try:
            check(f"{tgt} {act}", requests.post(url, json=payload, timeout=15))
        except Exception as e:
            print(f"❌ {tgt} {act}: {e}")

print("\n🏁 Teste concluído.\n")