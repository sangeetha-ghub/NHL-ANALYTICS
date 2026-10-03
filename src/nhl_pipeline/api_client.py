"""Thin, polite client for the public NHL web API (no key required).

Features
--------
* one ``requests.Session`` (connection reuse)
* fixed delay between live requests (be nice to the API)
* retry with exponential back-off on 429 / 5xx / network errors
* every raw JSON response is written to disk (data/raw/api_cache) so the
  payloads can be inspected in the notebooks. Only immutable resources
  (box scores of finished games) are *read back* from that cache; mutable
  ones (standings, rosters, schedules, season stats) are always fetched live.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class NHLApiError(RuntimeError):
    """Raised when an endpoint cannot be fetched after all retries."""


class NHLClient:
    def __init__(
        self,
        base_url: str = config.BASE_URL,
        delay: float = config.REQUEST_DELAY_SEC,
        timeout: float = config.REQUEST_TIMEOUT_SEC,
        max_retries: int = config.MAX_RETRIES,
        cache_dir: Path | None = config.RAW_CACHE_DIR,
        use_cache: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.use_cache = use_cache and self.cache_dir is not None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "nhl-analytics-hub/1.0 (educational project)"})
        self._last_call = 0.0
        self.live_calls = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------ core
    def _cache_path(self, path: str) -> Path:
        safe = path.strip("/").replace("/", "__")
        return self.cache_dir / f"{safe}.json"  # type: ignore[operator]

    def get(self, path: str, read_cache: bool = False) -> dict[str, Any]:
        """GET ``{base_url}/{path}`` and return parsed JSON.

        ``read_cache=True`` serves the response from disk when present — only
        use it for resources that never change (e.g. a finished game's box score).
        """
        if self.use_cache and read_cache:
            cp = self._cache_path(path)
            if cp.exists():
                self.cache_hits += 1
                return json.loads(cp.read_text(encoding="utf-8"))

        url = f"{self.base_url}/{path.lstrip('/')}"
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            wait = self.delay - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(url, timeout=self.timeout)
                self._last_call = time.monotonic()
                self.live_calls += 1
                if resp.status_code in RETRY_STATUS:
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.RequestException, ValueError) as err:
                last_err = err
                status = getattr(getattr(err, "response", None), "status_code", None)
                if status is not None and status not in RETRY_STATUS:
                    raise NHLApiError(f"{url} -> HTTP {status}") from err
                backoff = min(2 ** attempt, 30)
                log.warning("GET %s failed (%s), retry %d/%d in %ss",
                            url, err, attempt, self.max_retries, backoff)
                time.sleep(backoff)
        else:
            raise NHLApiError(f"{url} failed after {self.max_retries} attempts") from last_err

        if self.use_cache:
            cp = self._cache_path(path)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(data), encoding="utf-8")
        return data

    # ------------------------------------------------------------- endpoints
    def standings(self) -> dict[str, Any]:
        return self.get("standings/now")

    def roster(self, team_abbrev: str, scope: str = config.ROSTER_SCOPE) -> dict[str, Any]:
        suffix = "current" if scope == "current" else config.SEASON
        return self.get(f"roster/{team_abbrev}/{suffix}")

    def schedule(self, team_abbrev: str, season: str = config.SEASON) -> dict[str, Any]:
        return self.get(f"club-schedule-season/{team_abbrev}/{season}")

    def boxscore(self, game_id: int) -> dict[str, Any]:
        # only called for finished games → safe to serve from cache
        return self.get(f"gamecenter/{game_id}/boxscore", read_cache=True)

    def club_stats(self, team_abbrev: str, season: str = config.SEASON, game_type: int = 2) -> dict[str, Any]:
        return self.get(f"club-stats/{team_abbrev}/{season}/{game_type}")
