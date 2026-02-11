import fnmatch
import os
from pathlib import Path

from .constants import DEFAULT_IGNORE_DIRS, DEFAULT_IGNORE_PATTERNS
from .models import FileInfo, RepoInfo
from .syntax import LANG_MAP


class RepoScanner:
    def __init__(self, root: str, max_file_size: int = 512 * 1024,
                 extra_ignore: list[str] | None = None):
        self.root = Path(root).resolve()
        self.max_file_size = max_file_size
        self.extra_ignore = extra_ignore or []

    def _should_ignore_dir(self, dirname: str) -> bool:
        return dirname in DEFAULT_IGNORE_DIRS or dirname.startswith(".")

    def _should_ignore_file(self, filename: str) -> bool:
        for pattern in DEFAULT_IGNORE_PATTERNS + self.extra_ignore:
            if fnmatch.fnmatch(filename, pattern) or \
               fnmatch.fnmatch(filename.lower(), pattern.lower()):
                return True
        return False

    def _detect_language(self, filepath: Path) -> str:
        special = {
            "dockerfile": "docker", "makefile": "makefile",
            "cmakelists.txt": "cmake", "rakefile": "ruby",
            "gemfile": "ruby", "requirements.txt": "text",
            "pipfile": "toml", "cargo.toml": "toml",
            "go.mod": "go", "go.sum": "text",
        }
        name = filepath.name.lower()
        if name in special:
            return special[name]
        return LANG_MAP.get(filepath.suffix.lower(), "text")

    def _is_text_file(self, filepath: Path) -> bool:
        try:
            with open(filepath, "rb") as handle:
                chunk = handle.read(8192)
            return b"\x00" not in chunk
        except (IOError, OSError):
            return False

    def scan(self) -> RepoInfo:
        repo = RepoInfo(root=self.root, name=self.root.name)
        files = []

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not self._should_ignore_dir(d))
            for filename in sorted(filenames):
                if self._should_ignore_file(filename):
                    continue
                filepath = Path(dirpath) / filename
                rel_path = filepath.relative_to(self.root)
                try:
                    size = filepath.stat().st_size
                except OSError:
                    continue
                if size > self.max_file_size or size == 0:
                    continue
                if not self._is_text_file(filepath):
                    continue
                try:
                    content = filepath.read_text(encoding="utf-8", errors="replace")
                except (IOError, OSError):
                    continue
                line_count = content.count("\n") + (
                    1 if content and not content.endswith("\n") else 0)
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
        return repo

    def _build_tree(self, files: list[FileInfo]) -> str:
        tree = {}
        for info in files:
            parts = info.path.parts
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(f"{part}/", {})
            node[parts[-1]] = None
        lines = [f"{self.root.name}/"]
        self._tree_lines(tree, lines, "")
        return "\n".join(lines)

    def _tree_lines(self, node: dict, lines: list[str], prefix: str):
        items = list(node.items())
        for index, (name, subtree) in enumerate(items):
            is_last = (index == len(items) - 1)
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{name}")
            if subtree is not None:
                extension = "    " if is_last else "│   "
                self._tree_lines(subtree, lines, prefix + extension)
