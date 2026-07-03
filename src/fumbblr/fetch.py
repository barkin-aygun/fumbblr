"""Optional feature: download a single FUMBBL replay over HTTP.

This module is the *only* part of fumbblr that touches the network, and it is
imported lazily by :mod:`fumbblr.sources` -- a file-only deployment never loads
it.  It is intentionally NOT a scraper: it fetches a single replay the user
explicitly points at, caches it on disk, and is polite (identifying User-Agent,
small inter-request delay).

A remote source is one of:

  * a FUMBBL ``.jnlp`` launcher file        -> replay id extracted, then fetched
  * a bare replay id (e.g. 1901960)          -> replay/get/{id}/gz
  * a bare match  id (e.g. 4701297)          -> match/get -> replayId -> replay

FUMBBL API (https://fumbbl.com/apidoc/):
  match/get/{matchId}        -> match meta incl. ``replayId``
  replay/get/{replayId}/gz   -> gzipped replay command stream
"""
from __future__ import annotations

import gzip
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .replay import load_replay

_UA = "fumbblr (bloodygit training-drill converter; single-replay fetch)"
_CACHE = Path.home() / ".cache" / "fumbblr"
_BASE = "https://fumbbl.com/api"

# Politeness: the FUMBBL site owner asked downloads be kept low, so fumbblr is
# deliberately slow.  Every request waits this long *after* completing, and
# transient failures (rate limiting, 5xx) back off exponentially rather than
# retrying in a tight loop.  Override the post-request delay with FUMBBLR_DELAY
# (seconds).  The default is intentionally leisurely -- the harvester is meant
# to trickle games in over days, not race.
_DELAY = float(os.environ.get("FUMBBLR_DELAY", "5.0"))
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 5


class RateLimited(RuntimeError):
    """Raised when FUMBBL keeps returning 429 after our backoff is exhausted.

    The harvester catches this to stop the current batch early and let the next
    scheduled run try again later, instead of hammering a server that has
    explicitly asked us to slow down."""


def _fetch_bytes(url: str, *, data: bytes | None = None,
                 headers: dict | None = None, timeout: int = 60) -> bytes:
    """Fetch ``url`` (GET, or POST if ``data`` is given) with a polite delay and
    exponential backoff on transient errors.  Raises :class:`RateLimited` if
    429s outlast our retries."""
    hdrs = {"User-Agent": _UA, **(headers or {})}
    last_429 = False
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if _DELAY:
                time.sleep(_DELAY)
            return body
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_STATUS or attempt == _MAX_RETRIES - 1:
                if e.code == 429:
                    raise RateLimited(f"429 Too Many Requests for {url}") from e
                raise
            last_429 = e.code == 429
            # Honour Retry-After when the server sends it; else exp backoff.
            retry_after = e.headers.get("Retry-After") if e.headers else None
            wait = float(retry_after) if (retry_after and retry_after.isdigit()) \
                else min(60.0, 2.0 ** attempt * max(1.0, _DELAY))
            time.sleep(wait)
        except urllib.error.URLError:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(min(60.0, 2.0 ** attempt * max(1.0, _DELAY)))
    raise RateLimited(f"gave up on {url}") if last_429 else RuntimeError(url)


def _api_bytes(path: str) -> bytes:
    return _fetch_bytes(f"{_BASE}/{path}")


def replay_id_from_jnlp(text: str) -> str | None:
    """Extract a replay id from a FUMBBL ``.jnlp`` launcher's contents.

    This step itself is offline (it just parses text); it lives here because the
    only reason to read a ``.jnlp`` is to then fetch the replay it names."""
    # href="/ffblive.jnlp?replay=1889874..."  or  <argument>1889874</argument>
    m = re.search(r"replay=(\d+)", text)
    if m:
        return m.group(1)
    m = re.search(r"-gameId\s*</argument>\s*<argument>\s*(\d+)", text)
    return m.group(1) if m else None


def fetch_replay_by_id(replay_id: str | int, *, cache=True) -> dict | None:
    """Fetch (and cache) replay/get/{replay_id}/gz. None if the server returns
    nothing (e.g. the id is actually a match id)."""
    replay_id = str(replay_id)
    _CACHE.mkdir(parents=True, exist_ok=True)
    cached = _CACHE / f"replay_{replay_id}.gz"
    if cache and cached.exists():
        return load_replay(cached)
    raw = _api_bytes(f"replay/get/{replay_id}/gz")
    if not raw:
        return None
    if cache:
        cached.write_bytes(raw)
    with gzip.open(io.BytesIO(raw), "rb") as f:
        return json.load(f)


def resolve_replay_id_from_match(match_id: str | int) -> str | None:
    meta = json.loads(_api_bytes(f"match/get/{match_id}").decode())
    rid = meta.get("replayId")
    return str(rid) if rid else None


def fetch_source(source) -> tuple[dict, str]:
    """Resolve a *remote* source -- a ``.jnlp`` launcher, a replay id, or a match
    id -- by downloading from FUMBBL.  Returns ``(replay_dict, replay_id)``.

    Local replay files are handled offline by :func:`fumbblr.sources.load_source`
    and never reach here."""
    s = str(source)
    p = Path(s)

    if p.exists() and p.suffix == ".jnlp":
        rid = replay_id_from_jnlp(p.read_text())
        if not rid:
            raise ValueError(f"no replay id found in {p}")
        replay = fetch_replay_by_id(rid)
        if replay is None:
            raise ValueError(f"FUMBBL returned no replay for id {rid}")
        return replay, rid

    if s.isdigit():
        # try as a replay id first; fall back to treating it as a match id
        replay = fetch_replay_by_id(s)
        if replay is not None:
            return replay, s
        rid = resolve_replay_id_from_match(s)
        if rid:
            replay = fetch_replay_by_id(rid)
            if replay is not None:
                return replay, rid
        raise ValueError(f"{s} is neither a fetchable replay id nor a match id")

    raise ValueError(f"unrecognised remote replay source: {source!r}")
