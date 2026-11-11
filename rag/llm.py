"""One interface over four LLM providers (groq | gemini | anthropic | openai).

    from rag.llm import complete, complete_json
    text = complete(system, user)                  # answer model
    data = complete_json(system, user, fast=True)  # small/fast model, JSON object out

Provider: LLM_PROVIDER in .env, else the first provider whose API key is set.
Every call is cached on disk (sqlite) by provider + model + prompts, so the eval
is reproducible and repeated demo questions don't spend free-tier quota.
Errors surface as LLMError so callers can degrade gracefully.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

import config

load_dotenv(config.ROOT / ".env")

PROVIDERS = ("groq", "gemini", "anthropic", "openai")
_KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class LLMError(RuntimeError):
    """Any provider failure (missing key, network, rate limit, bad JSON)."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: float
    cached: bool


# --- provider / model selection -------------------------------------------------

def get_provider() -> str:
    """LLM_PROVIDER if set, else the first provider with a key in the environment."""
    chosen = os.getenv("LLM_PROVIDER", "").strip().lower()
    if chosen:
        if chosen not in PROVIDERS:
            raise LLMError(f"LLM_PROVIDER={chosen!r} is not one of {PROVIDERS}")
        return chosen
    for p in PROVIDERS:
        if os.getenv(_KEY_ENV[p]):
            return p
    raise LLMError("No LLM key found. Copy .env.example to .env and set one provider's key.")


def get_model(fast: bool = False, provider: str | None = None) -> str:
    provider = provider or get_provider()
    override = os.getenv("LLM_FAST_MODEL" if fast else "LLM_MODEL")
    return override or config.LLM_DEFAULT_MODELS[provider]["fast" if fast else "answer"]


def describe() -> str:
    """'groq · openai/gpt-oss-120b', for the UI's provider indicator."""
    try:
        return f"{get_provider()} · {get_model()}"
    except LLMError as e:
        return f"not configured ({e})"


def _key(provider: str) -> str:
    key = os.getenv(_KEY_ENV[provider])
    if not key:
        raise LLMError(f"{_KEY_ENV[provider]} is not set in .env")
    return key


# --- disk cache -------------------------------------------------------------------

@lru_cache(maxsize=1)
def _db() -> sqlite3.Connection:
    config.LLM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.LLM_CACHE, check_same_thread=False)
    con.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, ts REAL)")
    return con


def _cache_key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


# --- provider calls ---------------------------------------------------------------

def _call_groq(model: str, system: str, user: str, json_mode: bool, max_tokens: int) -> str:
    from groq import Groq

    kwargs: dict[str, Any] = {}
    if model.startswith("openai/gpt-oss"):
        kwargs["reasoning_effort"] = "low"  # gpt-oss reasons first; "low" keeps latency ~1 s
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = Groq(api_key=_key("groq"), timeout=config.LLM_TIMEOUT_S).chat.completions.create(
        model=model, temperature=config.LLM_TEMPERATURE, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs,
    )
    return resp.choices[0].message.content or ""


def _call_openai(model: str, system: str, user: str, json_mode: bool, max_tokens: int) -> str:
    from openai import OpenAI

    kwargs: dict[str, Any] = {"response_format": {"type": "json_object"}} if json_mode else {}
    resp = OpenAI(api_key=_key("openai"), timeout=config.LLM_TIMEOUT_S).chat.completions.create(
        model=model, max_completion_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs,
    )
    return resp.choices[0].message.content or ""


def _call_gemini(model: str, system: str, user: str, json_mode: bool, max_tokens: int) -> str:
    from google import genai
    from google.genai import types

    cfg = types.GenerateContentConfig(
        system_instruction=system, temperature=config.LLM_TEMPERATURE, max_output_tokens=max_tokens,
        response_mime_type="application/json" if json_mode else None,
    )
    resp = genai.Client(api_key=_key("gemini")).models.generate_content(model=model, contents=user, config=cfg)
    return resp.text or ""


def _call_anthropic(model: str, system: str, user: str, json_mode: bool, max_tokens: int) -> str:
    import anthropic

    # Current Claude models reject non-default sampling params (temperature) with a 400,
    # so none is sent; JSON is requested in the system prompt and parsed below.
    if json_mode:
        system += "\n\nRespond with a single JSON object and nothing else."
    resp = anthropic.Anthropic(api_key=_key("anthropic"), timeout=config.LLM_TIMEOUT_S).messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        raise LLMError("model declined the request")
    return "".join(b.text for b in resp.content if b.type == "text")


_CALLERS = {"groq": _call_groq, "openai": _call_openai, "gemini": _call_gemini, "anthropic": _call_anthropic}


# --- public API -------------------------------------------------------------------

def complete(
    system: str, user: str, *, fast: bool = False, json_mode: bool = False, max_tokens: int = 1500
) -> LLMResponse:
    """One system + user turn → text. Cached on disk; raises LLMError on failure."""
    provider = get_provider()
    model = get_model(fast, provider)
    key = _cache_key(provider, model, system, user, json_mode, config.LLM_TEMPERATURE, max_tokens)
    if config.LLM_CACHE_ENABLED and config.LLM_CACHE_READ:
        row = _db().execute("SELECT v FROM cache WHERE k = ?", (key,)).fetchone()
        if row:
            return LLMResponse(row[0], provider, model, 0.0, cached=True)
    t0 = time.perf_counter()
    try:
        text = _CALLERS[provider](model, system, user, json_mode, max_tokens)
    except LLMError:
        raise
    except Exception as e:  # SDK-specific errors (rate limit, auth, network) → one type
        raise LLMError(f"{provider}/{model}: {type(e).__name__}: {e}") from e
    latency = round((time.perf_counter() - t0) * 1000, 1)
    if not text.strip():
        raise LLMError(f"{provider}/{model} returned an empty response")
    if config.LLM_CACHE_ENABLED:
        _db().execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, text, time.time()))
        _db().commit()
    return LLMResponse(text, provider, model, latency, cached=False)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating code fences or prose around it."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise LLMError(f"no JSON object in response: {text[:120]!r}") from None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise LLMError(f"invalid JSON in response: {e}") from e
    if not isinstance(data, dict):
        raise LLMError("expected a JSON object")
    return data


def complete_json(
    system: str, user: str, *, fast: bool = True, max_tokens: int = 1500
) -> tuple[dict[str, Any], LLMResponse]:
    """Like complete(), but returns the parsed JSON object as well as the raw response."""
    resp = complete(system, user, fast=fast, json_mode=True, max_tokens=max_tokens)
    return parse_json_object(resp.text), resp
