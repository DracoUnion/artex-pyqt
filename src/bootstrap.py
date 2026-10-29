"""
Application bootstrap for the ARTEX desktop shell.

Assembles the zero-Qt service graph (Gateway + EventHub + TaskManager) into
an :class:`AppContext` and returns it. The GUI receives the context; the
existing Gateway remains the HTTP-compatible adapter used by the UI pages.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from common.context import AppContext
from common.events import EventHub
from services.gateway import Gateway
from tasks import TaskManager


def base_dir() -> Path:
    """Application root (holds data/, skills/, workspace/)."""
    return Path(__file__).resolve().parent.parent


def default_data_dir() -> Path:
    d = base_dir() / "data"
    os.makedirs(d, exist_ok=True)
    return d


def default_skills_dir() -> Path:
    d = base_dir() / "skills"
    os.makedirs(d, exist_ok=True)
    return d


def build_context(
    *,
    data_dir: Optional[str] = None,
    skills_dir: Optional[str] = None,
    max_workers: int = 4,
    extras: Optional[dict[str, Any]] = None,
) -> AppContext:
    """Assemble services and return an :class:`AppContext`.

    ``data_dir`` / ``skills_dir`` may be overridden in tests with temporary
    directories; defaults mirror :func:`main`.`app_data_dir` / `skills_dir`.
    """
    data = Path(data_dir) if data_dir else default_data_dir()
    skills = Path(skills_dir) if skills_dir else default_skills_dir()
    events = EventHub()
    tasks = TaskManager(max_workers=max_workers, events=events)
    gateway = Gateway(str(data), str(skills))
    return AppContext(
        gateway=gateway,
        events=events,
        tasks=tasks,
        extras=extras or {},
    )