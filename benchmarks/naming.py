"""Generic, publishable names for the models under test.

Gateway ids often carry deployment details that must not leave the machine:
a provider namespace, an ``internal-``/``ext-`` prefix, or an opaque tier alias
such as ``openai-lite``. The report shows only the name this module derives:

1. an explicit name from the models file (``id = Name``) always wins;
2. otherwise the last path segment, with deployment prefixes and ``:latest``
   removed, when what is left names a recognisable public model family;
3. otherwise the model's own answer to "which model are you?", marked
   *self-reported* because models can be wrong about themselves;
4. otherwise the upstream vendor and the alias's tier, e.g. ``openai (lite)``.

Raw ids are kept only in the local, git-ignored result files, for reference.
"""
from __future__ import annotations

import re
from typing import Optional

#: Public model families. A name containing one of these is already generic.
FAMILIES = (
    "gpt", "o1", "o3", "o4", "claude", "gemini", "gemma", "llama", "qwen", "qwq", "mistral",
    "mixtral", "codestral", "devstral", "ministral", "deepseek", "phi", "granite", "glm",
    "kimi", "command", "yi", "falcon", "olmo", "starcoder", "codellama", "nemotron",
    "minimax", "hermes", "solar", "smollm", "dbrx", "jamba", "grok",
)
_PREFIX = re.compile(r"^(?:internal|external|ext|tuned|private|custom|local|prod|staging|dev)(?:[-_.]+|$)",
                     re.IGNORECASE)
_TIER_WORDS = ("lite", "standard", "premium", "flash", "medium", "pro", "mini", "max", "plus", "basic")
_FAMILY_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(f) for f in FAMILIES) + r")", re.IGNORECASE)


def base_segment(model_id: str) -> str:
    """Last path segment without ``:latest``."""
    name = model_id.strip().rstrip("/").rsplit("/", 1)[-1]
    return name[: -len(":latest")] if name.lower().endswith(":latest") else name


def strip_deployment_prefixes(name: str) -> str:
    previous = None
    while previous != name:
        previous, name = name, _PREFIX.sub("", name).lstrip("-_.")
    return name


def scrub(name: str) -> str:
    """Defence in depth for any name that reaches the report."""
    cleaned = strip_deployment_prefixes(base_segment(name))
    return cleaned or "model"


def names_public_family(name: str) -> bool:
    return bool(_FAMILY_RE.search(name))


def needs_identity(model_id: str) -> bool:
    """True when the id alone cannot give a publishable, meaningful name."""
    name = strip_deployment_prefixes(base_segment(model_id))
    if not name:
        return True
    tokens = [t for t in re.split(r"[-_.:\s]+", name.lower()) if t]
    vendor_alias = bool(tokens) and all(t in _TIER_WORDS or t in ("openai", "anthropic", "google")
                                        or t.isdigit() for t in tokens)
    return vendor_alias or not names_public_family(name)


def sanitize_claim(text: str) -> str:
    """The model's self-identification, cut down to something that looks like a name."""
    line = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    line = re.sub(r"(?i)^(i am|i'm|this is|model|name)\s*[:\-]?\s*", "", line.strip())
    line = re.sub(r"[`*\"'“”]", "", line).strip(" .,:;!")
    line = re.sub(r"(?i)^(an? )?(ai|language) model (called|named)\s+", "", line)
    words = line.split()
    if not words or len(line) > 40 or len(words) > 5:
        return ""
    return line


def vendor_label(vendor: Optional[str], model_id: str) -> str:
    name = strip_deployment_prefixes(base_segment(model_id)).lower()
    tier = next((word for word in _TIER_WORDS if word in re.split(r"[-_.]+", name)), "")
    vendor = (vendor or "").strip().lower() or "unknown vendor"
    return f"{vendor} ({tier})" if tier else vendor


def display_name(model_id: str, *, override: Optional[str] = None, claimed: Optional[str] = None,
                 vendor: Optional[str] = None) -> tuple[str, str]:
    """``(name, source)``; source is override, derived, self-reported or vendor."""
    if override and override.strip():
        return override.strip(), "override"
    if not needs_identity(model_id):
        return scrub(model_id), "derived"
    claim = sanitize_claim(claimed or "")
    if claim:
        return f"{claim} (self-reported)", "self-reported"
    return vendor_label(vendor, model_id), "vendor"


_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_HOSTLIKE = re.compile(r"\b[\w-]+(?:\.[\w-]+)+(?:/[\w.:@-]+)+")  # registry.example.ai/library/model:tag
_NAMESPACED = re.compile(r"\b[\w.-]+/[\w.:-]+")                   # provider/model ids


def scrub_detail(detail: str) -> str:
    """A failure reason that names no host, URL, product path or raw model id.

    Serving and capability errors carry the upstream's own message, which can
    name all of those; the report shows a generic reason instead. Grader
    reasons ("answered 7, expected 5") pass through, minus anything id-shaped.
    """
    text = detail or ""
    lowered = text.lower()
    if "does not support tools" in lowered or "tools_unsupported" in lowered:
        return "refused: the model does not support tool calls"
    kind = re.match(r"(http_(\d+)|timeout|connection|bad_response|skipped)\b", text)
    if kind:
        if kind.group(2):
            return f"serving error (HTTP {kind.group(2)})"
        return "skipped: model unavailable" if kind.group(1) == "skipped" else f"serving error ({kind.group(1)})"
    text = _URL.sub("<url>", text)
    text = _HOSTLIKE.sub("<path>", text)
    return _NAMESPACED.sub("<model>", text)


IDENTITY_PROMPT = ("Which large language model are you, exactly? Reply with only the model "
                   "family and version, nothing else.")
