"""
LLM-powered Q&A over GEO search results.

Provider priority:
  1. Ollama  — local, free, no API key (LLM_PROVIDER=ollama)
  2. OpenAI  — cloud, requires OPENAI_API_KEY (LLM_PROVIDER=openai)
  3. Fallback — structured bullet-point summary (no LLM)

Set LLM_PROVIDER in .env to choose. Default: "ollama" if Ollama is reachable,
otherwise "openai" if key present, otherwise fallback.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 8000
_CONTEXT_RESULTS = 10

SYSTEM_PROMPT = (
    "You are a biomedical research assistant specialising in NCBI GEO datasets. "
    "Answer the user's question using ONLY the GEO datasets provided as context. "
    "Be concise (3-5 sentences). Cite dataset accessions (e.g. GSE12345) inline. "
    "If the datasets do not contain enough information to answer, say so clearly."
)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(question: str, results: list[dict[str, Any]]) -> str:
    lines = [f"User question: {question}\n", "--- GEO Datasets ---"]
    total = 0
    for i, r in enumerate(results[:_CONTEXT_RESULTS], start=1):
        block = (
            f"\n[{i}] {r.get('accession', '')} — {r.get('title', '')}\n"
            f"  Organism: {', '.join(r.get('organisms') or [])} | "
            f"Tech: {r.get('tech_type', '')} | "
            f"Samples: {r.get('sample_count', '')} | "
            f"Date: {(r.get('submission_date') or '')[:10]}\n"
            f"  Summary: {(r.get('summary') or '')[:400]}\n"
        )
        total += len(block)
        if total > _MAX_CONTEXT_CHARS:
            break
        lines.append(block)
    lines.append("--- End of Datasets ---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------

def answer_with_ollama(
    question: str,
    results: list[dict[str, Any]],
    model: str = "llama3",
    base_url: str = "http://localhost:11434",
) -> str:
    """
    Call a locally running Ollama instance.
    Ollama exposes an OpenAI-compatible /v1 endpoint since v0.1.24.
    """
    from openai import OpenAI

    client = OpenAI(base_url=f"{base_url}/v1", api_key="ollama")
    context = _build_context(question, results)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
        temperature=0.2,
        max_tokens=600,
    )
    return response.choices[0].message.content.strip()


def ollama_reachable(base_url: str = "http://localhost:11434") -> bool:
    """Return True if Ollama is running and reachable."""
    import urllib.request
    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=2)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# OpenAI (cloud)
# ---------------------------------------------------------------------------

def answer_with_openai(
    question: str,
    results: list[dict[str, Any]],
    api_key: str,
    model: str = "gpt-4o-mini",
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    context = _build_context(question, results)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
        temperature=0.2,
        max_tokens=600,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Fallback (no LLM)
# ---------------------------------------------------------------------------

def answer_without_llm(question: str, results: list[dict[str, Any]]) -> str:
    if not results:
        return "No datasets matched your query."

    lines = [f"**Top {min(5, len(results))} datasets for:** {question}\n"]
    for r in results[:5]:
        acc = r.get("accession", "")
        url = r.get("geo_url", f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}")
        orgs = ", ".join(r.get("organisms") or [])
        lines.append(
            f"- **[{acc}]({url})** — {r.get('title', '')}  \n"
            f"  {orgs} | {r.get('sample_count', '')} samples"
        )

    lines.append(
        "\n*No LLM configured. Set `LLM_PROVIDER=ollama` (local) or add "
        "`OPENAI_API_KEY` to `.env` for AI-generated answers.*"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_answer(
    question: str,
    results: list[dict[str, Any]],
    llm_provider: str = "auto",
    llm_model: str | None = None,
    openai_api_key: str | None = None,
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "llama3",
) -> tuple[str, str]:
    """
    Generate an answer grounded in the search results.

    Args:
        question:        User's natural language question
        results:         Search result dicts from HybridSearchEngine
        llm_provider:    "auto" | "ollama" | "openai" | "none"
        llm_model:       Override model name (provider-specific)
        openai_api_key:  OpenAI API key (None = skip OpenAI)
        ollama_base_url: Ollama server URL
        ollama_model:    Default Ollama model

    Returns:
        (answer_text, provider_used)
        provider_used is one of: "ollama", "openai", "none"
    """
    if not results:
        return answer_without_llm(question, results), "none"

    # Resolve "auto": prefer Ollama if running, else OpenAI if key present
    if llm_provider == "auto":
        if ollama_reachable(ollama_base_url):
            llm_provider = "ollama"
        elif openai_api_key:
            llm_provider = "openai"
        else:
            llm_provider = "none"

    if llm_provider == "ollama":
        model = llm_model or ollama_model
        try:
            answer = answer_with_ollama(question, results, model=model, base_url=ollama_base_url)
            return answer, "ollama"
        except Exception as exc:
            logger.warning(f"Ollama Q&A failed ({exc}), trying OpenAI fallback")
            llm_provider = "openai"  # cascade

    if llm_provider == "openai":
        if not openai_api_key:
            logger.warning("LLM_PROVIDER=openai but OPENAI_API_KEY not set")
        else:
            model = llm_model or "gpt-4o-mini"
            try:
                answer = answer_with_openai(question, results, openai_api_key, model)
                return answer, "openai"
            except Exception as exc:
                logger.warning(f"OpenAI Q&A failed: {exc}")

    return answer_without_llm(question, results), "none"
