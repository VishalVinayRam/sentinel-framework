"""Generic source code scanner for RCA context.

Unlike code_context_builder.py (hardcoded for the Sentinel demo repo), this
scanner works against any arbitrary repo with no service-specific keyword lists.
It finds files whose path or content is relevant to the given service name,
then extracts the most bug-likely code snippet from each file.
"""

from pathlib import Path
from typing import Optional

_SOURCE_EXTENSIONS = {
    ".py", ".go", ".ts", ".js", ".java", ".rb", ".rs",
    ".cpp", ".c", ".cs", ".php", ".scala", ".kt",
}

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".tox",
    "vendor", "dist", "build", ".venv", "venv",
    ".gradle", ".idea", ".vscode",
})

# Generic error-pattern keywords — signal files worth including
_BASE_KEYWORDS = [
    "error", "exception", "timeout", "panic", "fatal", "crash",
    "connection", "pool", "retry", "failed", "refused", "oom",
    "deadlock", "overflow", "leak", "invalid", "unauthorized",
]

_BUG_MARKERS = ("←", "# BUG", "# FIXME", "# TODO", "# HACK")


def scan(
    service: str,
    repo_root: Optional[str] = None,
    extra_keywords: Optional[list] = None,
    exclude_dirs: Optional[list] = None,
    max_files: int = 3,
    snippet_lines: int = 30,
) -> Optional[dict]:
    """Scan repo_root for source files relevant to service.

    Returns a dict {"files": [...]} compatible with the RCA engine, or None.
    Each file entry has: path, language, lines (list[str]), start_line, score.
    """
    root = Path(repo_root or ".").resolve()
    keywords = _BASE_KEYWORDS + (extra_keywords or [])

    # Directories to exclude beyond the global _SKIP_DIRS
    # Default: skip the sentinel-framework package tree so it never contaminates
    # results when scanning the Sentinel project's own repo.
    extra_skip = set(exclude_dirs or ["sentinel"])

    candidates = []
    for path in _walk(root, extra_skip=extra_skip):
        score = _score(path, root, service, keywords)
        if score > 0:
            candidates.append((score, path))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)

    files = []
    for _, path in candidates[:max_files]:
        snippet = _extract_snippet(path, keywords, snippet_lines)
        if snippet is None:
            continue
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        files.append({
            "path":       rel,
            "language":   path.suffix.lstrip("."),
            "lines":      snippet["lines"],
            "start_line": snippet["start_line"],
        })

    return {"files": files} if files else None


# ── helpers ────────────────────────────────────────────────────────────────

def _walk(root: Path, extra_skip: set = frozenset()):
    skip = _SKIP_DIRS | extra_skip
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in _SOURCE_EXTENSIONS:
            continue
        if any(part in skip for part in path.parts):
            continue
        yield path


def _service_slugs(service: str) -> set:
    s = service.lower()
    return {
        s,
        s.replace("-", "_"),
        s.replace("-service", ""),
        s.replace("_service", ""),
        s.split("-")[0],
        s.split("_")[0],
    }


def _score(path: Path, root: Path, service: str, keywords: list) -> float:
    slugs    = _service_slugs(service)
    path_str = str(path).lower()

    # Path-based score — being in a directory named after the service is a strong signal
    path_score = sum(4.0 for slug in slugs if f"/{slug}/" in path_str or path_str.endswith(f"/{slug}"))

    try:
        text = path.read_text(errors="replace")
    except OSError:
        return 0.0

    kw_score = sum(1.0 for kw in keywords if kw.lower() in text.lower())
    if kw_score == 0:
        return 0.0

    # Require a path-level match OR a high keyword density to avoid pulling in
    # infrastructure/utility files that happen to mention common error words.
    if path_score == 0 and kw_score < 12:
        return 0.0

    return path_score + kw_score


def _extract_snippet(path: Path, keywords: list, context: int = 30) -> Optional[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None

    best_idx   = 0
    best_score = 0.0

    for i, line in enumerate(lines):
        kw_hits = sum(1 for kw in keywords if kw.lower() in line.lower())
        if kw_hits == 0:
            continue

        score = float(kw_hits)
        if line.lstrip().startswith(("import ", "from ")):
            score -= 0.5
        if any(marker in line for marker in _BUG_MARKERS):
            score += 1.5
        if score > best_score:
            best_score = score
            best_idx   = i

    start = max(0, best_idx - 5)
    end   = min(len(lines), best_idx + context)
    return {
        "lines":      lines[start:end],
        "start_line": start + 1,
    }
