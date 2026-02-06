# app/services/mt5/command_store.py
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return uuid.uuid4().hex


def _scope_from_env() -> str:
    return (
        (os.getenv("IID") or "").strip()
        or (os.getenv("INSTANCE_ID") or "").strip()
        or (os.getenv("BRIDGE_ID") or "").strip()
        or "default"
    )


@dataclass
class CommandMeta:
    id: str
    type: str
    status: str
    ts_ms: int
    scope: str


class CommandStore:
    """
    Command channel (Bridge -> EA) + Results channel (EA -> Bridge) com persistência.

    Backend:
      - Redis (recomendado)
      - fallback Memory (apenas se REDIS_URL não estiver definido)

    Keys (por scope):
      - queue:    mt5:{scope}:cmdq                  (Redis LIST)
      - cmd:      mt5:{scope}:cmd:{id}              (Redis STRING JSON)
      - result:   mt5:{scope}:result:{id}           (Redis STRING JSON)

    TTL:
      - comandos e resultados expiram (default 1h) para não crescer infinito
    """

    def __init__(
        self,
        *,
        backend: str,
        redis_url: Optional[str] = None,
        namespace: str = "mt5",
        default_scope: Optional[str] = None,
        cmd_ttl_sec: int = 3600,
        result_ttl_sec: int = 3600,
        max_pop: int = 200,
    ) -> None:
        self.backend = backend
        self.redis_url = redis_url
        self.ns = namespace
        self.default_scope = default_scope or _scope_from_env()
        self.cmd_ttl_sec = int(cmd_ttl_sec)
        self.result_ttl_sec = int(result_ttl_sec)
        self.max_pop = int(max_pop)

        self._r = None  # redis client (lazy)
        self._mem_cmdq: Dict[str, List[Dict[str, Any]]] = {}
        self._mem_cmd: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._mem_res: Dict[Tuple[str, str], Dict[str, Any]] = {}

        if self.backend == "redis":
            if not self.redis_url:
                raise RuntimeError("CommandStore backend=redis but redis_url is missing")
            self._ensure_redis()

    # --------------------------- factory ---------------------------

    @classmethod
    def from_env(cls) -> "CommandStore":
        redis_url = (os.getenv("REDIS_URL") or "").strip()
        namespace = (os.getenv("REDIS_NAMESPACE") or "mt5").strip() or "mt5"
        default_scope = _scope_from_env()

        cmd_ttl_sec = int(os.getenv("CMD_TTL_SEC", "3600"))
        result_ttl_sec = int(os.getenv("RESULT_TTL_SEC", "3600"))

        if redis_url:
            return cls(
                backend="redis",
                redis_url=redis_url,
                namespace=namespace,
                default_scope=default_scope,
                cmd_ttl_sec=cmd_ttl_sec,
                result_ttl_sec=result_ttl_sec,
            )

        # fallback: memory (dev only)
        return cls(
            backend="memory",
            redis_url=None,
            namespace=namespace,
            default_scope=default_scope,
            cmd_ttl_sec=cmd_ttl_sec,
            result_ttl_sec=result_ttl_sec,
        )

    # --------------------------- redis helpers ---------------------------

    def _ensure_redis(self) -> None:
        if self._r is not None:
            return
        try:
            import redis  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "REDIS_URL definido mas o pacote 'redis' não está instalado. "
                "Instala com: pip install redis"
            ) from e

        # decode_responses=True -> strings Python
        self._r = redis.Redis.from_url(self.redis_url, decode_responses=True)

        # sanity ping (falha cedo)
        try:
            self._r.ping()
        except Exception as e:
            raise RuntimeError(f"Não consegui conectar ao Redis em REDIS_URL={self.redis_url!r}") from e

    def _k_queue(self, scope: str) -> str:
        return f"{self.ns}:{scope}:cmdq"

    def _k_cmd(self, scope: str, cid: str) -> str:
        return f"{self.ns}:{scope}:cmd:{cid}"

    def _k_res(self, scope: str, cid: str) -> str:
        return f"{self.ns}:{scope}:result:{cid}"

    # --------------------------- public API ---------------------------

    def health(self) -> Dict[str, Any]:
        out = {
            "ok": True,
            "backend": self.backend,
            "redis_url": self.redis_url if self.backend == "redis" else None,
            "namespace": self.ns,
            "default_scope": self.default_scope,
            "cmd_ttl_sec": self.cmd_ttl_sec,
            "result_ttl_sec": self.result_ttl_sec,
        }
        if self.backend == "redis":
            self._ensure_redis()
            try:
                assert self._r is not None
                out["redis_ping"] = bool(self._r.ping())
            except Exception as e:
                out["ok"] = False
                out["redis_ping"] = False
                out["error"] = str(e)
        return out

    def enqueue(self, cmd: Dict[str, Any], *, scope: Optional[str] = None) -> CommandMeta:
        sc = (scope or cmd.get("scope") or self.default_scope or "default").strip() or "default"

        c = dict(cmd or {})
        cid = str(c.get("id") or _new_id())
        c["id"] = cid
        c.setdefault("ts_ms", _now_ms())
        c.setdefault("scope", sc)

        # normaliza type
        ctype = str(c.get("type") or c.get("kind") or "unknown").strip() or "unknown"
        c["type"] = ctype
        c.pop("kind", None)

        reg = dict(c)
        reg.setdefault("status", "queued")
        reg.setdefault("queued_ms", int(c["ts_ms"]))

        if self.backend == "redis":
            self._ensure_redis()
            assert self._r is not None
            kcmd = self._k_cmd(sc, cid)
            kq = self._k_queue(sc)

            payload = json.dumps(c, separators=(",", ":"), ensure_ascii=False)
            reg_json = json.dumps(reg, separators=(",", ":"), ensure_ascii=False)

            pipe = self._r.pipeline()
            pipe.set(kcmd, reg_json, ex=self.cmd_ttl_sec)
            pipe.rpush(kq, payload)
            pipe.execute()

            return CommandMeta(id=cid, type=ctype, status="queued", ts_ms=int(c["ts_ms"]), scope=sc)

        # memory
        self._mem_cmd[(sc, cid)] = reg
        self._mem_cmdq.setdefault(sc, []).append(c)
        return CommandMeta(id=cid, type=ctype, status="queued", ts_ms=int(c["ts_ms"]), scope=sc)

    def pop_for_ea(self, *, scope: Optional[str] = None, max_n: int = 20) -> List[Dict[str, Any]]:
        sc = (scope or self.default_scope or "default").strip() or "default"
        n = max(1, min(int(max_n), self.max_pop))
        now = _now_ms()

        if self.backend == "redis":
            self._ensure_redis()
            assert self._r is not None

            kq = self._k_queue(sc)

            out: List[Dict[str, Any]] = []
            pipe = self._r.pipeline()

            # LPOP em loop (n é pequeno, ok). Mantém ordem FIFO.
            for _ in range(n):
                pipe.lpop(kq)
            raw = pipe.execute()

            for item in raw:
                if not item:
                    continue
                try:
                    c = json.loads(item)
                    if isinstance(c, dict):
                        out.append(c)
                except Exception:
                    continue

            # marcar como 'sent' no registry
            if out:
                pipe2 = self._r.pipeline()
                for c in out:
                    cid = str(c.get("id") or "")
                    if not cid:
                        continue
                    kcmd = self._k_cmd(sc, cid)
                    reg = self.get_command(cid, scope=sc) or {}
                    reg["status"] = "sent"
                    reg["sent_ms"] = now
                    pipe2.set(kcmd, json.dumps(reg, separators=(",", ":"), ensure_ascii=False), ex=self.cmd_ttl_sec)
                pipe2.execute()

            return out

        # memory
        q = self._mem_cmdq.get(sc) or []
        out = q[:n]
        self._mem_cmdq[sc] = q[n:]
        for c in out:
            cid = str(c.get("id") or "")
            if cid and (sc, cid) in self._mem_cmd:
                self._mem_cmd[(sc, cid)]["status"] = "sent"
                self._mem_cmd[(sc, cid)]["sent_ms"] = now
        return [dict(x) for x in out]

    def pending_count(self, *, scope: Optional[str] = None) -> int:
        sc = (scope or self.default_scope or "default").strip() or "default"

        if self.backend == "redis":
            self._ensure_redis()
            assert self._r is not None
            return int(self._r.llen(self._k_queue(sc)) or 0)

        return len(self._mem_cmdq.get(sc) or [])

    def set_result(self, cmd_id: str, result: Dict[str, Any], *, scope: Optional[str] = None) -> None:
        cid = (cmd_id or "").strip()
        if not cid:
            return

        sc = (scope or (result.get("scope") if isinstance(result, dict) else None) or self.default_scope or "default").strip() or "default"
        now = _now_ms()

        r = dict(result or {})
        r.setdefault("id", cid)
        r.setdefault("scope", sc)
        r.setdefault("ts_ms", now)

        ok = bool(r.get("ok"))
        status = "done" if ok else "failed"

        if self.backend == "redis":
            self._ensure_redis()
            assert self._r is not None

            kres = self._k_res(sc, cid)
            kcmd = self._k_cmd(sc, cid)

            # atualiza cmd registry
            reg = self.get_command(cid, scope=sc) or {"id": cid, "scope": sc}
            reg["status"] = status
            reg["done_ms"] = now
            reg["result"] = r  # embed (conveniente p/ status endpoint)

            pipe = self._r.pipeline()
            pipe.set(kres, json.dumps(r, separators=(",", ":"), ensure_ascii=False), ex=self.result_ttl_sec)
            pipe.set(kcmd, json.dumps(reg, separators=(",", ":"), ensure_ascii=False), ex=self.cmd_ttl_sec)
            pipe.execute()
            return

        # memory
        self._mem_res[(sc, cid)] = r
        reg = self._mem_cmd.get((sc, cid)) or {"id": cid, "scope": sc}
        reg["status"] = status
        reg["done_ms"] = now
        reg["result"] = r
        self._mem_cmd[(sc, cid)] = reg

    def get_result(self, cmd_id: str, *, scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
        cid = (cmd_id or "").strip()
        if not cid:
            return None
        sc = (scope or self.default_scope or "default").strip() or "default"

        if self.backend == "redis":
            self._ensure_redis()
            assert self._r is not None
            raw = self._r.get(self._k_res(sc, cid))
            if not raw:
                return None
            try:
                obj = json.loads(raw)
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None

        r = self._mem_res.get((sc, cid))
        return dict(r) if isinstance(r, dict) else None

    def get_command(self, cmd_id: str, *, scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
        cid = (cmd_id or "").strip()
        if not cid:
            return None
        sc = (scope or self.default_scope or "default").strip() or "default"

        if self.backend == "redis":
            self._ensure_redis()
            assert self._r is not None
            raw = self._r.get(self._k_cmd(sc, cid))
            if not raw:
                return None
            try:
                obj = json.loads(raw)
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None

        c = self._mem_cmd.get((sc, cid))
        return dict(c) if isinstance(c, dict) else None