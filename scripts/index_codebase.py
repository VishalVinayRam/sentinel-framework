#!/usr/bin/env python3
"""
Sentinel codebase indexer — build a local vector index for fast RCA code lookup.

Chunks your source files, embeds each chunk via Ollama, and stores everything
in a ChromaDB collection on disk. Run once; Sentinel queries it on every alert.

Usage:
  # From a GitHub repo (no cloning — uses GitHub API):
  python scripts/index_codebase.py --repo owner/repo --token ghp_...

  # From a local directory:
  python scripts/index_codebase.py --local /path/to/your/service

  # Point at this demo project:
  python scripts/index_codebase.py --local . --include "target/services"

Options:
  --repo OWNER/REPO     GitHub repo to index (no clone needed)
  --local PATH          Local directory to index
  --include SUBPATH     Only index files under this subdirectory (repeatable)
  --token TOKEN         GitHub personal access token (for private repos)
  --branch BRANCH       Branch to index (default: main)
  --model MODEL         Ollama model to use for embeddings (default: llama3.2:1b)
  --ollama URL          Ollama base URL (default: http://localhost:11434)
  --index-dir PATH      Where to write the ChromaDB (default: .sentinel-index)
  --chunk-lines N       Lines per chunk (default: 40)
  --overlap N           Overlap between chunks in lines (default: 8)
  --clear               Delete existing index before rebuilding
"""

import argparse
import base64
import os
import sys
import time
from pathlib import Path
from typing import Generator

import requests

# ── Constants ─────────────────────────────────────────────────────────────────

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".go", ".java", ".rb", ".rs", ".cs",
    ".cpp", ".c", ".h", ".scala", ".kt",
    ".php", ".swift", ".sh",
}

SKIP_PATHS = (
    "node_modules/", "vendor/", ".git/", "__pycache__/", ".venv/",
    "dist/", "build/", "coverage/", ".mypy_cache/", ".ruff_cache/",
    "migrations/", "generated/", "*.min.js", "*.lock", "*.sum",
    "test/", "tests/", "_test.", ".test.", "spec/",
)

GITHUB_API = "https://api.github.com"

R    = "\033[0m"
G    = "\033[92m"
Y    = "\033[93m"
B    = "\033[96m"
BOLD = "\033[1m"


# ── File walking ───────────────────────────────────────────────────────────────

def _should_skip(path: str) -> bool:
    pl = path.lower()
    return any(s.rstrip("*") in pl for s in SKIP_PATHS)


def _is_source(path: str) -> bool:
    return Path(path).suffix in SOURCE_EXTENSIONS and not _should_skip(path)


def walk_local(root: str, include: list[str]) -> Generator[tuple[str, str], None, None]:
    """Yield (relative_path, content) for all source files under root."""
    base = Path(root).resolve()
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(base))
        if include and not any(rel.startswith(inc) for inc in include):
            continue
        if not _is_source(rel):
            continue
        try:
            yield rel, p.read_text(errors="replace")
        except Exception:
            pass


def walk_github(repo: str, branch: str, token: str,
                include: list[str]) -> Generator[tuple[str, str], None, None]:
    """Yield (path, content) for source files in a GitHub repo without cloning."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Resolve branch → commit SHA
    try:
        sha = requests.get(f"{GITHUB_API}/repos/{repo}/branches/{branch}",
                           headers=headers, timeout=10).json()["commit"]["sha"]
    except Exception as e:
        print(f"  {Y}Cannot reach GitHub: {e}{R}")
        return

    # Fetch full recursive tree in one API call
    try:
        tree = requests.get(f"{GITHUB_API}/repos/{repo}/git/trees/{sha}?recursive=1",
                            headers=headers, timeout=20).json().get("tree", [])
    except Exception as e:
        print(f"  {Y}Tree fetch failed: {e}{R}")
        return

    blobs = [b for b in tree
             if b.get("type") == "blob" and _is_source(b["path"])
             and (not include or any(b["path"].startswith(inc) for inc in include))]

    print(f"  {B}→{R}  {len(blobs)} source files found in {repo}@{branch}")

    for blob in blobs:
        path = blob["path"]
        try:
            resp = requests.get(
                f"{GITHUB_API}/repos/{repo}/contents/{path}?ref={branch}",
                headers=headers, timeout=10,
            ).json()
            content = base64.b64decode(resp["content"]).decode(errors="replace")
            yield path, content
        except Exception as e:
            print(f"  {Y}Skip {path}: {e}{R}")
            continue


# ── Chunker ────────────────────────────────────────────────────────────────────

def chunk_file(file_path: str, content: str,
               chunk_lines: int = 40, overlap: int = 8) -> list[dict]:
    """Split a source file into overlapping line-based chunks."""
    lines = content.splitlines()
    chunks = []
    i = 0
    chunk_id = 0
    while i < len(lines):
        end = min(i + chunk_lines, len(lines))
        text = "\n".join(lines[i:end])
        if text.strip():
            chunks.append({
                "id":         f"{file_path}:L{i+1}",
                "text":       f"# {file_path} (lines {i+1}–{end})\n{text}",
                "file_path":  file_path,
                "start_line": i + 1,
                "end_line":   end,
                "chunk_id":   chunk_id,
            })
        i += chunk_lines - overlap
        chunk_id += 1
    return chunks


# ── Embedder ───────────────────────────────────────────────────────────────────

def embed(text: str, model: str, ollama_url: str) -> list[float]:
    resp = requests.post(
        f"{ollama_url}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=90,  # first call loads the model — allow extra time
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build Sentinel code embedding index")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--repo",  metavar="OWNER/REPO", help="GitHub repo to index")
    src.add_argument("--local", metavar="PATH",       help="Local directory to index")
    parser.add_argument("--include",   action="append", default=[], metavar="SUBPATH",
                        help="Only index files under this path prefix (repeatable)")
    parser.add_argument("--token",     default=os.environ.get("GITHUB_TOKEN", ""),
                        help="GitHub token (env: GITHUB_TOKEN)")
    parser.add_argument("--branch",    default="main")
    parser.add_argument("--model",     default=os.environ.get("EMBED_MODEL",
                                                os.environ.get("KSERVE_MODEL", "llama3.2:1b")))
    parser.add_argument("--ollama",    default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--index-dir", default=os.environ.get("SENTINEL_INDEX_DIR", ".sentinel-index"))
    parser.add_argument("--chunk-lines", type=int, default=40)
    parser.add_argument("--overlap",     type=int, default=8)
    parser.add_argument("--clear",       action="store_true",
                        help="Delete existing index before rebuilding")
    args = parser.parse_args()

    print(f"\n{BOLD}{B}  Sentinel Codebase Indexer{R}\n")

    # -- verify Ollama --
    try:
        models = [m["name"] for m in
                  requests.get(f"{args.ollama}/api/tags", timeout=5).json().get("models", [])]
        # match ignoring :latest tag
        def _base(m): return m.split(":")[0]
        if _base(args.model) not in [_base(m) for m in models]:
            print(f"  {Y}Warning: model '{args.model}' not in Ollama ({models}).{R}")
            print(f"  {Y}Pull it with: ollama pull {args.model}{R}\n")
        else:
            print(f"  {G}✓{R}  Ollama ready  model={args.model}  dims=", end="", flush=True)
            dims = len(embed("test", args.model, args.ollama))
            print(f"{dims}")
    except Exception as e:
        print(f"  {Y}✗  Ollama not reachable at {args.ollama}: {e}{R}")
        sys.exit(1)

    # -- open or create chromadb --
    try:
        import chromadb
    except ImportError:
        print("  chromadb not installed. Run: pip install chromadb")
        sys.exit(1)

    index_path = Path(args.index_dir).resolve()
    if args.clear and index_path.exists():
        import shutil
        shutil.rmtree(index_path)
        print(f"  {Y}Cleared existing index at {index_path}{R}")

    client = chromadb.PersistentClient(path=str(index_path))
    collection = client.get_or_create_collection(
        name="sentinel-code",
        metadata={"hnsw:space": "cosine"},
    )
    existing = collection.count()
    print(f"  {G}✓{R}  Index at {index_path}  (existing chunks: {existing})")

    # -- walk source files --
    print()
    if args.repo:
        label = f"github:{args.repo}@{args.branch}"
        file_iter = walk_github(args.repo, args.branch, args.token, args.include)
    else:
        label = f"local:{args.local}"
        file_iter = walk_local(args.local, args.include)
        total_local = sum(1 for _ in walk_local(args.local, args.include))
        print(f"  {B}→{R}  {total_local} source files found in {args.local}")
        file_iter = walk_local(args.local, args.include)

    # -- chunk → embed → store --
    files_done = chunks_done = skipped = 0
    t0 = time.time()

    for file_path, content in file_iter:
        chunks = chunk_file(file_path, content, args.chunk_lines, args.overlap)
        if not chunks:
            continue

        file_vecs = []
        file_ids  = []
        file_docs = []
        file_meta = []

        for chunk in chunks:
            # Skip chunks that are already indexed (same ID)
            try:
                existing_ids = collection.get(ids=[chunk["id"]])["ids"]
                if existing_ids:
                    skipped += 1
                    continue
            except Exception:
                pass

            try:
                vec = embed(chunk["text"], args.model, args.ollama)
                file_vecs.append(vec)
                file_ids.append(chunk["id"])
                file_docs.append(chunk["text"])
                file_meta.append({
                    "file_path":  chunk["file_path"],
                    "start_line": chunk["start_line"],
                    "end_line":   chunk["end_line"],
                    "repo":       args.repo or args.local,
                    "label":      label,
                })
            except Exception as e:
                print(f"\n  {Y}embed error {chunk['id']}: {e}{R}")

        if file_vecs:
            collection.add(ids=file_ids, embeddings=file_vecs,
                           documents=file_docs, metadatas=file_meta)
            chunks_done += len(file_vecs)

        files_done += 1
        elapsed = time.time() - t0
        rate = chunks_done / elapsed if elapsed else 0
        print(f"\r  {B}→{R}  {files_done} files  {chunks_done} chunks embedded"
              f"  ({rate:.1f} chunks/s) …", end="", flush=True)

    elapsed = time.time() - t0
    print(f"\n\n  {G}✓{R}  Done in {elapsed:.1f}s")
    print(f"  {G}✓{R}  {chunks_done} new chunks added  ({skipped} already indexed)")
    print(f"  {G}✓{R}  Total index size: {collection.count()} chunks")
    print(f"\n  Index location: {index_path}")
    print(f"  To rebuild from scratch: add --clear")
    print(f"  Sentinel will use this index automatically on next alert.\n")


if __name__ == "__main__":
    main()
