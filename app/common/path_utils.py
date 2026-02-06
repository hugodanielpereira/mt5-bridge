# app/common/path_utils.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]

def norm_path(p: PathLike, *, base: Optional[Path] = None) -> Path:
    """
    Normaliza paths vindos de ENV/ficheiros:
    - Aceita Linux (/home/...)
    - Aceita Windows/Wine (Z:\\home\\..., C:\\Trading\\...)
    - Se relativo, resolve contra `base` (default: cwd)
    """
    s = str(p or "").strip()
    if not s:
        return (base or Path.cwd()).resolve()

    # linux abs
    if s.startswith("/"):
        return Path(s).expanduser().resolve()

    # windows abs com drive
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].upper()
        rest = s[2:].lstrip("\\/").replace("\\", "/")

        # Wine Z: -> /
        if drive == "Z":
            return Path("/" + rest).resolve()

        # fallback: trata como relativo
        b = (base or Path.cwd()).resolve()
        return (b / rest).resolve()

    # alguns outputs vêm tipo "\\home\\user\\..." (strings com barras escapadas)
    if s.startswith("\\\\") or s.startswith("\\"):
        s2 = s.replace("\\", "/")
        if s2.startswith("//"):
            s2 = s2[1:]
        return Path(s2).expanduser().resolve()

    # relativo
    b = (base or Path.cwd()).resolve()
    return (b / s).expanduser().resolve()


def _ascend_to_repo_root(start: Path) -> Path:
    cur = start
    while cur.parent != cur:
        if (cur / "apps").exists() or (cur / "bridges").exists() or (cur / "logs").exists():
            return cur
        cur = cur.parent
    return start


def project_root() -> Path:
    """
    Raiz do mono-repo (Trading).
    Aceita:
      - TRADING_ROOT / PROJECT_ROOT
      - PROJECT_DIR (pode ser Trading ou apps/ml-strategy-lab; fazemos subida)
    """
    hint = (os.getenv("TRADING_ROOT") or os.getenv("PROJECT_ROOT") or os.getenv("PROJECT_DIR") or "").strip()
    if hint:
        p = norm_path(hint, base=Path.cwd())
    else:
        p = Path(__file__).resolve()

    if p.is_file():
        p = p.parent

    return _ascend_to_repo_root(p)


def apps_root() -> Path:
    """
    Raiz do MLSL dentro do mono-repo:
      <Trading>/apps/ml-strategy-lab

    Permite override via APPS_ROOT / MLSL_APPS_DIR / PROJECT_DIR.
    """
    env = (os.getenv("APPS_ROOT") or os.getenv("MLSL_APPS_DIR") or os.getenv("PROJECT_DIR") or "").strip()
    if env:
        p = norm_path(env, base=project_root())
        # se apontar para Trading, ajusta para apps/ml-strategy-lab
        if (p / "apps" / "ml-strategy-lab").exists():
            return (p / "apps" / "ml-strategy-lab").resolve()
        return p.resolve()

    return (project_root() / "apps" / "ml-strategy-lab").resolve()


def bridge_root() -> Path:
    """
    Raiz do bridge:
      <Trading>/bridges/mt5-bridge
    """
    env = (os.getenv("BRIDGE_ROOT") or "").strip()
    if env:
        return norm_path(env, base=project_root())
    return (project_root() / "bridges" / "mt5-bridge").resolve()


def logs_dir() -> Path:
    """
    Logs centralizados em <Trading>/logs
    - LOG_DIR override opcional
    """
    root = project_root()
    return norm_path(os.getenv("LOG_DIR", str(root / "logs")), base=root)