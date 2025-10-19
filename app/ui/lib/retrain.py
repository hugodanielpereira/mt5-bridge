# app/ui/lib/retrain.py
from __future__ import annotations
import os, time, json
from typing import Any, Dict, List, Optional
from pathlib import Path
from .paths import SCHEDULE_YAML, CONFIGS, SIGNALS_INBOX, SIGNALS_DONE

def tf_to_minutes(tf: str) -> int:
    if not tf: return 30
    t = str(tf).upper().strip()
    mapping = {"M1":1,"M5":5,"M15":15,"M30":30,"H1":60,"H2":120,"H3":180,"H4":240,"H6":360,"H8":480,"H12":720,"D1":1440,"W1":10080}
    if t in mapping: return mapping[t]
    import re
    m = re.match(r"^(\d+)\s*([MHDW])$", t)
    if m:
        n,u = int(m.group(1)), m.group(2)
        return n if u=="M" else n*60 if u=="H" else n*1440 if u=="D" else n*10080
    return 30

def file_age_minutes(p: Path) -> Optional[float]:
    try:
        if not p.exists(): return None
        return max(0.0,(time.time()-p.stat().st_mtime)/60.0)
    except Exception:
        return None

def read_yaml(p: Path) -> Dict[str, Any]:
    if not p.exists(): return {}
    text = p.read_text(encoding="utf-8")
    try:
        import yaml; return yaml.safe_load(text) or {}
    except Exception:
        try: return json.loads(text)
        except Exception: return {}

def latest_signal_name() -> Optional[str]:
    candidates = []
    for base in (SIGNALS_INBOX, SIGNALS_DONE):
        if not base.exists(): continue
        for f in base.glob("*.json"):
            try: candidates.append((f.stat().st_mtime, f.name))
            except Exception: pass
    return max(candidates, key=lambda t:t[0])[1] if candidates else None

def build_strategy_rows() -> List[Dict[str, Any]]:
    grace = float(os.getenv("RETRAIN_GRACE_MIN","10"))
    now = time.time()

    rows: List[Dict[str, Any]] = []
    jobs_in_sched: set[str] = set()

    sched = read_yaml(SCHEDULE_YAML)
    for job in (sched.get("jobs") or []):
        symbol = job.get("symbol") or job.get("mt5_symbol") or job.get("instrument") or "-"
        tf = str(job.get("timeframe") or "-").upper()
        cfg_file = job.get("config_file")
        window_min = job.get("retrain_every_minutes")
        last_run_ts = job.get("last_run_ts")

        rows.append(_calc_row(symbol, tf, "schedule", cfg_file, window_min, last_run_ts, grace, now))
        jobs_in_sched.add(f"{symbol}_{tf}")

    if CONFIGS.exists():
        for f in sorted(CONFIGS.glob("*_config.yaml")):
            name = f.stem
            parts = name.split("_")
            symbol = parts[0]
            tf = (parts[1] if len(parts)>1 else "M30").upper()
            key = f"{symbol}_{tf}"
            if key in jobs_in_sched: continue
            rows.append(_calc_row(symbol, tf, "config", str(f), tf_to_minutes(tf), None, grace, now))

    return rows

def _calc_row(symbol, tf, source, cfg_file, window_min, last_run_ts, grace, now) -> Dict[str, Any]:
    since_min = None
    if last_run_ts:
        try: since_min = max(0.0, (now - float(last_run_ts))/60.0)
        except Exception: since_min = None

    w = None
    if window_min:
        try: w = float(window_min)
        except Exception: w = None

    due = next_in = overdue = None
    if (since_min is not None) and (w is not None):
        raw = w - since_min
        due = since_min >= max(0.0, w - grace)
        next_in = raw if raw >= 0 else 0.0
        overdue = 0.0 if raw >= 0 else -raw

    def r(v):
        return round(v,1) if isinstance(v,(int,float)) else v

    return {
        "symbol": symbol, "tf": tf, "source": source, "config_file": cfg_file,
        "window_min": r(w if w is not None else window_min),
        "since_last_min": r(since_min), "due": due,
        "next_in_min": r(next_in), "overdue_min": r(overdue),
    }