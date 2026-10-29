"""LLM judge for post-retrieval relevance filtering.

Copied from geofind/geofind/judge.py and adapted for GEOSearch:
- Imports llm from search.llm (not geofind's internal module)
- Items are GEOSearch result dicts (accession/title/summary) instead of
  geofind's annotated hit dicts — _fmt_item is simplified accordingly
- record/hybrid context modes removed (GEOSearch has title+summary only)
- fold_samples dependency removed

Usage:
    from search.judge import judge

    result = judge(
        query="CAR-T cell exhaustion",
        interpretation="CAR-T cell exhaustion in solid tumors",
        items=final_results[:150],       # GEOSearch result dicts
        constraints=["CAR-T cells", "exhaustion phenotype"],
        criteria="Samples must be CAR-T cells showing exhaustion markers...",
    )
    # result["calls"] -> {accession: {"tier": "relevant"|"borderline"|"not_relevant",
    #                                  "reason": "12-word fragment"}}
    # result["judged"] -> bool (False only when all batches failed)
"""

import concurrent.futures
import logging
import os
import time

from search.llm import LLMError, call_json

logger = logging.getLogger(__name__)

TIERS = ("relevant", "borderline", "not_relevant")

# Items per LLM call and concurrent workers.
# 150 results → 6 concurrent batches of 25 → ~2–4s wall time total.
BATCH_SIZE = int(os.environ.get("GEOSEARCH_JUDGE_BATCH", "25"))
MAX_WORKERS = int(os.environ.get("GEOSEARCH_JUDGE_WORKERS", "6"))

# How many chars of the summary each item carries in the judge prompt.
ABSTRACT_CHARS = int(os.environ.get("GEOSEARCH_JUDGE_ABSTRACT_CHARS", "1200"))

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["calls"],
    "properties": {
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["i", "reason", "label"],
                "properties": {
                    "i": {"type": "integer", "description": "index in the list"},
                    "reason": {"type": "string",
                               "description": "fragment, at most 12 words, "
                                              "naming the decisive detail"},
                    "label": {"type": "string", "enum": ["relevant", "borderline"]},
                },
            },
        },
    },
}

PROMPT_HEAD = """\
# Task
A scientist searched NCBI's GEO database with the query below, hoping to find \
relevant public datasets. Put yourself in their place: the query states the \
research question they are working on. Below is a numbered list of candidate \
datasets, each with its title, organism, sample count, and the start of its \
abstract. Decide for each whether it would be of interest to them for that \
question.

# Query
{query}

# How the query was read
{interpretation}
Constraints the query names: {constraints}
{criteria}
# Labels
- relevant: the dataset is directly about the query's topic. The deposited \
samples are of that topic, and studying it is what the study is about.
- borderline: the topic is present but the dataset is not directly about it: \
it appears only in a comparator arm, motivates the study without being in the \
samples, or the data are about a neighbouring topic.
- not_relevant: the dataset is not about the topic. It is absent from the \
samples and from the study's aims; a passing mention, a comorbidity field, or \
background framing does not make a dataset relevant. Mark all SuperSeries \
(series_type superseries) as not_relevant.

# Rules
The query alone defines the topic. It can be ambiguous; read it as the \
scientist meant it: a disease name asks for data on that disease in any \
assay. Do not add requirements the query does not make, but every one it \
does make is a requirement, and they are listed above: when the query names \
a data type, assay, tissue, cell type, sample source, organism, study design \
or accompanying data, a dataset on the right topic that does not meet it is \
borderline, not relevant, whatever else it has going for it. \
Bulk RNA-seq, microarray or TCR-seq of the right disease does not answer a \
query for single-cell RNA-seq; microarray does not answer a query for \
RNA-seq; skin does not answer a query for blood; \
monocyte-derived macrophages do not answer a query for PBMCs; organoids or \
cell lines do not answer a query for a tissue or for patients; a series with \
no outcome information does not answer a query asking for survival data. A \
named treatment, sample source or comparison is a constraint in its own \
right, not a class: vedolizumab or ustekinumab do not answer a query for \
anti-TNF; PBMCs or neutrophils do not answer a query for whole blood; a \
series with no aged arm does not answer a query for aging. A study design \
named in the query must be visible in the design or the sample table: one \
time point after a stimulus does not answer a query for a time course, and \
one dose does not answer a query for a dose response, however many \
replicates there are. A \
gene or protein named in the query is the topic only when the dataset is \
about it -- perturbed, stimulated with it, or the factor under study -- not \
when it is merely one of the genes measured or a marker mentioned in passing.

Weigh what the data actually are over what the abstract is about: a paper \
motivated by the topic that deposited samples of something else is not \
directly about it. Judge each dataset on its own terms; there is no quota of \
relevant or irrelevant results to hit. Where the record is thin, lean to \
borderline rather than guessing.

# Output
JSON with one list, "calls". Each entry is {{"i": <the number in square \
brackets>, "reason": <a fragment of at most 12 words naming the decisive \
detail -- the samples, assay, comparison or disease actually studied, and \
the constraint missed if one is, e.g. "lesional vs non-lesional skin, bulk \
RNA-seq" or "LPS at one time point only"; no full sentence, do not restate \
the title>, "label": "relevant" or "borderline"}}. Write the reason before \
the label and let the label follow from it: relevant when the deposited \
samples are of the topic, it is what the study is about, and every \
constraint is met; borderline when the topic is there but the dataset is \
not directly about it or misses a constraint the query makes. Every dataset \
not listed is not_relevant, so go through all {n} datasets (numbered 1 to \
{n}) and list every one where the topic is present. Leave out only datasets \
where the topic is absent. Use each index at most once.

# Datasets
"""

CRITERIA_TEXT = """
# What the query requires
{criteria}
A dataset on the topic that does not meet this is borderline; not_relevant \
is for datasets that are not about the topic at all.
"""


def _fmt_item(i, item, abstract_chars=ABSTRACT_CHARS):
    """Format one GEOSearch result dict for the judge prompt."""
    head = [item.get("accession", "")]
    org = item.get("organisms") or item.get("organism") or ""
    if isinstance(org, list):
        org = "; ".join(org[:2])
    if org:
        head.append(str(org))
    n = item.get("sample_count") or item.get("n_samples")
    if n:
        head.append(f"{n} samples")
    date = item.get("submission_date") or item.get("date") or ""
    if date:
        head.append(str(date)[:10])
    lines = [f"[{i}] " + " | ".join(str(x) for x in head)]
    if item.get("title"):
        lines.append(f"  title: {item['title']}")
    summary = " ".join((item.get("summary") or item.get("ncbi_summary") or "").split())
    if summary and abstract_chars:
        if len(summary) > abstract_chars:
            summary = summary[:abstract_chars].rsplit(" ", 1)[0] + " ..."
        lines.append(f"  abstract: {summary}")
    return "\n".join(lines)


def build_prompt(query, interpretation, items, constraints=None,
                 abstract_chars=ABSTRACT_CHARS, criteria=""):
    body = "\n".join(_fmt_item(i, it, abstract_chars)
                     for i, it in enumerate(items, 1))
    crit = CRITERIA_TEXT.format(criteria=" ".join(criteria.split())) if criteria and criteria.strip() else ""
    return PROMPT_HEAD.format(
        query=query.strip(),
        interpretation=interpretation or "(as written)",
        constraints="; ".join(constraints or []) or "(none beyond the topic)",
        criteria=crit,
        n=len(items),
    ) + body + "\n"


def _parse(obj, n):
    """{index: (tier, reason)} — first call for each index stands."""
    calls = {}
    entries = [(e.get("label") if isinstance(e, dict) else None, e)
               for e in obj.get("calls") or []]
    for tier in ("relevant", "borderline"):
        entries += [(tier, e) for e in obj.get(tier) or []]
    for tier, entry in entries:
        if tier not in ("relevant", "borderline"):
            continue
        try:
            i = int(entry.get("i"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= i <= n and i not in calls:
            calls[i] = (tier, str(entry.get("reason") or "").strip())
    return calls


def _judge_batch(query, interpretation, items, constraints, effort, timeout,
                 model, log, label="", criteria=""):
    """One LLM call over `items`, retried once on failure.
    Returns (calls_dict, usage, error, seconds).
    calls_dict is None when both attempts failed.
    """
    prompt = build_prompt(query, interpretation, items, constraints, criteria=criteria)
    n = len(items)
    max_out = 2000 + 40 * n
    last_err = ""
    started = time.monotonic()
    for attempt in range(2):
        t = time.monotonic()
        try:
            obj, usage = call_json(prompt, SCHEMA, "judge", effort=effort,
                                   max_output_tokens=max_out,
                                   timeout=timeout, model=model)
        except (LLMError, ValueError) as exc:
            last_err = str(exc)
            if log:
                logger.warning("judge batch %s: %d items, attempt %d failed in %.1fs: %s",
                               label, n, attempt + 1, time.monotonic() - t, exc)
            continue
        if log:
            logger.debug("judge batch %s: %d items, %.1fs%s, %d output tokens",
                         label, n, time.monotonic() - t,
                         ", attempt 2" if attempt else "",
                         usage.get("output_tokens", 0))
        by_index = _parse(obj, n)
        calls = {}
        for i, it in enumerate(items, 1):
            tier, reason = by_index.get(i, ("not_relevant", ""))
            calls[it["accession"]] = {"tier": tier, "reason": reason}
        return calls, usage, "", round(time.monotonic() - started, 2)
    return None, {}, last_err, round(time.monotonic() - started, 2)


def judge(query, interpretation, items, constraints=None, effort="none",
          timeout=15.0, model=None, batch_size=BATCH_SIZE, workers=MAX_WORKERS,
          log=True, criteria=""):
    """Judge GEOSearch result dicts in concurrent batches.

    Args:
        query: Original user query string.
        interpretation: One-sentence rewriter interpretation (or empty string).
        items: List of GEOSearch result dicts. Each must have "accession",
               "title", and "summary". Optional: "organisms", "sample_count",
               "submission_date".
        constraints: Short readable constraint list (from rewriter), shown to judge.
        effort: LLM reasoning effort — "none" (fast), "low", "medium".
        timeout: Per-batch timeout in seconds.
        model: Override Azure deployment name (default: GEOSEARCH_JUDGE_MODEL env var).
        batch_size: Items per LLM call (default: 25).
        workers: Concurrent batches (default: 6).
        log: Whether to emit debug logs for each batch.
        criteria: Rewriter's criteria string, shown to judge as ground truth.

    Returns:
        {
            "judged": bool,         # False only when every batch failed
            "calls": {              # accession -> tier + reason
                accession: {"tier": "relevant"|"borderline"|"not_relevant",
                            "reason": "12-word fragment"}
            },
            "usage": dict,          # summed token counts
            "error": str,           # joined errors from failed batches
            "unjudged": [str],      # accessions from failed batches
            "batch_seconds": [float],
        }
    """
    if not items:
        return {"judged": True, "calls": {}, "usage": {}, "error": "",
                "unjudged": [], "batch_seconds": []}

    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    calls, usage, errors, unjudged, seconds = {}, {}, [], [], []

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(workers, len(batches)))) as pool:
        futures = [pool.submit(_judge_batch, query, interpretation, b,
                               constraints, effort, timeout, model, log,
                               f"{k + 1}/{len(batches)}", criteria=criteria)
                   for k, b in enumerate(batches)]
        for batch, fut in zip(batches, futures):
            got, u, err, secs = fut.result()
            seconds.append(secs)
            if got is None:
                errors.append(err)
                unjudged.extend(it["accession"] for it in batch)
                continue
            calls.update(got)
            for k, v in u.items():
                usage[k] = usage.get(k, 0) + v

    judged = len(unjudged) < len(items)
    return {"judged": judged, "calls": calls, "usage": usage,
            "error": "; ".join(errors), "unjudged": unjudged,
            "batch_seconds": seconds}
