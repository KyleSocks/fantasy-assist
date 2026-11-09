"""In-process caches keyed on file stamps.

The expensive work (projecting every player, valuing a league) depends only
on data/hockey.db and the YAML under config/, so it is cached against a
stamp of those files and recomputed when any of them changes.

The DB runs in WAL mode: a commit can land in ``hockey.db-wal`` without
touching ``hockey.db`` until a checkpoint, so its stamp covers both files.
Stamps are (mtime_ns, size) -- a rewrite within the same second still
changes mtime_ns, and a same-size rewrite still changes mtime.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Hashable

from src.paths import CONFIG_DIR


def file_stamp(*paths: str | os.PathLike) -> tuple:
    """(mtime_ns, size) per path; None for a missing file."""
    out = []
    for p in paths:
        try:
            st = os.stat(p)
        except FileNotFoundError:
            out.append(None)
        else:
            out.append((st.st_mtime_ns, st.st_size))
    return tuple(out)


def db_stamp(db_path: str | os.PathLike) -> tuple:
    """Stamp of a SQLite DB including its write-ahead log.

    An empty WAL (SQLite creates one as soon as any connection opens, and
    removes it when the last one closes) holds no data, so it stamps the
    same as no WAL -- otherwise merely having a connection open would look
    like a change and miss every cache.
    """
    main, wal = file_stamp(db_path, f"{db_path}-wal")
    if wal is not None and wal[1] == 0:
        wal = None
    return (main, wal)


def config_stamp(config_dir: Path | None = None) -> tuple:
    """Stamp of every YAML under config/ (league + model settings, which
    are bind-mounted and editable without a rebuild)."""
    root = config_dir or CONFIG_DIR
    files = sorted(root.rglob("*.yaml"))
    return tuple(zip((str(f) for f in files), file_stamp(*files)))


class Memo:
    """A small thread-safe LRU of computed values.

    The lock is held while computing, so concurrent requests for the same
    board wait for one build instead of each running it (the box is a
    ZimaBoard). Different Memo instances don't share a lock.
    """

    def __init__(self, maxsize: int = 8):
        self.maxsize = maxsize
        self._data: OrderedDict[Hashable, Any] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            value = compute()
            self._data[key] = value
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)
            return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_PROJECTIONS = Memo(maxsize=4)


def project_all_cached(season: int, db_path: str | os.PathLike) -> list:
    """project_all(), cached per (season, DB, config). Callers must treat
    the returned Projection objects as read-only: they are shared."""
    from src.projections.projections import project_all

    key = (season, str(db_path), db_stamp(db_path), config_stamp())
    return _PROJECTIONS.get(key, lambda: project_all(season, str(db_path)))


def clear_all() -> None:
    """Drop every cache in this module (tests)."""
    _PROJECTIONS.clear()
