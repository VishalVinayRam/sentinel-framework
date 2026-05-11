import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class Chunk:
    text: str
    source_file: str
    start_line: int
    end_line: int
    chunk_index: int
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".go", ".java", ".rb", ".rs", ".cpp", ".c", ".h",
    ".md", ".txt", ".yaml", ".yml", ".json", ".tf",
    ".sh", ".sql",
}


class CodebaseIndexer:
    """Walks a codebase and breaks files into overlapping chunks for embedding."""

    def __init__(self, root: str, chunk_size: int = 512, overlap: int = 64, exclude: list = None):
        self.root = Path(root)
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.exclude = exclude or ["node_modules/", ".git/", "__pycache__/", "*.lock", "*.bin", "*.safetensors"]

    def iter_chunks(self) -> Iterator[Chunk]:
        for file_path in self._iter_files():
            yield from self._chunk_file(file_path)

    def _iter_files(self) -> Iterator[Path]:
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in SUPPORTED_EXTENSIONS:
                continue
            rel = str(path.relative_to(self.root))
            if self._is_excluded(rel):
                continue
            yield path

    def _is_excluded(self, rel_path: str) -> bool:
        for pattern in self.exclude:
            if fnmatch.fnmatch(rel_path, pattern):
                return True
            if rel_path.startswith(pattern.rstrip("/")):
                return True
        return False

    def _chunk_file(self, path: Path) -> Iterator[Chunk]:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return

        rel = str(path.relative_to(self.root))
        words = []
        word_to_line = []

        for line_no, line in enumerate(lines, start=1):
            for word in line.split():
                words.append(word)
                word_to_line.append(line_no)

        if not words:
            return

        step = max(1, self.chunk_size - self.overlap)
        chunk_index = 0

        for start in range(0, len(words), step):
            end = min(start + self.chunk_size, len(words))
            chunk_words = words[start:end]
            chunk_text = " ".join(chunk_words)
            start_line = word_to_line[start]
            end_line = word_to_line[end - 1]

            yield Chunk(
                text=chunk_text,
                source_file=rel,
                start_line=start_line,
                end_line=end_line,
                chunk_index=chunk_index,
                metadata={"extension": path.suffix, "repo_root": str(self.root)},
            )
            chunk_index += 1

            if end == len(words):
                break
