"""One HTTP client for every outbound call.

- Retries 429 and 5xx with exponential backoff + jitter, honoring Retry-After.
- Never retries other 4xx (bad key, bad request): those fail fast with a clear error.
- Per-host concurrency cap so a fan-out doesn't hammer one API and get banned.
"""
from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from urllib.parse import urlparse

import httpx

from .config import settings

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504, 529}
import os

PER_HOST_LIMIT = int(os.environ.get("CONTAGION_PER_HOST_LIMIT", "4"))
# LLM calls are long (web search turns run 30 to 90 s); a cap of 4 serializes the run.
HOST_LIMITS = {"api.anthropic.com": int(os.environ.get("CONTAGION_LLM_CONCURRENCY", "10"))}


class UpstreamError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


_client: httpx.AsyncClient | None = None
_host_locks: dict[str, asyncio.Semaphore] = {}


def _lock(host: str) -> asyncio.Semaphore:
    if host not in _host_locks:
        _host_locks[host] = asyncio.Semaphore(HOST_LIMITS.get(host, PER_HOST_LIMIT))
    return _host_locks[host]


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
    return _client


async def aclose() -> None:
    if _client is not None:
        await _client.aclose()


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return min(float(value), 30.0)
    except ValueError:
        return None


async def request(
    method: str,
    url: str,
    *,
    attempts: int = 4,
    timeout: float | None = None,
    **kwargs,
) -> httpx.Response:
    host = urlparse(url).netloc
    last_err: Exception | None = None
    async with _lock(host):
        for attempt in range(1, attempts + 1):
            try:
                resp = await client().request(method, url, timeout=timeout or httpx.USE_CLIENT_DEFAULT, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = exc
                if attempt == attempts:
                    break
                await asyncio.sleep(_backoff(attempt))
                continue

            if resp.status_code < 400:
                return resp
            if resp.status_code in RETRY_STATUS and attempt < attempts:
                await asyncio.sleep(_retry_after(resp) or _backoff(attempt))
                continue
            body = resp.text[:300].replace("\n", " ")
            raise UpstreamError(f"{host} returned {resp.status_code}: {body}", resp.status_code)

    raise UpstreamError(f"{host} unreachable after {attempts} attempts: {last_err!r}")


def _backoff(attempt: int) -> float:
    return min(2 ** attempt, 20) * (0.5 + random.random() / 2)


async def get_json(url: str, **kwargs):
    resp = await request("GET", url, **kwargs)
    return resp.json()


async def post_json(url: str, payload: dict, **kwargs):
    resp = await request("POST", url, json=payload, **kwargs)
    return resp.json()
