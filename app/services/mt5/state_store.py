# app/services/mt5/state_store.py
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class MT5WebState:
    # ---------------- meta/heartbeat ----------------
    last_heartbeat_ms: int = 0
    terminal_id: Optional[str] = None
    account_login: Optional[str] = None
    server: Optional[str] = None
    build: Optional[str] = None
    bridge_instance_id: Optional[str] = None

    # ---------------- snapshots ----------------
    account: Dict[str, Any] = field(default_factory=dict)
    positions: List[Dict[str, Any]] = field(default_factory=list)

    # ---------------- symbol caches ----------------
    quotes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    symbol_info: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ---------------- ohlcv cache ----------------
    ohlcv: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)  # key=f"{symbol}:{tf}" -> rows

    # ---------------- deal/order history (EA pushes periodically) ----
    deals_history: List[Dict[str, Any]] = field(default_factory=list)
    orders_history: List[Dict[str, Any]] = field(default_factory=list)
    deals_history_ts: int = 0
    orders_history_ts: int = 0

    # ---------------- command channel (Bridge -> EA) ----------------
    pending_commands: List[Dict[str, Any]] = field(default_factory=list)

    # canonical command registry: cmd_id -> command (+status)
    commands: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ---------------- results channel (EA -> Bridge) ----------------
    last_result_ms: int = 0
    results: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # cmd_id -> result dict

    def is_ea_connected(self, ttl_ms: int = 15_000) -> bool:
        if self.last_heartbeat_ms <= 0:
            return False
        return (_now_ms() - self.last_heartbeat_ms) <= int(ttl_ms)


class MT5StateStore:
    """
    Store em memória (thread-safe) para modo webrequest.

    - single-process (uvicorn 1 worker) ok.
    - se fores multi-worker/HA -> Redis (mesma API).
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._st = MT5WebState()

    # ------------------------------------------------------------------
    # Snapshot (safe copy)
    # ------------------------------------------------------------------
    def snapshot(self) -> MT5WebState:
        with self._lock:
            st = self._st
            return MT5WebState(
                last_heartbeat_ms=st.last_heartbeat_ms,
                terminal_id=st.terminal_id,
                account_login=st.account_login,
                server=st.server,
                build=st.build,
                bridge_instance_id=st.bridge_instance_id,
                account=dict(st.account or {}),
                positions=[dict(x) for x in (st.positions or []) if isinstance(x, dict)],
                quotes={k: dict(v) for k, v in (st.quotes or {}).items()},
                symbol_info={k: dict(v) for k, v in (st.symbol_info or {}).items()},
                ohlcv={k: [dict(x) for x in v] for k, v in (st.ohlcv or {}).items()},
                deals_history=[dict(x) for x in (st.deals_history or [])],
                orders_history=[dict(x) for x in (st.orders_history or [])],
                deals_history_ts=st.deals_history_ts,
                orders_history_ts=st.orders_history_ts,
                pending_commands=[dict(x) for x in (st.pending_commands or [])],
                commands={k: dict(v) for k, v in (st.commands or {}).items()},
                last_result_ms=st.last_result_ms,
                results={k: dict(v) for k, v in (st.results or {}).items()},
            )

    # ------------------------------------------------------------------
    # Internal cleanup
    # ------------------------------------------------------------------
    def _cleanup_locked(
        self,
        max_results: int = 2000,
        drop_results: int = 500,
        cmd_ttl_ms: int = 60 * 60 * 1000,  # 1h
        result_ttl_ms: int = 60 * 60 * 1000,  # 1h
    ) -> None:
        now = _now_ms()

        # drop old command registry entries (done/failed + ttl)
        if self._st.commands:
            to_del: List[str] = []
            for cid, cmd in self._st.commands.items():
                status = str(cmd.get("status") or "")
                ts = int(cmd.get("ts_ms") or 0)
                done_ts = int(cmd.get("done_ms") or 0)
                base = done_ts if done_ts > 0 else ts
                if base > 0 and (now - base) > cmd_ttl_ms and status in ("done", "failed", "expired"):
                    to_del.append(cid)
            for cid in to_del:
                self._st.commands.pop(cid, None)
                # resultados também podem ir embora
                self._st.results.pop(cid, None)

        # drop very old pending commands (safety)
        if self._st.pending_commands:
            fresh: List[Dict[str, Any]] = []
            for c in self._st.pending_commands:
                ts = int(c.get("ts_ms") or 0)
                if ts <= 0 or (now - ts) <= cmd_ttl_ms:
                    fresh.append(c)
                else:
                    cid = str(c.get("id") or "")
                    if cid:
                        # marca expired se existir no registry
                        if cid in self._st.commands:
                            self._st.commands[cid]["status"] = "expired"
                            self._st.commands[cid]["done_ms"] = now
            self._st.pending_commands = fresh

        # results cap hard (evitar crescer infinito)
        if len(self._st.results) > max_results:
            items = list(self._st.results.items())
            items.sort(key=lambda kv: int(kv[1].get("ts_ms") or 0))
            for k, _ in items[:drop_results]:
                self._st.results.pop(k, None)

        # drop results old by TTL (best effort)
        if self._st.results:
            to_del = []
            for cid, r in self._st.results.items():
                ts = int(r.get("ts_ms") or 0)
                if ts > 0 and (now - ts) > result_ttl_ms:
                    to_del.append(cid)
            for cid in to_del:
                self._st.results.pop(cid, None)

    # ------------------------------------------------------------------
    # Updaters: meta/snapshots (EA -> Bridge)
    # ------------------------------------------------------------------
    def update_heartbeat(self, meta: Dict[str, Any]) -> None:
        """
        Heartbeat source-of-truth:
        - meta["ts_ms"] (epoch ms)
        - meta["ts"] pode ser epoch seconds OU epoch ms (auto-detetado)
        - fallback: _now_ms()

        Isto evita erros quando o cliente manda ts em ms (como o teu curl).
        """
        meta = meta or {}

        ts_ms_val = meta.get("ts_ms")
        ts_val = meta.get("ts")

        now_ms = _now_ms()
        parsed_ms: int = 0

        def _to_int(x) -> int:
            return int(float(x))

        try:
            if ts_ms_val is not None:
                parsed_ms = _to_int(ts_ms_val)
            elif ts_val is not None:
                raw = _to_int(ts_val)
                # heurística: se for grande (>= 1e12), já é ms; senão é seconds
                parsed_ms = raw if raw >= 1_000_000_000_000 else raw * 1000
        except Exception:
            parsed_ms = 0

        if parsed_ms <= 0:
            parsed_ms = now_ms

        # sanity: se vier muito no futuro (> 24h), ignora
        if parsed_ms > now_ms + 24 * 60 * 60 * 1000:
            parsed_ms = now_ms

        with self._lock:
            self._st.last_heartbeat_ms = parsed_ms

            mapping = {
                "terminal_id": ("terminal_id", "terminal_name", "terminal"),
                "build": ("build", "terminal_build"),
                "account_login": ("account_login",),
                "server": ("server",),
                "bridge_instance_id": ("bridge_instance_id", "iid", "instance_id"),
            }

            for attr, keys in mapping.items():
                for k in keys:
                    v = meta.get(k)
                    if v is not None and str(v).strip() != "":
                        setattr(self._st, attr, str(v).strip())
                        break

            # Extract account data from heartbeat (EA sends balance/equity/etc.)
            _acct_fields = ("balance", "equity", "margin", "free_margin", "profit", "currency", "leverage")
            if any(meta.get(f) is not None for f in _acct_fields):
                acct: Dict[str, Any] = dict(self._st.account or {})
                for f in _acct_fields:
                    v = meta.get(f)
                    if v is not None:
                        acct[f] = v
                # also include login/server for completeness
                if self._st.account_login:
                    acct["login"] = self._st.account_login
                if self._st.server:
                    acct["server"] = self._st.server
                acct["ts_ms"] = parsed_ms
                self._st.account = acct

    def set_account(self, account: Dict[str, Any]) -> None:
        with self._lock:
            self._st.last_heartbeat_ms = _now_ms()
            self._st.account = dict(account or {})

    def set_positions(self, positions: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._st.last_heartbeat_ms = _now_ms()
            self._st.positions = [x for x in (positions or []) if isinstance(x, dict)]

    def set_quote(self, symbol: str, quote: Dict[str, Any]) -> None:
        sym = (symbol or "").strip()
        if not sym:
            return
        with self._lock:
            self._st.last_heartbeat_ms = _now_ms()
            q = dict(quote or {})
            q.setdefault("symbol", sym)
            q.setdefault("ts_ms", _now_ms())
            self._st.quotes[sym] = q

    def set_symbol_info(self, symbol: str, info: Dict[str, Any]) -> None:
        sym = (symbol or "").strip()
        if not sym:
            return
        with self._lock:
            self._st.last_heartbeat_ms = _now_ms()
            it = dict(info or {})
            it.setdefault("symbol", sym)
            it.setdefault("ts_ms", _now_ms())
            self._st.symbol_info[sym] = it

    def set_deals_history(self, deals: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._st.deals_history = [x for x in (deals or []) if isinstance(x, dict)]
            self._st.deals_history_ts = _now_ms()

    def set_orders_history(self, orders: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._st.orders_history = [x for x in (orders or []) if isinstance(x, dict)]
            self._st.orders_history_ts = _now_ms()

    def set_ohlcv(self, symbol: str, tf: str, rows: List[Dict[str, Any]]) -> None:
        sym = (symbol or "").strip()
        t = (tf or "").upper().strip()
        if not sym or not t:
            return
        key = f"{sym}:{t}"
        with self._lock:
            self._st.last_heartbeat_ms = _now_ms()
            self._st.ohlcv[key] = [x for x in (rows or []) if isinstance(x, dict)]

    # ------------------------------------------------------------------
    # Command Queue: Bridge -> EA
    # ------------------------------------------------------------------
    def enqueue_command(self, cmd: Optional[Dict[str, Any]] = None, *, kind: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Suporta 2 estilos (compat):

        A) antigo: enqueue_command(cmd_dict) -> meta
        B) novo:   enqueue_command(kind="order_market", payload={...}) -> meta

        meta devolvido:
          {"id": "...", "type": "...", "status": "queued", "ts_ms": ...}
        """
        with self._lock:
            c: Dict[str, Any] = {}
            if isinstance(cmd, dict):
                c.update(cmd)
            if isinstance(payload, dict):
                c.update(payload)
            if kind:
                c["type"] = kind

            cid = str(c.get("id") or _new_id())
            c["id"] = cid
            c.setdefault("ts_ms", _now_ms())
            c.setdefault("type", c.get("kind") or "unknown")
            c.pop("kind", None)

            # registry canonical
            reg = dict(c)
            reg.setdefault("status", "queued")
            reg.setdefault("queued_ms", c["ts_ms"])
            self._st.commands[cid] = reg

            # queue
            self._st.pending_commands.append(dict(c))

            self._cleanup_locked()
            return {"id": cid, "type": str(reg.get("type")), "status": str(reg.get("status")), "ts_ms": int(reg.get("ts_ms") or 0)}

    def pop_commands(self, max_n: int = 20, max_age_ms: int = 60_000) -> List[Dict[str, Any]]:
        """
        Remove e devolve até max_n comandos pendentes.
        Descarta comandos muito antigos (max_age_ms).
        Marca como 'sent' no registry quando forem devolvidos.
        """
        max_n = max(1, min(int(max_n), 200))
        max_age_ms = max(5_000, int(max_age_ms))
        now = _now_ms()

        with self._lock:
            # drop velhos
            fresh: List[Dict[str, Any]] = []
            for c in self._st.pending_commands:
                ts = int(c.get("ts_ms") or 0)
                if ts <= 0 or (now - ts) <= max_age_ms:
                    fresh.append(c)
                else:
                    cid = str(c.get("id") or "")
                    if cid and cid in self._st.commands:
                        self._st.commands[cid]["status"] = "expired"
                        self._st.commands[cid]["done_ms"] = now
            self._st.pending_commands = fresh

            out = self._st.pending_commands[:max_n]
            self._st.pending_commands = self._st.pending_commands[max_n:]

            # mark sent
            for c in out:
                cid = str(c.get("id") or "")
                if cid and cid in self._st.commands:
                    self._st.commands[cid]["status"] = "sent"
                    self._st.commands[cid]["sent_ms"] = now

            self._cleanup_locked()
            return [dict(x) for x in out]

    def pending_count(self) -> int:
        with self._lock:
            return len(self._st.pending_commands)

    def get_command(self, cmd_id: str) -> Optional[Dict[str, Any]]:
        cid = (cmd_id or "").strip()
        if not cid:
            return None
        with self._lock:
            c = self._st.commands.get(cid)
            if not isinstance(c, dict):
                return None
            # anexar resultado se existir
            out = dict(c)
            r = self._st.results.get(cid)
            if isinstance(r, dict):
                out["result"] = dict(r)
            return out

    # ------------------------------------------------------------------
    # Results: EA -> Bridge
    # ------------------------------------------------------------------
    def set_result(self, cmd_id: str, result: Dict[str, Any]) -> None:
        cid = (cmd_id or "").strip()
        if not cid:
            return
        with self._lock:
            now = _now_ms()
            self._st.last_result_ms = now

            r = dict(result or {})
            r.setdefault("id", cid)
            r.setdefault("ts_ms", now)
            self._st.results[cid] = r

            # update command status
            if cid in self._st.commands and isinstance(self._st.commands[cid], dict):
                ok = r.get("ok")
                status = "done" if bool(ok) else "failed"
                self._st.commands[cid]["status"] = status
                self._st.commands[cid]["done_ms"] = now

            self._cleanup_locked()

    def get_result(self, cmd_id: str) -> Optional[Dict[str, Any]]:
        cid = (cmd_id or "").strip()
        if not cid:
            return None
        with self._lock:
            r = self._st.results.get(cid)
            return dict(r) if isinstance(r, dict) else None

    def clear_results(self) -> None:
        with self._lock:
            self._st.results.clear()
            self._st.last_result_ms = _now_ms()


# singleton simples
STORE = MT5StateStore()