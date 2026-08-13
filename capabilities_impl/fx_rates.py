"""Live foreign-exchange reference rates from the European Central Bank, via
the Frankfurter API (https://frankfurter.dev) — the one piece of FinGuru's
data that genuinely comes from a free, official, machine-readable source.

Deliberate constraints, because a live call is the easiest place for a wrong
number to enter an answer:

- **Allowlisted host.** Only _API_BASE is ever contacted. This capability
  cannot be steered into fetching an arbitrary URL, so a prompt-injected
  "look up the rate at <attacker-url>" has nowhere to go.
- **Fails closed.** Any timeout, non-200, or unparseable body returns
  `available: False` with a reason. It never falls back to a remembered or
  approximated rate — FinGuru saying "I can't get that right now" is a
  correct answer; a stale number presented as live is not.
- **Short TTL cache.** ECB publishes once per working day around 16:00 CET,
  so a 1-hour cache costs nothing in freshness and keeps a chatty
  conversation from hammering the API.
- **Labelled as reference.** ECB rates are the daily reference fix, not the
  rate a bank or card gives a retail customer, which is typically 1.5-3%
  worse. The return value says so and the skill instructions require FinGuru
  to pass that caveat on.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests

_API_BASE = "https://api.frankfurter.app"
_SOURCE_NAME = "European Central Bank (via Frankfurter)"
_TIMEOUT_SECONDS = 10
_CACHE_TTL_SECONDS = 3600

# (base, target) -> (monotonic_deadline, payload). Guarded by a lock because
# FastAPI serves requests on a thread pool and two concurrent chats can hit
# the same pair at once.
_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _normalize(code: str) -> str:
    return str(code or "").strip().upper()


def _cached(key: tuple[str, str]) -> dict[str, Any] | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            return dict(hit[1], from_cache=True)
        if hit:
            del _cache[key]
    return None


def _store(key: tuple[str, str], payload: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, payload)


def get_fx_rate(base: str = "USD", target: str = "INR") -> dict:
    """Current ECB reference rate for one currency pair.

    Codes are ISO 4217 (USD, INR, EUR, GBP, AED, SGD...). Returns
    `available: False` rather than raising, so a failed lookup reaches the
    model as a tool result it can honestly report instead of aborting the run.
    """
    base_code, target_code = _normalize(base), _normalize(target)
    if len(base_code) != 3 or len(target_code) != 3:
        return {
            "available": False,
            "reason": f"Currency codes must be 3-letter ISO codes, got base={base!r} target={target!r}",
        }
    if base_code == target_code:
        return {
            "available": True, "base": base_code, "target": target_code, "rate": 1.0,
            "rate_date": None, "source_name": "identity", "source_url": None,
            "is_reference_rate": True, "from_cache": False,
            "caveat": "Same currency on both sides.",
        }

    key = (base_code, target_code)
    hit = _cached(key)
    if hit is not None:
        return hit

    url = f"{_API_BASE}/latest"
    try:
        response = requests.get(
            url, params={"base": base_code, "symbols": target_code}, timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return {"available": False, "reason": f"Could not reach the exchange-rate service: {exc}"}

    if response.status_code != 200:
        return {
            "available": False,
            "reason": f"Exchange-rate service returned HTTP {response.status_code}",
            "source_url": url,
        }

    try:
        body = response.json()
        rate = body["rates"][target_code]
        rate_date = body.get("date")
    except (ValueError, KeyError, TypeError):
        # Covers an unknown currency code too: the API omits it from `rates`
        # rather than erroring, so a KeyError here is the "no such pair" path.
        return {
            "available": False,
            "reason": f"No published rate for {base_code}->{target_code}",
            "source_url": url,
        }

    payload = {
        "available": True,
        "base": base_code,
        "target": target_code,
        "rate": rate,
        "rate_date": rate_date,
        "source_name": _SOURCE_NAME,
        "source_url": _API_BASE,
        "is_reference_rate": True,
        "from_cache": False,
        "caveat": (
            "ECB daily reference rate. Banks, cards and remittance services apply a spread "
            "on top, so the rate actually received is typically 1.5-3% worse than this."
        ),
    }
    _store(key, payload)
    return payload
