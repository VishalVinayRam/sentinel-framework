"""
CodeContextBuilder — fetches real source code evidence for an incident.

Priority order:
  1. Local repo scan  — walks target/services/{service}/ and services/{service}/
     using keyword matching, extracts the surrounding function/block
  2. GitHub API       — used when GITHUB_TOKEN + GITHUB_REPO are set and local
     scan finds nothing useful
  3. Static fallback  — the hardcoded _CODE_CONTEXT_MAP in api.py (last resort)
"""

import ast
import os
import re
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")  # e.g. "org/repo"

# Keywords that signal the root cause for each (service, severity) pair.
# More specific = better hit quality.
_KEYWORDS: dict[tuple, list[str]] = {
    ("auth-service",    "P1"): ["pool_size", "max_overflow", "pool_pre_ping", "create_engine"],
    ("auth-service",    "P2"): ["utcnow", "expires_at", "token_cache", "TTLCache"],
    ("payments-service","P1"): ["requests.post", "timeout", "raise_for_status", "charge"],
    ("payments-service","P2"): ["_pending", "deque", "enqueue", "flush"],
    ("search-api",      "P1"): ["bulk_index", "es.bulk", "body=body"],
    ("search-api",      "P2"): ["bulk_index", "es.bulk", "body=body"],
    ("search-api",      "P3"): ["bulk_index", "es.bulk", "body=body"],
    ("worker-service",  "P1"): ["process_message", "except:", "delete_message"],
    ("worker-service",  "P2"): ["process_message", "except:", "delete_message"],
    ("data-pipeline",   "P1"): ["get_shard_iterator", "NextShardIterator", "sleep"],
    ("data-pipeline",   "P2"): ["get_shard_iterator", "NextShardIterator", "sleep"],
    ("notification-service", "P3"): ["SENDGRID_API_KEY", "os.environ", "sendgrid"],
    ("user-service",    "P3"): ["list_profiles", "db.query", "filter(User.id"],
    ("inventory-service","P3"): ["get_stock", "r.get", "setex"],
    ("api-gateway",     "P1"): ["UPSTREAM_TIMEOUT", "httpx", "TimeoutException"],
    ("ml-inference",    "P2"): ["torch.no_grad", "empty_cache", "_model.generate"],
}

_GENERIC_KEYWORDS: dict[str, list[str]] = {
    "auth-service":          ["pool_size", "max_overflow", "utcnow", "token_cache"],
    "payments-service":      ["requests.post", "timeout", "charge", "refund"],
    "search-api":            ["bulk_index", "es.bulk", "Elasticsearch"],
    "worker-service":        ["process_message", "SQS", "delete_message"],
    "data-pipeline":         ["get_shard_iterator", "kinesis", "Kinesis"],
    "notification-service":  ["SENDGRID_API_KEY", "sendgrid", "smtp"],
    "user-service":          ["list_profiles", "db.query", "User.id"],
    "inventory-service":     ["get_stock", "redis", "setex"],
    "api-gateway":           ["UPSTREAM_TIMEOUT", "httpx", "proxy"],
    "ml-inference":          ["torch", "_model", "cuda"],
}

_SEARCH_DIRS = [
    "target/services/{service}",
    "services/{service}",
    "target/services",
    "services",
]

_SOURCE_EXTENSIONS = {".py", ".yaml", ".yml", ".tf", ".json"}

# Files that should never be returned as code context evidence
_EXCLUDE_PATHS = {
    "services/dashboard/api.py",
    "services/dashboard/code_context_builder.py",
}


def _candidate_dirs(service: str) -> list[Path]:
    dirs = []
    for tmpl in _SEARCH_DIRS:
        p = REPO_ROOT / tmpl.format(service=service)
        if p.exists():
            dirs.append(p)
    return dirs


def _walk_source_files(service: str) -> list[Path]:
    files = []
    seen = set()
    for d in _candidate_dirs(service):
        for f in d.rglob("*"):
            if f.is_file() and f.suffix in _SOURCE_EXTENSIONS and f not in seen:
                try:
                    rel = str(f.relative_to(REPO_ROOT))
                except ValueError:
                    rel = str(f)
                if rel not in _EXCLUDE_PATHS:
                    seen.add(f)
                    files.append(f)
    return files


def _service_dir_variants(service: str) -> list[str]:
    """Return all plausible directory names for a service slug."""
    variants = {service}                                  # "payments-service"
    variants.add(service.replace("-service", ""))         # "payments"
    variants.add(service.replace("-", "_"))               # "payments_service"
    variants.add(service.split("-")[0])                   # "payments"
    return list(variants)


def _score_file(path: Path, service: str, keywords: list[str]) -> int:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return 0
    kw_score = sum(1 for kw in keywords if kw in text)
    if kw_score == 0:
        return 0
    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
    # Heavy bonus for being in the right service directory (try all name variants)
    svc_dirs = _service_dir_variants(service)
    in_target = any(f"target/services/{v}/" in rel + "/" for v in svc_dirs)
    in_service = any(f"services/{v}/" in rel + "/" for v in svc_dirs)
    if in_target:
        kw_score += 100
    elif in_service:
        kw_score += 10
    # Penalty for clearly unrelated dirs
    if any(d in rel for d in ("dashboard", "scripts", "tests", "__pycache__")):
        kw_score = max(0, kw_score - 50)
    return kw_score


def _is_docstring_line(lines: list[str], idx: int) -> bool:
    """Return True if idx falls inside a module-level triple-quoted string."""
    in_triple = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_triple:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    # single-line docstring
                    if i == idx:
                        return True
                else:
                    in_triple = True
                    if i == idx:
                        return True
        else:
            if i == idx:
                return True
            if '"""' in stripped or "'''" in stripped:
                in_triple = False
        if i > idx:
            break
    return False


def _best_code_line(all_lines: list[str], keywords: list[str]) -> int:
    """Return 0-indexed line with the most keyword hits.

    Scoring rules (higher = better anchor):
    - Pure comment lines: 40% of base score (they describe bugs, not have them)
    - Code lines with inline bug markers (←, BUG, FIXME): +1.5 bonus
    - Import/from lines: -0.5 penalty (too generic, push below actual code)
    - Docstring-interior lines: skipped entirely
    """
    _BUG_MARKERS = ("←", "# BUG", "# FIXME", "# ←", "# noqa", "# TODO")
    best_line, best_score = 0, 0.0
    for i, line in enumerate(all_lines):
        if _is_docstring_line(all_lines, i):
            continue
        stripped = line.lstrip()
        is_comment = stripped.startswith(("#", "//", "*", "/*"))
        is_import = stripped.startswith(("import ", "from "))
        kw_hits = sum(1 for kw in keywords if kw in line)
        if kw_hits == 0:
            continue
        score = float(kw_hits)
        if is_comment:
            score *= 0.4
        elif is_import:
            score -= 0.5
        elif any(m in line for m in _BUG_MARKERS):
            score += 1.5  # explicit inline bug annotation is the ideal anchor
        if score > best_score:
            best_score = score
            best_line = i
    return best_line


def _extract_function_range(lines: list[str], hit_line: int) -> tuple[int, int]:
    """Return (start, end) 0-indexed line indices for the function/block enclosing hit_line."""
    start = hit_line
    found_anchor = False
    for i in range(hit_line, -1, -1):
        stripped = lines[i].lstrip()
        if stripped.startswith(("def ", "class ", "resource ", "async def ")):
            start = i
            found_anchor = True
            break
        # Top-level statement (no indent): valid anchor only when it's real code
        if i < hit_line and not lines[i].startswith(" ") and not lines[i].startswith("\t"):
            if (stripped and
                not stripped.startswith(("#", '"""', "'''")) and
                not _is_docstring_line(lines, i)):
                start = i
                found_anchor = True
                break
        if hit_line - i > 40:
            break

    if not found_anchor:
        start = max(0, hit_line - 8)

    # Walk down to find end of block
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    end = min(len(lines) - 1, start + 50)
    for i in range(start + 1, min(len(lines), start + 60)):
        stripped = lines[i].lstrip()
        if not stripped:
            continue
        indent = len(lines[i]) - len(stripped)
        if indent <= base_indent and not stripped.startswith(("#", '"""', "'''")):
            if i > start + 3:
                end = i - 1
                break
    return start, min(end, len(lines) - 1)


def _highlight_offsets(lines: list[str], keywords: list[str]) -> list[int]:
    offsets = []
    for idx, line in enumerate(lines):
        for kw in keywords:
            if kw in line and "# ←" in line or (kw in line and any(
                bad in line for bad in ["BUG", "TODO", "FIXME", "←", "no burst", "missing", "too small",
                                        "swallows", "unbounded", "hangs", "never", "too short"]
            )):
                offsets.append(idx)
                break
    return offsets


def _build_file_entry(path: Path, keywords: list[str]) -> Optional[dict]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None

    all_lines = text.splitlines()
    if not all_lines:
        return None

    best_line = _best_code_line(all_lines, keywords)
    start, end = _extract_function_range(all_lines, best_line)
    snippet = all_lines[start:end + 1]

    # Trim to ≤35 lines for readability
    if len(snippet) > 35:
        # Keep the hit region centred
        centre = best_line - start
        lo = max(0, centre - 17)
        hi = min(len(snippet), lo + 35)
        snippet = snippet[lo:hi]
        start = start + lo

    rel_path = path.relative_to(REPO_ROOT)
    suffix = path.suffix.lstrip(".")
    lang_map = {"py": "python", "yaml": "yaml", "yml": "yaml", "tf": "hcl", "json": "json"}
    language = lang_map.get(suffix, suffix)

    hits = _highlight_offsets(snippet, keywords)

    return {
        "path": str(rel_path),
        "language": language,
        "start_line": start + 1,
        "lines": snippet,
        "highlight_offsets": hits,
        "annotation": _annotation(path, keywords, snippet),
        "fix": _fix_hint(path, keywords),
    }


def _annotation(path: Path, keywords: list[str], lines: list[str]) -> str:
    bad_lines = [ln.strip() for ln in lines if any(
        bad in ln for bad in ["BUG", "←", "no burst", "unbounded", "hangs", "swallows", "never"]
    )]
    if bad_lines:
        return " | ".join(bad_lines[:2])
    kws_found = [kw for kw in keywords if any(kw in ln for ln in lines)]
    return f"Review {path.name} — pay attention to: {', '.join(kws_found[:3])}" if kws_found else f"Review {path.name}"


def _fix_hint(path: Path, keywords: list[str]) -> str:
    fixes = {
        "pool_size":           "engine = create_engine(DB_URL, pool_size=20, max_overflow=10, pool_pre_ping=True, pool_recycle=300)",
        "utcnow":              "Use datetime.now(tz=timezone.utc) consistently; replace bare except with specific exception types",
        "requests.post":       "Add timeout=5, wrap with tenacity @retry(stop=stop_after_attempt(3), wait=wait_exponential())",
        "es.bulk":             "Chunk docs into batches of 500: for i in range(0, len(docs), 500): es.bulk(body=batch)",
        "get_shard_iterator":  "Replace sleep(300) with sleep(1); catch ExpiredIteratorException and re-fetch iterator",
        "process_message":     "Replace bare except with specific exceptions; call sqs.delete_message() on non-retryable errors",
        "SENDGRID_API_KEY":    "Read key inside the function (not at module level); use AWS Secrets Manager with TTL refresh",
        "UPSTREAM_TIMEOUT":    "UPSTREAM_TIMEOUT=10.0; add httpx retry transport; wrap in pybreaker CircuitBreaker",
        "empty_cache":         "del tokens, out after decode; call torch.cuda.empty_cache() every N=100 requests",
    }
    for kw, hint in fixes.items():
        if kw in keywords:
            return hint
    return f"Review flagged lines in {path.name} and apply defensive coding patterns."


def _github_fallback(service: str, keywords: list[str]) -> list[dict]:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return []
    try:
        from sentinel.providers.git.github import GitHubProvider
        gh = GitHubProvider(token=GITHUB_TOKEN)
        hits = gh.search_code(GITHUB_REPO, keywords[:4], extensions=[".py", ".yaml", ".tf"])
        entries = []
        for hit in hits[:3]:
            content = gh.fetch_file(GITHUB_REPO, hit["path"])
            if not content:
                continue
            all_lines = content.splitlines()
            best_line = 0
            best_score = 0
            for i, line in enumerate(all_lines):
                score = sum(1 for kw in keywords if kw in line)
                if score > best_score:
                    best_score = score
                    best_line = i
            start, end = _extract_function_range(all_lines, best_line)
            snippet = all_lines[start:end + 1]
            if len(snippet) > 35:
                centre = best_line - start
                lo = max(0, centre - 17)
                hi = min(len(snippet), lo + 35)
                snippet = snippet[lo:hi]
                start = start + lo
            suffix = Path(hit["path"]).suffix.lstrip(".")
            lang_map = {"py": "python", "yaml": "yaml", "yml": "yaml", "tf": "hcl"}
            entries.append({
                "path": hit["path"],
                "language": lang_map.get(suffix, suffix),
                "start_line": start + 1,
                "lines": snippet,
                "highlight_offsets": _highlight_offsets(snippet, keywords),
                "annotation": _annotation(Path(hit["path"]), keywords, snippet),
                "fix": _fix_hint(Path(hit["path"]), keywords),
            })
        return entries
    except Exception as e:
        import sys
        print(f"[code-ctx] GitHub fallback failed: {e}", file=sys.stderr)
        return []


def build(service: str, severity: str) -> Optional[dict]:
    """
    Return a code_context dict with real file snippets, or None if nothing found
    (caller should fall back to the static map).
    """
    keywords = (
        _KEYWORDS.get((service, severity))
        or _KEYWORDS.get((service, "P1"))
        or _GENERIC_KEYWORDS.get(service)
        or []
    )
    if not keywords:
        return None

    source_files = _walk_source_files(service)

    # Score and rank files by keyword density + service-path proximity
    scored = [(f, _score_file(f, service, keywords)) for f in source_files]
    scored = [(f, s) for f, s in scored if s > 0]
    scored.sort(key=lambda x: -x[1])

    top_score = scored[0][1] if scored else 0
    # If the best file has no service-path bonus (score ≤ 50), the repo scan
    # found nothing relevant — fall through to static map or GitHub fallback.
    if top_score <= 50:
        entries = _github_fallback(service, keywords)
        if not entries:
            return None
        return {
            "summary": f"Fetched {len(entries)} source file(s) from GitHub relevant to {service} {severity}.",
            "files": entries,
            "source": "github",
        }
    # Only accept files with at least 30% of the top score
    score_floor = int(top_score * 0.3)

    entries = []
    seen_paths = set()
    for path, score in scored[:8]:
        if score < score_floor:
            break
        if path in seen_paths:
            continue
        seen_paths.add(path)
        entry = _build_file_entry(path, keywords)
        if entry and entry["lines"]:
            entries.append(entry)
        if len(entries) >= 3:
            break

    # Also look for companion config/infra files (same service, different extension)
    if entries:
        svc_rel_prefix = f"target/services/{service}/"
        for path, score in scored:
            if path in seen_paths:
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if path.suffix in {".yaml", ".yml", ".tf"} and svc_rel_prefix in rel:
                seen_paths.add(path)
                entry = _build_file_entry(path, keywords)
                if entry and entry["lines"]:
                    entries.append(entry)
                    break

    if not entries:
        entries = _github_fallback(service, keywords)

    if not entries:
        return None

    # Build a human-readable summary from what we found
    bad_annotations = [e["annotation"] for e in entries if "←" in e.get("annotation", "") or "BUG" in e.get("annotation", "")]
    summary = bad_annotations[0] if bad_annotations else f"Fetched {len(entries)} source file(s) relevant to {service} {severity} incident."

    return {
        "summary": summary,
        "files": entries,
        "source": "live",
    }
