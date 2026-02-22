import os
import logging
import subprocess
import concurrent.futures
import mmap
from pathlib import Path

from .constants import DEFAULT_IGNORE_PATTERNS
from .file_utils import (
    build_tree,
    compile_ignore_matcher,
    detect_language,
    is_probably_text,
    line_count_from_bytes,
    normalize_posix_path,
    should_ignore_dir,
)
from .models import FileInfo, RepoInfo

log = logging.getLogger(__name__)


class RepoScanner:
    def __init__(self, root: str, max_file_size: int = 512 * 1024,
                 extra_ignore: list[str] | None = None,
                 prefer_git_source: bool = True,
                 scan_workers: int | None = None):
        self.root = Path(root).resolve()
        self.max_file_size = max_file_size
        self.extra_ignore = extra_ignore or []
        self.prefer_git_source = prefer_git_source
        self.scan_workers = scan_workers or 8
        self._ignore_patterns = [*DEFAULT_IGNORE_PATTERNS, *self.extra_ignore]
        self._ignore_match = compile_ignore_matcher(self._ignore_patterns)

    def _should_ignore_file(self, rel_posix: str, filename: str) -> bool:
        _ = filename
        return self._ignore_match(rel_posix)

    def _detect_language(self, filepath: Path) -> str:
        return detect_language(filepath)

    def _read_bytes(self, filepath: Path) -> bytes | None:
        try:
            return filepath.read_bytes()
        except (IOError, OSError) as e:
            log.debug("failed to read file: %s (%s)", filepath, e)
            return None

    def _read_sample(self, filepath: Path, sample_size: int = 8192) -> bytes | None:
        try:
            with filepath.open("rb") as f:
                return f.read(sample_size)
        except (IOError, OSError) as e:
            log.debug("failed to read file sample: %s (%s)", filepath, e)
            return None

    def _count_lines_stream(self, filepath: Path, chunk_size: int = 64 * 1024) -> int | None:
        try:
            total = 0
            ends_with_newline = False
            with filepath.open("rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    total += chunk.count(b"\n")
                    ends_with_newline = chunk.endswith(b"\n")
            if total == 0:
                # Non-empty file with no newline terminators.
                return 1
            if not ends_with_newline:
                total += 1
            return total
        except (IOError, OSError) as e:
            log.debug("failed to stream-count lines: %s (%s)", filepath, e)
            return None

    def _count_lines_mmap(self, filepath: Path) -> int | None:
        try:
            with filepath.open("rb") as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    if len(mm) == 0:
                        return 0
                    total = mm[:].count(b"\n")
                    if not mm[-1:] == b"\n":
                        total += 1
                    return total
        except (IOError, OSError, ValueError) as e:
            log.debug("failed to mmap-count lines: %s (%s)", filepath, e)
            return None

    def _git_ls_files(self) -> list[Path] | None:
        if not self.prefer_git_source:
            return None
        try:
            top = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if top.returncode != 0:
                return None
            top_root = Path(top.stdout.strip()).resolve()
            if top_root != self.root:
                return None

            proc = subprocess.run(
                ["git", "ls-files"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        rels = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return [self.root / Path(rel) for rel in rels]

    def _iter_files(self):
        git_files = self._git_ls_files()
        if git_files is not None:
            for filepath in sorted(git_files):
                if filepath.is_file():
                    yield filepath
            return
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not should_ignore_dir(d))
            for filename in sorted(filenames):
                yield Path(dirpath) / filename

    def _scan_one_file(self, filepath: Path, include_content: bool) -> tuple[str, FileInfo | None]:
        try:
            rel_path = filepath.relative_to(self.root)
        except ValueError:
            return ("skipped_unreadable", None)

        rel_posix = normalize_posix_path(rel_path)
        if self._should_ignore_file(rel_posix, filepath.name):
            return ("ignored_by_pattern", None)

        try:
            st = filepath.stat()
            size = st.st_size
            mtime_ns = int(st.st_mtime_ns)
        except OSError:
            return ("skipped_unreadable", None)

        if size > self.max_file_size or size == 0:
            return ("skipped_size_or_empty", None)

        if include_content:
            blob = self._read_bytes(filepath)
            if blob is None:
                return ("skipped_unreadable", None)
            if not is_probably_text(blob[:8192]):
                return ("skipped_binary", None)
            line_count = line_count_from_bytes(blob)
            content = blob.decode(encoding="utf-8", errors="replace")
        else:
            sample = self._read_sample(filepath)
            if sample is None:
                return ("skipped_unreadable", None)
            if not is_probably_text(sample):
                return ("skipped_binary", None)

            content = ""
            line_count = self._count_lines_mmap(filepath)
            if line_count is None:
                line_count = self._count_lines_stream(filepath)
            if line_count is None:
                return ("skipped_unreadable", None)

        info = FileInfo(
            path=rel_path,
            abs_path=filepath,
            language=self._detect_language(filepath),
            size=size,
            mtime_ns=mtime_ns,
            line_count=line_count,
            content=content,
        )
        return ("ok", info)

    def scan(self, include_content: bool = True) -> RepoInfo:
        """Scan repository files and return a populated RepoInfo."""
        repo = RepoInfo(root=self.root, name=self.root.name)
        files = []
        scan_stats: dict[str, int] = {
            "seen_files": 0,
            "ignored_by_pattern": 0,
            "skipped_unreadable": 0,
            "skipped_size_or_empty": 0,
            "skipped_binary": 0,
        }

        candidates = list(self._iter_files())
        scan_stats["seen_files"] = len(candidates)

        max_workers = max(1, min(self.scan_workers, len(candidates) or 1))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            for status, info in pool.map(lambda p: self._scan_one_file(p, include_content), candidates):
                if status == "ok" and info is not None:
                    files.append(info)
                elif status in scan_stats:
                    scan_stats[status] += 1

        files.sort(key=lambda item: str(item.path))
        for index, info in enumerate(files, 1):
            info.index = index

        repo.files = files
        repo.total_lines = sum(item.line_count for item in files)
        repo.total_size = sum(item.size for item in files)

        lang_stats = {}
        for info in files:
            lang_stats.setdefault(info.language, {"files": 0, "lines": 0})
            lang_stats[info.language]["files"] += 1
            lang_stats[info.language]["lines"] += info.line_count
        repo.language_stats = dict(sorted(
            lang_stats.items(), key=lambda item: item[1]["lines"], reverse=True))
        repo.tree_str = self._build_tree(files)
        repo.scan_stats = scan_stats
        return repo

    def _build_tree(self, files: list[FileInfo]) -> str:
        rels = [normalize_posix_path(info.path) for info in files]
        return build_tree(rels, self.root.name, style="unicode")
