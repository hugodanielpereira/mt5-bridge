# app/controllers/reporting_controller.py
from __future__ import annotations

import os
import json
import time
from pathlib import Path
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query, Request, HTTPException

from app.common.meta import bridge_instance_id, meta_snapshot

router = APIRouter(prefix="/reporting", tags=["reporting"])


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _now_ts() -> float:
    return time.time()


def _parse_yyyy_mm_dd(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid 'day' (use YYYY-MM-DD, UTC)")


def _today_yyyy_mm_dd() -> str:
    # UTC day boundary
    return datetime.utcnow().strftime("%Y-%m-%d")


def _guess_repo_roots() -> List[Path]:
    """
    Heurísticas para encontrar o mono-repo / outputs.
    Mantém-se simples para não depender do LAB nem PathUtils aqui.
    """
    cwd = Path.cwd().resolve()
    cands: List[Path] = [cwd, cwd.parent, cwd.parent.parent]

    # .../app/controllers -> tenta subir 2 e 3 níveis com segurança
    here = Path(__file__).resolve()
    parents = list(here.parents)
    if len(parents) > 2:
        cands.append(parents[2])
    if len(parents) > 3:
        cands.append(parents[3])

    out: List[Path] = []
    seen: set[str] = set()
    for p in cands:
        k = str(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _resolve_ledger_base_dir() -> Path:
    """
    Onde está o ledger JSONL?

    Ordem:
      1) LEDGER_DIR (direto)
      2) TRADING_ROOT / PROJECT_ROOT (raiz do mono-repo) -> outputs/live/ledger
      3) ./outputs/live/ledger (se existir)
      4) procurar ../apps/ml-strategy-lab/outputs/live/ledger em alguns parents
      5) fallback final: ./outputs/live/ledger (mesmo que não exista ainda)
    """
    env = (os.getenv("LEDGER_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()

    env2 = (os.getenv("TRADING_ROOT") or os.getenv("PROJECT_ROOT") or "").strip()
    if env2:
        base = Path(env2).expanduser().resolve()
        return base / "outputs" / "live" / "ledger"

    local = Path("./outputs/live/ledger").resolve()
    if local.exists():
        return local

    for root in _guess_repo_roots():
        cand = root / "apps" / "ml-strategy-lab" / "outputs" / "live" / "ledger"
        if cand.exists():
            return cand.resolve()

    return local


def _ledger_file_for_instance(ledger_base: Path, instance_id: str) -> Path:
    """
    Preferido:
      outputs/live/ledger/<instance_id>/trades.jsonl

    Fallback compat:
      outputs/live/ledger/trades.jsonl
    """
    p1 = ledger_base / instance_id / "trades.jsonl"
    if p1.exists():
        return p1
    return ledger_base / "trades.jsonl"


def _iter_jsonl(path: Path, *, max_lines: int = 200000) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Lê JSONL com tolerância a linhas inválidas.
    max_lines é guardrail para não rebentar memória.
    """
    meta: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "lines_read": 0,
        "lines_ok": 0,
        "lines_bad": 0,
        "truncated": False,
        "mtime": None,
        "size_bytes": None,
        "error": None,
    }

    if not path.exists():
        return [], meta

    try:
        st = path.stat()
        meta["mtime"] = st.st_mtime
        meta["size_bytes"] = st.st_size
    except Exception:
        pass

    items: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                meta["lines_read"] = i
                if i > max_lines:
                    meta["truncated"] = True
                    break

                s = (line or "").strip()
                if not s:
                    continue

                try:
                    obj = json.loads(s)
                    if isinstance(obj, dict):
                        items.append(obj)
                        meta["lines_ok"] += 1
                    else:
                        meta["lines_bad"] += 1
                except Exception:
                    meta["lines_bad"] += 1
    except Exception as e:
        meta["error"] = str(e)

    return items, meta


def _filter_by_day(items: List[Dict[str, Any]], day: str) -> List[Dict[str, Any]]:
    """
    Filtra por dia (UTC) com heurísticas:
      - se houver 'ts' epoch -> converte UTC date
      - se houver 'time'/'datetime' string -> tenta prefixo day
    """
    d = _parse_yyyy_mm_dd(day)
    out: List[Dict[str, Any]] = []

    for it in items:
        ts = it.get("ts")
        if ts is not None:
            try:
                dt = datetime.utcfromtimestamp(float(ts))
                if dt.date() == d:
                    out.append(it)
                    continue
            except Exception:
                pass

        for k in ("datetime", "time", "date"):
            v = it.get(k)
            if isinstance(v, str) and v.startswith(day):
                out.append(it)
                break

    return out


def _summarize_events(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Resumo simples e útil já, sem depender de schema perfeito.
    Conta por event + retcodes (se existirem).
    """
    by_event: Dict[str, int] = {}
    retcodes: Dict[str, int] = {}

    orders_sent_guess = 0
    closes_guess = 0
    modifies_guess = 0

    for it in items:
        ev = str(it.get("event") or "unknown")
        by_event[ev] = by_event.get(ev, 0) + 1

        rc = None
        r = it.get("result")
        if isinstance(r, dict):
            rc = r.get("retcode", r.get("code"))
        if rc is None:
            rc = it.get("retcode", it.get("code"))
        if rc is not None:
            k = str(rc)
            retcodes[k] = retcodes.get(k, 0) + 1

        # heurísticas
        if "order_market" in ev or ev in ("order_ack", "order_modify_ack", "order_close_ack"):
            orders_sent_guess += 1
        if "close" in ev:
            closes_guess += 1
        if "modify" in ev:
            modifies_guess += 1

    return {
        "counts": {
            "events_total": len(items),
            "orders_sent_guess": orders_sent_guess,
            "modifies_guess": modifies_guess,
            "closes_guess": closes_guess,
        },
        "by_event": dict(sorted(by_event.items(), key=lambda kv: kv[0])),
        "retcodes": dict(sorted(retcodes.items(), key=lambda kv: kv[0])),
    }


def _identity_from_request(req: Request) -> Dict[str, Any]:
    """
    Identidade estável do terminal/conta que este bridge representa.
    - começa com ENV (meta_snapshot)
    - tenta enriquecer com diag() (se disponível)
    """
    ident = meta_snapshot()

    svc = getattr(req.app.state, "mt5", None)
    if svc:
        try:
            diag = svc.diag() or {}
            # normalizações “best effort” (não assume schema fixo)
            for k_src, k_dst in (
                ("account_login", "account_login"),
                ("login", "account_login"),
                ("server", "server"),
                ("terminal_id", "terminal_id"),
                ("build", "build"),
                ("data_path", "data_path"),
            ):
                v = diag.get(k_src)
                if v is not None and not ident.get(k_dst):
                    ident[k_dst] = v
        except Exception:
            pass

    ident["bridge_instance_id"] = bridge_instance_id()
    return ident


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@router.get("/where")
def reporting_where(request: Request):
    """
    Debug: diz onde está a procurar o ledger e qual instance_id.
    """
    ident = _identity_from_request(request)
    inst = str(ident.get("bridge_instance_id") or "default")

    base = _resolve_ledger_base_dir()
    f = _ledger_file_for_instance(base, inst)

    return {
        "ok": True,
        "identity": ident,
        "ledger_base": str(base),
        "ledger_file": str(f),
        "exists": f.exists(),
    }


@router.get("/summary")
def reporting_summary(
    request: Request,
    day: Optional[str] = Query(default=None, description="YYYY-MM-DD (UTC)"),
    max_lines: int = Query(200000, ge=1000, le=2000000),
):
    """
    Resumo diário baseado no ledger JSONL (por bridge_instance_id).
    """
    day_s = day or _today_yyyy_mm_dd()

    ident = _identity_from_request(request)
    inst = str(ident.get("bridge_instance_id") or "default")

    base = _resolve_ledger_base_dir()
    ledger_file = _ledger_file_for_instance(base, inst)

    items, meta = _iter_jsonl(ledger_file, max_lines=max_lines)
    day_items = _filter_by_day(items, day_s)
    summary = _summarize_events(day_items)

    return {
        "ok": True,
        "day": day_s,
        "identity": ident,
        "ledger": meta,
        "summary": summary,
    }


@router.get("/events")
def reporting_events(
    request: Request,
    day: Optional[str] = Query(default=None, description="YYYY-MM-DD (UTC)"),
    limit: int = Query(500, ge=1, le=5000),
    event: Optional[str] = Query(None, description="filtra por event exacto"),
    max_lines: int = Query(200000, ge=1000, le=2000000),
):
    """
    Lista eventos do dia (para UI/diagnóstico).
    """
    day_s = day or _today_yyyy_mm_dd()

    ident = _identity_from_request(request)
    inst = str(ident.get("bridge_instance_id") or "default")

    base = _resolve_ledger_base_dir()
    ledger_file = _ledger_file_for_instance(base, inst)

    items, meta = _iter_jsonl(ledger_file, max_lines=max_lines)
    day_items = _filter_by_day(items, day_s)

    if event:
        day_items = [x for x in day_items if str(x.get("event") or "") == event]

    # mais recentes primeiro (se tiver ts)
    def _ts(x: Dict[str, Any]) -> float:
        try:
            return float(x.get("ts") or 0.0)
        except Exception:
            return 0.0

    day_items.sort(key=_ts, reverse=True)

    return {
        "ok": True,
        "day": day_s,
        "identity": ident,
        "ledger": meta,
        "count": len(day_items),
        "items": day_items[:limit],
    }


@router.get("/mt5_snapshot")
def reporting_mt5_snapshot(request: Request):
    """
    Snapshot rápido do próprio terminal via MT5Service:
      - diag()
      - open positions count
    """
    svc = getattr(request.app.state, "mt5", None)
    if not svc:
        return {"ok": False, "reason": "svc_missing", "identity": meta_snapshot()}

    out: Dict[str, Any] = {"ok": True, "identity": _identity_from_request(request)}

    try:
        out["diag"] = svc.diag() or {}
    except Exception as e:
        out["diag_error"] = str(e)

    try:
        pos = svc.positions() or []
        out["open_positions"] = len(pos)
    except Exception as e:
        out["open_positions_error"] = str(e)

    return out