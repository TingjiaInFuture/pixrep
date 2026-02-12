from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .constants import DEFAULT_IGNORE_PATTERNS
from .file_utils import (
    build_tree,
    detect_language,
    is_probably_text,
    line_count_from_bytes,
    matches_any,
    normalize_posix_path,
    safe_join_repo,
    should_ignore_dir,
)
from .onepdf_writer import MinimalPDFWriter, pdf_escape_literal


DEFAULT_CORE_IGNORE_PATTERNS = [
    # Docs / meta
    "*.md",
    "LICENSE*",
    "NOTICE*",
    "CHANGELOG*",
    "CODE_OF_CONDUCT*",
    "CONTRIBUTING*",
    ".github/*",
    "docs/*",
    "doc/*",
    # Tests / fixtures
    "test/*",
    "tests/*",
    "__tests__/*",
    "spec/*",
    "specs/*",
    "fixtures/*",
    "mocks/*",
    "*/*.test.*",
    "*/*.spec.*",
    "*.snap",
]


@dataclass(frozen=True)
class PackedFile:
    rel_posix: str
    abs_path: Path
    language: str
    size: int
    line_count: int
    content: str


def _git_ls_files(repo_root: Path) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def collect_core_files(
    repo_root: Path,
    max_file_size: int,
    extra_ignore: list[str] | None = None,
    core_only: bool = True,
    prefer_git: bool = True,
    include_patterns: list[str] | None = None,
) -> tuple[list[PackedFile], dict[str, int]]:
    """
    Collect (mostly) code files for ONEPDF_CORE.

    prefer_git uses `git ls-files` to avoid vendor/build outputs that might be
    present in working trees but not in the repo.
    """
    extra_ignore = extra_ignore or []
    include_patterns = include_patterns or []

    ignore_patterns = [*DEFAULT_IGNORE_PATTERNS, *extra_ignore]
    if core_only:
        ignore_patterns = [*ignore_patterns, *DEFAULT_CORE_IGNORE_PATTERNS]

    stats = {
        "seen_files": 0,
        "included": 0,
        "ignored_by_pattern": 0,
        "skipped_unreadable": 0,
        "skipped_size_or_empty": 0,
        "skipped_binary": 0,
        "skipped_not_included": 0,
        "skipped_path_escape": 0,
    }

    rel_files: list[str] = []
    if prefer_git:
        rel_files = _git_ls_files(repo_root) or []
    if not rel_files:
        for dirpath, dirnames, filenames in os.walk(repo_root):
            dirnames[:] = sorted(d for d in dirnames if not should_ignore_dir(d))
            for filename in filenames:
                rel_files.append(str((Path(dirpath) / filename).relative_to(repo_root)))

    packed: list[PackedFile] = []
    for rel in rel_files:
        stats["seen_files"] += 1
        rel_posix = normalize_posix_path(rel)

        if include_patterns and not matches_any(rel_posix, include_patterns):
            stats["skipped_not_included"] += 1
            continue

        basename = PurePosixPath(rel_posix).name
        if matches_any(rel_posix, ignore_patterns) or matches_any(basename, ignore_patterns):
            stats["ignored_by_pattern"] += 1
            continue

        abs_path = safe_join_repo(repo_root, rel_posix)
        if abs_path is None:
            stats["skipped_path_escape"] += 1
            continue

        try:
            st = abs_path.stat()
        except OSError:
            stats["skipped_unreadable"] += 1
            continue

        size = int(st.st_size)
        if size == 0 or size > max_file_size:
            stats["skipped_size_or_empty"] += 1
            continue

        try:
            blob = abs_path.read_bytes()
        except OSError:
            stats["skipped_unreadable"] += 1
            continue

        if not is_probably_text(blob):
            stats["skipped_binary"] += 1
            continue

        packed.append(
            PackedFile(
                rel_posix=rel_posix,
                abs_path=abs_path,
                language=detect_language(rel_posix),
                size=size,
                line_count=line_count_from_bytes(blob),
                content=blob.decode("utf-8", errors="replace"),
            )
        )
        stats["included"] += 1

    packed.sort(key=lambda f: f.rel_posix)
    return packed, stats


def _ascii_safe(s: str) -> str:
    # Keep PDF text simple and predictable: escape non-ASCII as \\uXXXX.
    out: list[str] = []
    for ch in s:
        code = ord(ch)
        if ch == "\t":
            out.append("    ")
        elif ch in ("\r",):
            continue
        elif 32 <= code <= 126:
            out.append(ch)
        elif ch == "\n":
            out.append("\n")
        else:
            out.append(f"\\u{code:04x}")
    return "".join(out)


def _wrap_line(line: str, max_cols: int) -> list[str]:
    if max_cols <= 0 or len(line) <= max_cols:
        return [line]
    return [line[i : i + max_cols] for i in range(0, len(line), max_cols)]


def pack_repo_to_one_pdf(
    repo_root: Path,
    out_pdf: Path,
    max_file_size: int = 512 * 1024,
    extra_ignore: list[str] | None = None,
    core_only: bool = True,
    prefer_git: bool = True,
    include_patterns: list[str] | None = None,
    max_cols: int = 120,
    wrap: bool = True,
    include_tree: bool = True,
    include_index: bool = True,
) -> dict[str, int]:
    """Pack repository files into a single minimized PDF (ONEPDF_CORE)."""
    files, stats = collect_core_files(
        repo_root=repo_root,
        max_file_size=max_file_size,
        extra_ignore=extra_ignore,
        core_only=core_only,
        prefer_git=prefer_git,
        include_patterns=include_patterns,
    )

    # Render into pages as a list of lines first (more compact streams, simpler logic).
    font_size = 7
    leading = 9  # points
    top = 36
    bottom = 36
    page_height = 842
    max_lines = max(1, int((page_height - top - bottom) / leading))

    lines: list[str] = []
    lines.append("pixcode onepdf")
    lines.append(f"repo: {repo_root.name}")
    lines.append(f"generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"files: {len(files)}")
    lines.append("")

    if include_tree:
        lines.append("== tree ==")
        tree_str = build_tree([f.rel_posix for f in files], repo_root.name, style="ascii")
        lines.extend(tree_str.split("\n"))
        lines.append("")

    if include_index:
        lines.append("== index ==")
        for idx, f in enumerate(files, start=1):
            lines.append(f"{idx:04d}  {f.rel_posix}  ({f.line_count} lines, {f.size} bytes)")
        lines.append("")

    lines.append("== files ==")
    lines.append("")

    for idx, f in enumerate(files, start=1):
        header = f"[{idx:04d}] {f.rel_posix}  ({f.language}, {f.line_count} lines, {f.size} bytes)"
        lines.append(header)
        lines.append("-" * min(max_cols, max(10, len(header))))
        content = _ascii_safe(f.content)
        for raw_line in content.split("\n"):
            raw_line = raw_line.rstrip()
            if wrap:
                for chunk in _wrap_line(raw_line, max_cols):
                    lines.append(chunk)
            else:
                lines.append(raw_line)
        lines.append("")

    # Convert lines to page streams.
    page_streams: list[bytes] = []
    current: list[str] = []

    def flush_page():
        if not current:
            return
        start_x = 36
        start_y = 842 - 36 - font_size
        parts: list[bytes] = []
        parts.append(b"BT\n")
        parts.append(b"/F1 %d Tf\n" % font_size)
        parts.append(b"%d TL\n" % leading)
        parts.append(b"%d %d Td\n" % (start_x, start_y))
        for l in current:
            esc = pdf_escape_literal(l)
            parts.append(b"(" + esc.encode("ascii", errors="replace") + b") Tj\nT*\n")
        parts.append(b"ET\n")
        page_streams.append(b"".join(parts))
        current.clear()

    for l in lines:
        current.append(l)
        if len(current) >= max_lines:
            flush_page()
    flush_page()

    writer = MinimalPDFWriter(title=f"{repo_root.name} onepdf")
    writer.build(page_streams, out_pdf)
    stats["pages"] = len(page_streams)
    stats["output_bytes"] = int(out_pdf.stat().st_size) if out_pdf.exists() else 0
    return stats
