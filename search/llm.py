"""Azure OpenAI JSON call helper — copied from geofind/geofind/llm.py.

Reads AZURE_API_KEY and AZURE_OPENAI_ENDPOINT from the environment (same
vars GEOSearch already uses for embeddings). Raises LLMError on any
unrecoverable failure; transport errors and 429/5xx are retried with
jittered backoff.
"""

import json
import os
import random
import time
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
# The /responses endpoint requires 2025-04-01-preview or later.
# AZURE_OPENAI_API_VERSION in .env may point to an older version for embeddings,
# so we use a separate var AZURE_OPENAI_RESPONSES_API_VERSION with a safe default.
API_VERSION = (
    os.environ.get("AZURE_OPENAI_RESPONSES_API_VERSION")
    or os.environ.get("AZURE_OPENAI_API_VERSION")
    or "2025-04-01-preview"
)
MODEL = os.environ.get("AZURE_OPENAI_CHAT_MODEL") or os.environ.get("AZURE_OPENAI_MODEL") or "gpt-4o"


class LLMError(RuntimeError):
    pass


def api_key():
    # Support both GEOSearch's AZURE_OPENAI_API_KEY and geofind's AZURE_API_KEY
    return os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_API_KEY", "")


def call_json(prompt, schema, name, effort="low", max_output_tokens=8192,
              timeout=60.0, max_tries=3, model=None, temperature=None):
    """POST `prompt` to the Azure OpenAI responses endpoint and return
    (parsed_json, usage). Raises LLMError on failure.

    `temperature` is sent only when `effort` is "none" — the deployment
    rejects it when reasoning is enabled.
    """
    key = api_key()
    if not key:
        raise LLMError("AZURE_API_KEY is not set")
    if not ENDPOINT:
        raise LLMError("AZURE_OPENAI_ENDPOINT is not set")

    body = {
        "model": model or MODEL,
        "input": [{"role": "user", "content": prompt}],
        "max_output_tokens": max_output_tokens,
        "text": {"format": {"type": "json_schema", "name": name,
                            "schema": schema, "strict": True}},
    }
    if effort:
        body["reasoning"] = {"effort": effort}
    if temperature is not None and effort == "none":
        body["temperature"] = float(temperature)

    url = f"{ENDPOINT}/openai/responses?api-version={API_VERSION}"
    last = None
    for attempt in range(1, max_tries + 1):
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
            return _extract(parsed), _usage(parsed)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            last = LLMError(f"HTTP {exc.code}: {detail}")
            if exc.code not in (408, 429) and exc.code < 500:
                raise last
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, LLMError) as exc:
            last = exc if isinstance(exc, LLMError) else LLMError(
                f"{type(exc).__name__}: {exc}")
        if attempt < max_tries:
            time.sleep(random.uniform(0.5, min(2 ** attempt, 6)))
    raise last


def _extract(parsed):
    text = parsed.get("output_text")
    if not text:
        chunks = []
        for item in parsed.get("output") or []:
            for part in item.get("content") or []:
                if part.get("text"):
                    chunks.append(part["text"])
        text = "\n".join(chunks)
    if not text:
        status = parsed.get("status", "unknown")
        reason = (parsed.get("incomplete_details") or {}).get("reason", "")
        raise LLMError(f"no text in response (status={status} {reason})")
    return json.loads(text)


def _usage(parsed):
    u = parsed.get("usage") or {}
    return {
        "input_tokens": u.get("input_tokens", 0),
        "cached_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "reasoning_tokens": (u.get("output_tokens_details") or {}).get(
            "reasoning_tokens", 0),
    }
