import os
import logging
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
                 extra_ignore: list[str] | None = None):
        self.root = Path(root).resolve()
        self.max_file_size = max_file_size
        self.extra_ignore = extra_ignore or []
        self._ignore_patterns = [*DEFAULT_IGNORE_PATTERNS, *self.extra_ignore]
        self._ignore_match = compile_ignore_matcher(self._ignore_patterns)

    def _should_ignore_file(self, rel_posix: str, filename: str) -> bool:
        return self._ignore_match(rel_posix) or self._ignore_match(filename)

    def _detect_language(self, filepath: Path) -> str:
        return detect_language(filepath)

    def _read_bytes(self, filepath: Path) -> bytes | None:
        try:
            return filepath.read_bytes()
        except (IOError, OSError) as e:
            log.debug("failed to read file: %s (%s)", filepath, e)
            return None

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

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not should_ignore_dir(d))
            for filename in sorted(filenames):
                scan_stats["seen_files"] += 1
                filepath = Path(dirpath) / filename
                rel_path = filepath.relative_to(self.root)
                rel_posix = normalize_posix_path(rel_path)
                if self._should_ignore_file(rel_posix, filename):
                    scan_stats["ignored_by_pattern"] += 1
                    continue
                try:
                    size = filepath.stat().st_size
                except OSError:
                    scan_stats["skipped_unreadable"] += 1
                    continue
                if size > self.max_file_size or size == 0:
                    scan_stats["skipped_size_or_empty"] += 1
                    continue
                blob = self._read_bytes(filepath)
                if blob is None:
                    scan_stats["skipped_unreadable"] += 1
                    continue
                if not is_probably_text(blob):
                    scan_stats["skipped_binary"] += 1
                    continue
                line_count = line_count_from_bytes(blob)
                content = ""
                if include_content:
                    content = blob.decode(encoding="utf-8", errors="replace")
                files.append(FileInfo(
                    path=rel_path, abs_path=filepath,
                    language=self._detect_language(filepath),
                    size=size, line_count=line_count, content=content,
                ))

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
