"""LLM query rewriter — copied from geofind/geofind/rewrite.py.

Adapted for GEOSearch:
- Imports llm from search.llm (not geofind's internal module)
- GEO_SEARCH.md path points to geofind/GEO_SEARCH.md (shared reference doc)
- prune_collisions() is included but its `count` argument must be supplied
  by the caller (GEOSearch does not use the geofind FTS5 index)

Public API used by GEOSearch:
    from search.rewrite import rewrite, plan_from_concepts, filters_from

    rw = rewrite("CAR-T exhaustion single-cell")
    # rw["concepts"]        -> list of {concept, type, role, names}
    # rw["interpretation"]  -> "CAR-T cell exhaustion studied by scRNA-seq"
    # rw["criteria"]        -> "Samples must be CAR-T cells showing exhaustion..."
    # rw["constraints"]     -> ["CAR-T cells (cell type: ...)", "exhaustion (...)"]

    # Structured filter extraction:
    geos_filters = {}
    for org in (rw_obj.get("organism") or []):
        geos_filters.setdefault("organisms", []).append(org)
    date = rw_obj.get("date")   # e.g. "2023:3000" → parse for date_range filter
    min_s = rw_obj.get("min_samples")
    if min_s:
        geos_filters["min_samples"] = min_s
"""

import os
import re

from search.llm import LLMError, call_json

CONCEPT_TYPES = ("gene", "molecule", "disease", "tissue", "cell_type",
                 "perturbation", "design", "assay", "other")
ROLES = ("topic", "modifier", "exclude")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["interpretation", "criteria", "concepts", "organism",
                 "dataset_types", "min_samples", "date"],
    "properties": {
        "interpretation": {
            "type": "string",
            "description": "One sentence: how the query was read, with "
                           "abbreviations expanded and typos corrected.",
        },
        "criteria": {
            "type": "string",
            "description": "One to three sentences: what the deposited samples "
                           "of a relevant dataset must be, and what on the topic "
                           "does not count. Only what the query requires.",
        },
        "concepts": {
            "type": "array",
            "description": "The concepts the query names, the topic first.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["concept", "type", "role", "names"],
                "properties": {
                    "concept": {"type": "string",
                                "description": "The concept in a few words."},
                    "type": {"type": "string", "enum": list(CONCEPT_TYPES)},
                    "role": {"type": "string", "enum": list(ROLES),
                             "description": "topic: what the datasets are "
                                            "about; modifier: narrows the "
                                            "topic; exclude: what the user "
                                            "does not want."},
                    "names": {"type": "array", "items": {"type": "string"},
                              "description": "Every name a depositor might "
                                             "have written, plainest first."},
                },
            },
        },
        "organism": {"type": "array", "items": {"type": "string"},
                     "description": "Binomials, only when the query implies "
                                    "them; empty otherwise."},
        "dataset_types": {"type": "array", "items": {"type": "string"},
                          "description": "Exact GEO DataSet Type values, only "
                                         "when the query names a reliably "
                                         "typed assay; empty otherwise."},
        "min_samples": {"type": ["integer", "null"],
                        "description": "Smallest sample count asked for, else null."},
        "date": {"type": ["string", "null"],
                 "description": "2025, 2025/06 or 2024/01:2025/12 when the "
                                "query asks for a period; an open end written "
                                "out, 2025:3000 for '2025 or later', 1900:2019 "
                                "for 'before 2020'; else null."},
    },
}

DATASET_TYPES = (
    "Expression profiling by high throughput sequencing",
    "Expression profiling by array",
    "Genome binding/occupancy profiling by high throughput sequencing",
    "Other",
    "Non-coding RNA profiling by high throughput sequencing",
    "Methylation profiling by high throughput sequencing",
    "Non-coding RNA profiling by array",
    "Methylation profiling by genome tiling array",
    "Genome binding/occupancy profiling by genome tiling array",
    "Methylation profiling by array",
    "Genome variation profiling by genome tiling array",
    "Genome variation profiling by SNP array",
    "Expression profiling by RT-PCR",
    "SNP genotyping by SNP array",
    "Genome variation profiling by array",
    "Expression profiling by genome tiling array",
    "Third-party reanalysis",
    "Protein profiling by protein array",
    "Genome variation profiling by high throughput sequencing",
    "Genome binding/occupancy profiling by array",
    "Expression profiling by SAGE",
    "Non-coding RNA profiling by genome tiling array",
    "Genome binding/occupancy profiling by SNP array",
    "Expression profiling by MPSS",
    "Methylation profiling by SNP array",
    "Expression profiling by SNP array",
    "Protein profiling by Mass Spec",
)
_TYPE_BY_LOWER = {t.lower(): t for t in DATASET_TYPES}
_DATE = re.compile(r"^\d{4}(/\d{2})?(:\d{4}(/\d{2})?)?$")

# GEO_SEARCH.md lives in geofind/ — path relative to this file
HERE = os.path.dirname(os.path.abspath(__file__))
REFERENCE_PATH = os.path.join(HERE, "..", "geofind", "GEO_SEARCH.md")


def load_reference(path=REFERENCE_PATH):
    """Load the GEO_SEARCH.md reference doc that forms the first part of
    every rewrite prompt. Cached at module load so it is prompt-cached by
    the Azure endpoint across calls."""
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


REFERENCE = load_reference()

TASK = """\
# Task

A scientist typed the words in the **Query** section below into a search \
box for NCBI GEO, the public repository of genomics datasets. Read them as \
the scientist meant them (abbreviations expanded, typos corrected) and \
return the concepts they name and the filters they ask for, following the \
reference above. The service writes the Entrez term from your answer.

# How the answer is used

- Each concept becomes one OR group of its names, every name quoted and \
matched exactly (no stemming), the groups ANDed. A `topic` concept is what \
the datasets are about (the disease, gene, molecule, cell type, tissue or \
process); every topic group is in every search. A `modifier` narrows the \
topic (a perturbation, a study design, an assay, a tissue or cell type the \
topic is studied in): every series matching the filters and all the topic \
groups is a candidate, ranked by how many modifier groups it also matches, \
and a budget of about 250 is filled from the best tier down, newest first \
within a tier. A modifier the depositor phrased differently costs a series \
its tier, not its place. An `exclude` concept is not searched; the judge \
applies it. The plainest name of each group is also searched as untagged \
words with NCBI's own term mapping, with the filters, and up to 50 series \
that adds are included.
- A later step judges every candidate against the request with every \
concept you return as a constraint, and shows the scientist the relevant \
ones newest first. So a concept left out is never searched and never \
enforced; a modifier that is too narrow costs nothing. But a topic group \
that is too narrow, or a group whose names bind two things into one \
phrase, loses every series whose depositor wrote it otherwise, and nothing \
recovers them. Return every concept the query names, and none it does not, \
one thing per concept (the reference's Rules).
- `organism`, `dataset_types`, `min_samples` and `date` are filters and \
always apply. Set them only when the query says so (reference rules 1 and \
6), with `dataset_types` values copied exactly from the reference list.

# Names

For each concept give every name a depositor might have written in a \
series title, summary, overall design or sample source -- eight to fifteen \
as a rule (reference rule 2): the full name, its abbreviation and the \
abbreviation's expansions, noun and adjective forms, fused spellings, \
singular and plural, former and alternative names, gene aliases and \
previous symbols, cell lines or models that are the topic, platform and \
product names for an assay. The plainest common name first. Each name is \
a unit depositors write as such; a phrase that joins the concept to \
another concept's words (the cell type to its state, the molecule to \
"stimulation", the disease to a tissue) is not a name of either. A state \
or process concept (activation, senescence, exhaustion, polarization) is \
named by its bare noun and adjective first (`senescence`, `senescent`), \
its own phrases after (`senescence-associated`, `SASP`); a phrase that \
puts the cell type back in front of it (`fibroblast senescence`) belongs \
to neither concept and is left out. No generic \
words (rule 4), no misspellings, no names that mostly mean something else \
(rule 7), no related-but-different things (rule 2).

# Criteria

`criteria` is what the judge reads as the query's requirement, in one to \
three sentences: what the deposited samples of a relevant dataset must be, \
and what on the same topic does not count. Say how each modifier is met -- a \
time course means two or more times after the stimulus compared, a dose \
response several doses, single-cell means single-cell or single-nucleus \
transcriptomes (a multiome series that deposits the RNA modality counts; an \
ATAC-only or other chromatin-only series does not, even with a companion \
RNA series, and neither do bulk and spatial), patients means human patient \
samples rather than cell lines. Require only what the query \
says; do not add an assay, tissue or design the scientist did not ask for. \
A query that names a gene, protein or molecule wants it as the variable \
studied: perturbed (knockdown, knockout, overexpression, an inhibitor or \
agonist), or the samples stratified on it (high versus low, positive versus \
negative), or its regulation or targets the question. A transcriptome that \
measures it among all genes, or reports it as a marker or readout of some \
other perturbation, does not count; say so in the criteria. A query that \
asks for a tissue (lung tissue, brain, biopsies, postmortem) wants the \
tissue itself -- biopsies, resections, explants, autopsy samples, or cells \
dissociated from it and profiled at once; cells isolated from the tissue \
and cultured (primary fibroblasts, epithelial cultures, organoids, iPSC \
derivatives) are not the tissue, and the criteria say so. An `exclude` \
concept is met only by a series with none of the excluded thing among its \
samples; a series that mixes it with the wanted samples (tissues plus cell \
lines, Visium plus another platform) is borderline, and the criteria say \
so. A platform or product the query names (10x Genomics, EPIC array, \
Smart-seq2) must be stated in the series; a matching assay on an unstated \
platform is borderline. Example for \
"SOX2 expression": "SOX2 must be the variable studied: SOX2-high versus \
SOX2-low tumours or cells, SOX2 knockdown, knockout or overexpression, or \
SOX2 regulation. SOX2 as one of several markers in a sample table, as a \
stemness readout of another perturbation, or as a clinical covariate does \
not count." \
Example for "time course of LPS response in macrophages": "The deposited \
samples must be macrophages (monocyte-derived, tissue-resident, iPSC-derived \
or a macrophage-differentiated line) stimulated with LPS and profiled at two \
or more times after stimulation, compared with each other or with an \
unstimulated zero point. A single post-LPS time point, a dose series at one \
time, or LPS given only as a comparator does not count."

# Check before answering

For each topic concept, look at its names. If every name carries the same \
qualifier that is a class word (tumor, cancer, malignant, human, primary, \
patient-derived, 3D) or the same second thing (a state, a stimulus, \
"signaling", "stimulation", "positive"), the concept is not atomic: the \
thing alone, in its bare names, is the topic, and the qualifier or second \
thing is a modifier of its own with its own bare names (reference Rules, \
opening paragraphs; rule 2; rule 5). A bare distinguishing word (`spatial`, \
`inflamed`, `lesional`) is a name only when another topic group anchors the \
term; in a one-topic query it is left out (rule 1). An abbreviation that is \
also a word or another thing (ALL, MS, PD, RA, rule 7) is never bare. A drug, compound or molecule the query \
names is a topic whatever the phrasing ("effect of X on Y cells" is X the \
topic, the cells a modifier, "effect" nothing; rule 5). Two kinds of phrase are units and \
stay: a specific tissue or organ in front of the thing (kidney organoids, \
cardiac fibroblasts), and a named disease subtype or entity with its own \
abbreviation (non-small cell lung cancer, high-grade serous ovarian cancer, \
acute kidney injury; rule 3 keeps the user's specificity).

# Output

JSON with `interpretation`, `criteria`, `concepts` (topic first, then the \
modifiers in order of importance, then exclusions), `organism`, \
`dataset_types`, `min_samples`, `date`.

# Query

"""

PROMPT = REFERENCE + "\n\n" + TASK

STOPWORDS = set("""a an the of in on for to and or with from by at as is are be
data dataset datasets study studies expression i need want looking which what
that this those these show me find profiles profile please""".split())

# Abbreviations that collide with common GEO words in a bare search.
AMBIGUOUS_BARE = {"ra", "cd", "hd", "uc", "mi", "ms", "pd", "ad", "er", "all"}

# Temperature for the rewrite call (only sent when effort="none").
TEMPERATURE = os.environ.get("GEOSEARCH_REWRITE_TEMPERATURE", "0")

# Collision ratio: a single-word synonym matching >4x the rest of its group
# is treated as a collision with another meaning and dropped.
COLLISION_RATIO = float(os.environ.get("GEOSEARCH_COLLISION_RATIO", "4"))

_TOKEN = re.compile(r'"[^"]*"|\[[^\]]*\]|\(|\)|[^\s()\[\]"]+')
_OPERATORS = {"AND", "OR", "NOT"}
_RANGE = re.compile(r"^[0-9][0-9/]*:[0-9][0-9/]*$")


def fallback_terms(words):
    """When the LLM is unavailable: the query words as required tokens,
    filler removed. Returns list of one Entrez-style term string."""
    toks = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9'+\-]*", words)
            if t.lower() not in STOPWORDS]
    if not toks:
        toks = re.findall(r"\w+", words) or [words.strip() or "GEO"]
    return [" AND ".join(f'"{t}"[All Fields]' for t in toks)]


def _clean_names(names):
    out, seen = [], set()
    for n in names or []:
        n = " ".join(str(n).replace('"', " ").replace("[", " ").replace("]", " ").split())
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    kept = [n for n in out if n.lower() not in AMBIGUOUS_BARE]
    return kept or out


def group_of(names):
    return "(" + " OR ".join(f'"{n}"[All Fields]' for n in names) + ")"


def filters_from(obj):
    """Entrez filter conjuncts from the rewriter's structured fields.
    Returns list of filter strings; malformed values are dropped silently."""
    out = []
    orgs = _clean_names(obj.get("organism") or [])
    if orgs:
        atoms = [f'"{o}"[Organism]' for o in orgs]
        out.append(atoms[0] if len(atoms) == 1 else "(" + " OR ".join(atoms) + ")")
    types = []
    for t in _clean_names(obj.get("dataset_types") or []):
        t = _TYPE_BY_LOWER.get(t.lower())
        if t and t not in types:
            types.append(t)
    if types:
        atoms = [f'"{t}"[DataSet Type]' for t in types]
        out.append(atoms[0] if len(atoms) == 1 else "(" + " OR ".join(atoms) + ")")
    n = obj.get("min_samples")
    if isinstance(n, int) and not isinstance(n, bool) and n > 1:
        out.append(f"{n}:100000[Number of Samples]")
    d = (obj.get("date") or "").strip() if isinstance(obj.get("date"), str) else ""
    if d and _DATE.match(d):
        out.append(f"{d}[Publication Date]")
    return out


def _groups_from_concepts(concepts):
    """(topic_groups, modifier_groups, constraint_strings) from concepts list.
    exclude-role concepts become constraint strings only (not searched)."""
    topics, mods, cons = [], [], []
    for c in concepts or []:
        if not isinstance(c, dict):
            continue
        names = _clean_names(c.get("names"))
        if not names:
            continue
        label = " ".join(str(c.get("concept") or names[0]).split())
        ctype = str(c.get("type") or "other").replace("_", " ")
        shown = ", ".join(names[:4]) + (", ..." if len(names) > 4 else "")
        if c.get("role") == "exclude":
            cons.append(f"NOT {label} ({shown})")
            continue
        (topics if c.get("role") == "topic" else mods).append(group_of(names))
        cons.append(f"{label} ({ctype}: {shown})")
    if not topics and mods:
        topics.append(mods.pop(0))
    return topics, mods, cons


def plan_from_concepts(obj):
    """Build a plan dict from the LLM's structured output dict.

    Returns:
        {
            "concepts": list,          # raw concept dicts
            "interpretation": str,
            "criteria": str,
            "constraints": list[str],  # readable constraint list for judge
            "topic_names": list[str],  # first name of each topic concept
            "exclude_names": list[str],# first name of each exclude concept
            "raw": dict,               # the original LLM output object
        }
    """
    concepts = [c for c in (obj.get("concepts") or []) if isinstance(c, dict)]
    topics, mods, cons = _groups_from_concepts(concepts)

    topic_names = []
    exclude_names = []
    for c in concepts:
        names = _clean_names(c.get("names"))
        if not names:
            continue
        if c.get("role") == "topic":
            topic_names.append(names[0])
        elif c.get("role") == "exclude":
            exclude_names.append(names[0])

    return {
        "concepts": concepts,
        "interpretation": (obj.get("interpretation") or "").strip(),
        "criteria": " ".join(str(obj.get("criteria") or "").split()),
        "constraints": cons,
        "topic_names": topic_names,
        "exclude_names": exclude_names,
        "raw": obj,
    }


def rewrite(words, effort="none", timeout=12.0, temperature=None):
    """Call the LLM to parse a natural language GEO query into structured concepts.

    Returns a plan dict (see plan_from_concepts) plus "usage".
    Falls back to raw words as tokens if the LLM fails — never raises.

    Args:
        words: The user's query string.
        effort: LLM reasoning effort ("none" = fast/cheap, "low", "medium").
        timeout: Request timeout in seconds.
        temperature: Sampling temperature (only used when effort="none").

    Returns:
        {
            "concepts": list[dict],
            "interpretation": str,
            "criteria": str,
            "constraints": list[str],
            "topic_names": list[str],
            "exclude_names": list[str],
            "raw": dict,
            "usage": dict,
            "fallback": bool,   # True when LLM failed and raw words were used
        }
    """
    prompt = PROMPT + "```\n" + words.strip() + "\n```\n"
    if temperature is None:
        temperature = float(TEMPERATURE) if TEMPERATURE != "" else None

    try:
        obj, usage = call_json(prompt, SCHEMA, "rewrite",
                               effort=effort, max_output_tokens=4096,
                               timeout=timeout, temperature=temperature)
    except (LLMError, ValueError):
        # Fallback: return empty plan so caller proceeds with raw query
        return {
            "concepts": [],
            "interpretation": "",
            "criteria": "",
            "constraints": [],
            "topic_names": [],
            "exclude_names": [],
            "raw": {},
            "usage": {},
            "fallback": True,
        }

    plan = plan_from_concepts(obj if isinstance(obj, dict) else {})
    plan["usage"] = usage
    plan["fallback"] = False
    return plan


def prune_collisions(concepts, count_fn, filters=None, ratio=COLLISION_RATIO):
    """Drop single-word synonym names that collide with a more common meaning.

    For each concept, if a single-word name (not the first/canonical name)
    matches >ratio times more datasets than all other names combined, it is
    dropped. The first name is always kept.

    Args:
        concepts: List of concept dicts from the rewriter.
        count_fn: Callable(entrez_term: str) -> int — counts matching datasets.
                  In GEOSearch this would query Postgres FTS or Milvus.
        filters: Optional list of Entrez filter strings to AND with each count.
        ratio: Drop threshold (default 4.0).

    Returns:
        (pruned_concepts, dropped)
        dropped is a list of {"concept", "name", "alone", "rest"} dicts.
    """
    filters = list(filters or [])
    dropped, out = [], []
    for c in concepts or []:
        if not isinstance(c, dict) or c.get("role") == "exclude":
            out.append(c)
            continue
        names = _clean_names(c.get("names"))
        keep = list(names)
        for i, n in enumerate(names):
            if i == 0 or len(n.split()) != 1:
                continue
            rest = [m for m in keep if m != n]
            if not rest:
                continue
            filter_prefix = " AND ".join(filters) + " AND " if filters else ""
            alone = count_fn(filter_prefix + f'"{n}"[All Fields]')
            others = count_fn(filter_prefix + group_of(rest))
            if others and alone > ratio * others:
                keep.remove(n)
                dropped.append({"concept": str(c.get("concept") or names[0]),
                                "name": n, "alone": alone, "rest": others})
        out.append(dict(c, names=keep) if keep != names else c)
    return out, dropped
