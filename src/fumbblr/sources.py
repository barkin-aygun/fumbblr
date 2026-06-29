"""Resolve a replay *source* into ``(replay_dict, replay_id)``.

The core path is fully offline: a local ``.gz`` / ``.json`` replay file is loaded
directly, with no network access and without importing the fetch module at all.

Resolving a bare replay id, a match id, or a ``.jnlp`` launcher requires
downloading from FUMBBL -- that is the *optional* :mod:`fumbblr.fetch` feature,
imported lazily only when a non-file source is given (and gated by
``allow_fetch``).  So a deployment that only ever hands fumbblr local replay
files never exercises (or needs) any networking code.
"""
from __future__ import annotations

import re
from pathlib import Path

from .replay import load_replay

# Replay files we load directly, offline.  (.jnlp is a launcher, not a replay:
# it only names a replay id, so it goes through the fetch feature.)
REPLAY_SUFFIXES = (".gz", ".json")


def replay_id_from_path(path) -> str:
    """Best-effort replay id from a file name -- the digits in the stem
    (``replay_1901960.gz`` -> ``1901960``), or the bare stem if it has none."""
    stem = Path(path).stem
    return re.sub(r"\D", "", stem) or stem


def load_replay_file(path) -> tuple[dict, str]:
    """Offline core entry point: load a local ``.gz`` / ``.json`` replay and
    return ``(replay_dict, replay_id)``.

    Never touches the network and never imports :mod:`fumbblr.fetch`.  Raises
    ``FileNotFoundError`` / ``ValueError`` for a missing or non-replay path."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"replay file not found: {p}")
    if p.suffix not in REPLAY_SUFFIXES:
        raise ValueError(
            f"not a replay file ({' / '.join(REPLAY_SUFFIXES)} expected): {p}")
    return load_replay(p), replay_id_from_path(p)


def load_source(source, *, allow_fetch: bool = True) -> tuple[dict, str]:
    """Resolve any supported source to ``(replay_dict, replay_id)``.

    Supported sources:

    * a local ``.gz`` / ``.json`` replay file -> loaded **offline** (core path)
    * a FUMBBL ``.jnlp`` launcher file         -> replay id extracted, then fetched
    * a bare replay id (e.g. ``1901960``)       -> downloaded
    * a bare match  id (e.g. ``4701297``)       -> match -> replay, downloaded

    Only the first is offline.  The rest require the optional fetch feature; set
    ``allow_fetch=False`` (CLI: ``--no-fetch``) to forbid all network access and
    accept local replay files only.
    """
    s = str(source)
    p = Path(s)

    if p.exists() and p.suffix in REPLAY_SUFFIXES:
        return load_replay_file(p)

    if not allow_fetch:
        raise ValueError(
            f"{source!r} is not a local replay file and fetching is disabled; "
            f"pass a local {' / '.join(REPLAY_SUFFIXES)} replay file")

    # Optional feature: only imported when we actually need to download.
    from . import fetch
    return fetch.fetch_source(s)
