"""Provider-agnostic LLM client with a content-addressed cache.

Design constraints, all from the phase spec:

* **Optional.**  The deterministic pipeline completes with no provider
  configured.  When none is available every task is written to an
  ``LLM_PENDING`` queue and the affected records carry ``llm_status:
  "pending"`` — never a silent substitution of keyword matching.
* **Cacheable and replayable.**  The cache key is
  ``sha256(task | prompt_version | model | schema_version | payload)``.  Once a
  run has populated the cache, ``--replay`` reproduces it with no network and
  no API key at all, which is what makes a published claim reproducible by
  someone who does not have a key.
* **Free tier only.**  Provider, model and key all come from environment
  variables; nothing here can enable billing, and the budget guard stops the
  run rather than spending past it.
* **No secrets, no personal data.**  The redactor strips emails and
  token-shaped strings from every payload before it leaves the process, and
  refuses to send the fields named in ``llm.controls.redaction.forbidden_fields``.

Providers speak either the OpenAI chat-completions shape (OpenRouter, Groq) or
Gemini's ``generateContent``.  Both are handled here so task code never learns
which one is in play.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..config import Phase2Config, iso, now
from ..ids import canonical_json, sha256_text
from ..store import read_json, write_json

log = logging.getLogger("impact2.llm")

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"
)


class LLMUnavailable(RuntimeError):
    """No provider is configured, or the budget is exhausted."""


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    input_tokens_estimated: int = 0
    output_tokens_estimated: int = 0
    errors: int = 0
    retries: int = 0
    sleep_seconds: float = 0.0
    json_schema_fallbacks: int = 0
    # `openrouter/free` routes to a different model per call, so which models
    # actually answered is provenance the run report must carry.
    models_used: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "json_schema_fallbacks": self.json_schema_fallbacks,
            "models_used": dict(sorted(self.models_used.items())),
            "input_tokens_estimated": self.input_tokens_estimated,
            "output_tokens_estimated": self.output_tokens_estimated,
            "errors": self.errors,
            "retries": self.retries,
            "sleep_seconds": round(self.sleep_seconds, 2),
            "estimated_cost_usd": 0.0,
            "cost_note": (
                "Free-tier endpoints only. No billing is configured and the "
                "client cannot enable it."
            ),
        }


def redact(payload: Any, forbidden: Sequence[str]) -> Any:
    """Strip anything that must never reach a third party."""
    if isinstance(payload, Mapping):
        return {
            k: redact(v, forbidden)
            for k, v in payload.items()
            if k not in set(forbidden)
        }
    if isinstance(payload, (list, tuple)):
        return [redact(v, forbidden) for v in payload]
    if isinstance(payload, str):
        cleaned = EMAIL_RE.sub("[email-redacted]", payload)
        return TOKEN_RE.sub("[token-redacted]", cleaned)
    return payload


@dataclass
class LLMClient:
    config: Phase2Config
    provider: str | None
    model: str | None
    api_key: str = field(repr=False, default="")
    replay_only: bool = False
    usage: Usage = field(default_factory=Usage)
    pending: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_call: float = field(default=0.0, repr=False)
    last_routed_model: str | None = None

    # -- construction ----------------------------------------------------
    @classmethod
    def build(cls, config: Phase2Config, *, replay_only: bool | None = None) -> "LLMClient":
        providers = config.get("llm.providers")
        preference = config.get("llm.provider_preference")
        requested = (os.getenv("LLM_PROVIDER") or "").strip().lower() or None

        replay = (
            replay_only
            if replay_only is not None
            else str(os.getenv(str(config.get("llm.cache.replay_only_env")), ""))
            .strip().lower() in {"1", "true", "yes"}
        )

        order = [requested] if requested else list(preference)
        for name in order:
            spec = providers.get(name)
            if not spec:
                continue
            key = os.getenv(str(spec["api_key_env"]), "").strip()
            if not key and not replay:
                continue
            model = (
                os.getenv(str(spec.get("model_env") or "LLM_MODEL"), "").strip()
                or str(spec["default_model"])
            )
            log.info(
                "LLM provider=%s model=%s (key present: %s, replay_only=%s)",
                name, model, bool(key), replay,
            )
            return cls(config=config, provider=name, model=model, api_key=key,
                       replay_only=replay)

        log.warning(
            "No LLM provider configured. The deterministic pipeline will run in "
            "full and every semantic task will be written to the LLM_PENDING "
            "queue. Set one of %s to enable the semantic layer.",
            [providers[p]["api_key_env"] for p in preference if p in providers],
        )
        return cls(config=config, provider=None, model=None, replay_only=replay)

    @property
    def available(self) -> bool:
        return bool(self.provider and (self.api_key or self.replay_only))

    # -- cache -------------------------------------------------------------
    def cache_path(self, key: str) -> Path:
        root = self.config.paths.llm_cache
        return root / key[:2] / f"{key}.json"

    def cache_key(self, task: str, payload: Mapping[str, Any]) -> str:
        prompt_version = str(self.config.get(f"llm.tasks.{task}.prompt_version"))
        return sha256_text(
            canonical_json(
                {
                    "task": task,
                    "prompt_version": prompt_version,
                    "model": self.model or "none",
                    "provider": self.provider or "none",
                    "payload": payload,
                }
            )
        )

    # -- the one entry point -----------------------------------------------
    def complete(
        self,
        *,
        task: str,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        payload: Mapping[str, Any],
        record_pending: bool = True,
    ) -> dict[str, Any] | None:
        """Return parsed JSON, or None when the task must be queued instead."""
        key = self.cache_key(task, payload)
        path = self.cache_path(key)
        cached = read_json(path, None)
        if cached is not None:
            self.usage.cache_hits += 1
            return cached.get("response")

        if not self.available or self.replay_only:
            if record_pending:
                self._queue(task, key, payload, reason=(
                    "replay-only mode and no cache entry" if self.replay_only
                    else "no LLM provider configured"
                ))
            return None

        budget = self.config.get("llm.budget")
        if self.usage.calls >= int(budget["max_calls_per_run"]):
            if record_pending:
                self._queue(task, key, payload, reason="per-run call budget exhausted")
            if budget.get("stop_on_budget_exceeded") and not budget.get("fail_open"):
                raise LLMUnavailable("LLM call budget exhausted")
            return None

        try:
            response = self._request(system=system, user=user, schema=schema)
        except Exception as exc:  # noqa: BLE001 - the pipeline must not die here
            self.usage.errors += 1
            log.warning("LLM task %s failed: %s", task, str(exc)[:200])
            if record_pending:
                self._queue(task, key, payload, reason=f"request failed: {str(exc)[:160]}")
            return None

        write_json(
            path,
            {
                "cache_key": key,
                "task": task,
                "provider": self.provider,
                "model": self.model,
                # What actually answered. With `openrouter/free` this differs
                # from `model` and differs between calls, so a cached record
                # must name the model that produced it.
                "routed_model": self.last_routed_model,
                "prompt_version": str(self.config.get(f"llm.tasks.{task}.prompt_version")),
                "requested_at": iso(now()),
                "input_sha256": sha256_text(canonical_json(payload)),
                "response": response,
            },
        )
        return response

    def _queue(
        self, task: str, key: str, payload: Mapping[str, Any], *, reason: str
    ) -> None:
        self.pending.append(
            {
                "task": task,
                "cache_key": key,
                "reason": reason,
                "prompt_version": str(self.config.get(f"llm.tasks.{task}.prompt_version")),
                "input_sha256": sha256_text(canonical_json(payload)),
                "subject": payload.get("subject") or payload.get("episode_id")
                or payload.get("candidate_id"),
                "queued_at": iso(now()),
            }
        )

    # -- transport ---------------------------------------------------------
    def _throttle(self) -> None:
        rpm = float(self.config.get("llm.request.requests_per_minute"))
        if rpm <= 0:
            return
        interval = 60.0 / rpm
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < interval:
                delay = interval - elapsed
                self.usage.sleep_seconds += delay
                time.sleep(delay)
            self._last_call = time.monotonic()

    def _request(
        self, *, system: str, user: str, schema: Mapping[str, Any]
    ) -> dict[str, Any]:
        import requests

        spec = self.config.get(f"llm.providers.{self.provider}")
        request_cfg = self.config.get("llm.request")
        max_retries = int(request_cfg["max_retries"])
        backoff = float(request_cfg["backoff_base_seconds"])

        self.usage.input_tokens_estimated += (len(system) + len(user)) // 4

        for attempt in range(max_retries + 1):
            self._throttle()
            try:
                if self.provider == "gemini":
                    text = self._request_gemini(requests, spec, system, user, schema,
                                                request_cfg)
                else:
                    text = self._request_openai_shape(requests, spec, system, user,
                                                      schema, request_cfg)
                self.usage.calls += 1
                self.usage.output_tokens_estimated += len(text) // 4
                return _parse_json(text)
            except Exception:
                if attempt >= max_retries:
                    raise
                self.usage.retries += 1
                delay = backoff * (2 ** attempt)
                self.usage.sleep_seconds += delay
                time.sleep(delay)
        raise LLMUnavailable("unreachable")

    def _request_openai_shape(
        self, requests: Any, spec: Mapping[str, Any], system: str, user: str,
        schema: Mapping[str, Any], request_cfg: Mapping[str, Any],
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **{str(k): str(v) for k, v in (spec.get("headers") or {}).items()},
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": float(request_cfg["temperature"]),
            "top_p": float(request_cfg["top_p"]),
            "max_tokens": int(request_cfg["max_output_tokens"]),
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "impact", "strict": True, "schema": schema},
            },
        }
        timeout = float(request_cfg["timeout_seconds"])
        url = f"{spec['base_url']}{spec['endpoint']}"
        response = requests.post(url, headers=headers, json=body, timeout=timeout)

        # Many free endpoints — and `openrouter/free`, which routes to whichever
        # free model is available — do not implement strict json_schema. Retry
        # once without it: the prompt already demands JSON and `_parse_json`
        # tolerates a reasoning preamble, which reasoning models emit.
        if response.status_code in (400, 404, 422):
            body.pop("response_format", None)
            self.usage.json_schema_fallbacks += 1
            response = requests.post(url, headers=headers, json=body, timeout=timeout)

        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise LLMUnavailable(str(data["error"])[:200])
        # `openrouter/free` resolves to a different model per call, so the model
        # that actually answered is provenance, not a constant. Every record
        # carries it; the run report lists the full set that was used.
        routed = str(data.get("model") or self.model)
        self.usage.models_used[routed] = self.usage.models_used.get(routed, 0) + 1
        self.last_routed_model = routed
        return data["choices"][0]["message"]["content"]

    def _request_gemini(
        self, requests: Any, spec: Mapping[str, Any], system: str, user: str,
        schema: Mapping[str, Any], request_cfg: Mapping[str, Any],
    ) -> str:
        endpoint = str(spec["endpoint"]).replace("{model}", str(self.model))
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": float(request_cfg["temperature"]),
                "topP": float(request_cfg["top_p"]),
                "maxOutputTokens": int(request_cfg["max_output_tokens"]),
                "responseMimeType": "application/json",
                "responseSchema": _gemini_schema(schema),
            },
        }
        response = requests.post(
            f"{spec['base_url']}{endpoint}",
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self.api_key},
            json=body, timeout=float(request_cfg["timeout_seconds"]),
        )
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    # -- reporting ---------------------------------------------------------
    def report(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "model_is_router": str(self.model or "").endswith("/free"),
            "models_actually_used": dict(sorted(self.usage.models_used.items())),
            "routing_note": (
                "`openrouter/free` selects an available free model per call, so "
                "the responding model varies within a run. Every cached record "
                "names the model that produced it. This is why the deterministic "
                "band stays authoritative and why repeatability is measured "
                "rather than assumed."
            ) if str(self.model or "").endswith("/free") else None,
            "available": self.available,
            "replay_only": self.replay_only,
            "api_key_present": bool(self.api_key),
            "cache_directory": str(
                self.config.paths.llm_cache.relative_to(self.config.paths.project_root)
            ),
            "usage": self.usage.as_dict(),
            "pending_tasks": len(self.pending),
            "prompt_versions": {
                name: str(self.config.get(f"llm.tasks.{name}.prompt_version"))
                for name in self.config.get("llm.tasks")
            },
            # Never, under any circumstance, the key itself.
            "api_key": "[never recorded]",
        }


def _parse_json(text: str) -> dict[str, Any]:
    """Parse a model response that should be JSON but might be fenced."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _gemini_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Gemini's response schema is JSON-Schema-like but rejects some keywords."""
    drop = {"additionalProperties", "$schema", "title", "strict"}
    def clean(node: Any) -> Any:
        if isinstance(node, Mapping):
            return {
                k: clean(v) for k, v in node.items()
                if k not in drop
            }
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node
    return clean(schema)
