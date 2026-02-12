from __future__ import annotations

import fnmatch
import os
import subprocess
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .constants import DEFAULT_IGNORE_DIRS, DEFAULT_IGNORE_PATTERNS
from .syntax import LANG_MAP


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


def _normalize_posix(rel_path: str) -> str:
    # git ls-files already returns forward slashes, but keep it robust.
    return str(PurePosixPath(rel_path))


def _matches_any(path_posix: str, patterns: list[str]) -> bool:
    lower = path_posix.lower()
    for pat in patterns:
        if fnmatch.fnmatch(path_posix, pat) or fnmatch.fnmatch(lower, pat.lower()):
            return True
    return False


def _is_probably_text(blob: bytes) -> bool:
    return b"\x00" not in blob[:8192]


def _line_count_from_bytes(blob: bytes) -> int:
    if not blob:
        return 0
    n = blob.count(b"\n")
    if not blob.endswith(b"\n"):
        n += 1
    return n


def _detect_language(path_posix: str) -> str:
    name = PurePosixPath(path_posix).name.lower()
    special = {
        "dockerfile": "docker",
        "makefile": "makefile",
        "cmakelists.txt": "cmake",
        "rakefile": "ruby",
        "gemfile": "ruby",
        "requirements.txt": "text",
        "pipfile": "toml",
        "cargo.toml": "toml",
        "go.mod": "go",
        "go.sum": "text",
    }
    if name in special:
        return special[name]
    suffix = PurePosixPath(path_posix).suffix.lower()
    return LANG_MAP.get(suffix, "text")


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
    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return files


def collect_core_files(
    repo_root: Path,
    max_file_size: int,
    extra_ignore: list[str] | None = None,
    core_only: bool = True,
    prefer_git: bool = True,
    include_patterns: list[str] | None = None,
) -> tuple[list[PackedFile], dict[str, int]]:
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
    }

    rel_files: list[str] = []
    if prefer_git:
        rel_files = _git_ls_files(repo_root) or []
    if not rel_files:
        # Fallback: walk the tree. This is less "core" than git ls-files but works anywhere.
        for dirpath, dirnames, filenames in os.walk(repo_root):
            # Avoid descending into typical junk dirs and dot-dirs.
            dirnames[:] = sorted(
                d
                for d in dirnames
                if (d not in DEFAULT_IGNORE_DIRS) and (not d.startswith("."))
            )
            for filename in filenames:
                rel_files.append(str((Path(dirpath) / filename).relative_to(repo_root)))

    packed: list[PackedFile] = []
    for rel in rel_files:
        stats["seen_files"] += 1
        rel_posix = _normalize_posix(rel)

        if include_patterns and not _matches_any(rel_posix, include_patterns):
            stats["skipped_not_included"] += 1
            continue

        if _matches_any(rel_posix, ignore_patterns) or _matches_any(
            PurePosixPath(rel_posix).name, ignore_patterns
        ):
            stats["ignored_by_pattern"] += 1
            continue

        abs_path = (repo_root / PurePosixPath(rel_posix)).resolve()
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

        if not _is_probably_text(blob):
            stats["skipped_binary"] += 1
            continue

        line_count = _line_count_from_bytes(blob)
        content = blob.decode("utf-8", errors="replace")
        packed.append(
            PackedFile(
                rel_posix=rel_posix,
                abs_path=abs_path,
                language=_detect_language(rel_posix),
                size=size,
                line_count=line_count,
                content=content,
            )
        )
        stats["included"] += 1

    packed.sort(key=lambda f: f.rel_posix)
    return packed, stats


def _build_tree(rel_paths_posix: list[str], root_name: str) -> str:
    tree: dict[str, dict | None] = {}
    for p in rel_paths_posix:
        parts = PurePosixPath(p).parts
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(f"{part}/", {})
        node[parts[-1]] = None

    lines = [f"{root_name}/"]

    def walk(node: dict, prefix: str):
        items = list(node.items())
        for idx, (name, subtree) in enumerate(items):
            is_last = idx == len(items) - 1
            # ASCII connectors: small, predictable, and glyph-safe in built-in PDF fonts.
            connector = "`-- " if is_last else "|-- "
            lines.append(f"{prefix}{connector}{name}")
            if subtree is not None:
                extension = "    " if is_last else "|   "
                walk(subtree, prefix + extension)

    walk(tree, "")
    return "\n".join(lines)


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
    if max_cols <= 0:
        return [line]
    if len(line) <= max_cols:
        return [line]
    chunks = []
    start = 0
    while start < len(line):
        chunks.append(line[start : start + max_cols])
        start += max_cols
    return chunks


def _pdf_escape_literal(s: str) -> str:
    # PDF literal string escaping.
    return (
        s.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


class _MinimalPDFWriter:
    def __init__(self, title: str):
        self.title = title
        self._objects: list[bytes] = []

    def _add_obj(self, body: bytes) -> int:
        self._objects.append(body)
        return len(self._objects)  # 1-based object numbers

    def build(self, page_streams: list[bytes], out_path: Path) -> None:
        # Object 1: Catalog
        # Object 2: Pages (Kids filled after page objs are created)
        # Object 3: Shared resources (built-in fonts, no embedding)
        resources_obj = self._add_obj(
            b"<< /ProcSet [/PDF /Text]\n"
            b"/Font <<\n"
            b"  /F1 << /Type /Font /Subtype /Type1 /BaseFont /Courier >>\n"
            b"  /F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\n"
            b">>\n"
            b">>"
        )

        pages_placeholder = self._add_obj(b"")  # replaced later
        catalog_obj = self._add_obj(f"<< /Type /Catalog /Pages {pages_placeholder} 0 R >>".encode("ascii"))

        page_obj_ids: list[int] = []
        content_obj_ids: list[int] = []

        # A4 in points.
        media_box = b"[0 0 595 842]"
        for stream in page_streams:
            compressed = zlib.compress(stream, level=9)
            content_obj = self._add_obj(
                b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(compressed)
                + compressed
                + b"\nendstream"
            )
            content_obj_ids.append(content_obj)

            page_obj = self._add_obj(
                b"<< /Type /Page\n"
                + b"/Parent %d 0 R\n" % pages_placeholder
                + b"/MediaBox "
                + media_box
                + b"\n/Resources %d 0 R\n" % resources_obj
                + b"/Contents %d 0 R\n" % content_obj
                + b">>"
            )
            page_obj_ids.append(page_obj)

        pages_obj_body = (
            b"<< /Type /Pages\n"
            + b"/Count %d\n" % len(page_obj_ids)
            + b"/Kids ["
            + b" ".join(b"%d 0 R" % pid for pid in page_obj_ids)
            + b"]\n>>"
        )
        self._objects[pages_placeholder - 1] = pages_obj_body

        # Write file with xref.
        header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        chunks: list[bytes] = [header]
        offsets: list[int] = [0]  # obj 0
        offset = len(header)

        for i, body in enumerate(self._objects, start=1):
            offsets.append(offset)
            obj = b"%d 0 obj\n" % i + body + b"\nendobj\n"
            chunks.append(obj)
            offset += len(obj)

        xref_offset = offset
        xref_lines = [b"xref\n", b"0 %d\n" % (len(self._objects) + 1)]
        xref_lines.append(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            xref_lines.append(f"{off:010d} 00000 n \n".encode("ascii"))
        xref = b"".join(xref_lines)
        chunks.append(xref)

        # Minimal trailer; avoid metadata to keep size low.
        trailer = (
            b"trailer\n"
            b"<<\n"
            b"/Size %d\n" % (len(self._objects) + 1)
            + b"/Root %d 0 R\n" % catalog_obj
            + b">>\n"
            b"startxref\n"
            + str(xref_offset).encode("ascii")
            + b"\n%%EOF\n"
        )
        chunks.append(trailer)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"".join(chunks))


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
    lines.append(f"pixcode onepdf")
    lines.append(f"repo: {repo_root.name}")
    lines.append(f"generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"files: {len(files)}")
    lines.append("")

    if include_tree:
        lines.append("== tree ==")
        tree_str = _build_tree([f.rel_posix for f in files], repo_root.name)
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
        # Build a compact text-only content stream.
        # Start at top-left, then T* steps down by leading.
        start_x = 36
        start_y = 842 - 36 - font_size
        tl = leading
        parts: list[bytes] = []
        parts.append(b"BT\n")
        parts.append(b"/F1 %d Tf\n" % font_size)
        parts.append(b"%d TL\n" % tl)
        parts.append(b"%d %d Td\n" % (start_x, start_y))
        for l in current:
            esc = _pdf_escape_literal(l)
            parts.append(b"(" + esc.encode("ascii", errors="replace") + b") Tj\nT*\n")
        parts.append(b"ET\n")
        page_streams.append(b"".join(parts))
        current.clear()

    for l in lines:
        current.append(l)
        if len(current) >= max_lines:
            flush_page()
    flush_page()

    writer = _MinimalPDFWriter(title=f"{repo_root.name} onepdf")
    writer.build(page_streams, out_pdf)
    stats["pages"] = len(page_streams)
    stats["output_bytes"] = int(out_pdf.stat().st_size) if out_pdf.exists() else 0
    return stats
