import ast
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from .file_utils import normalize_posix_path
from .models import FileInfo, LintIssue, RepoInfo, SemanticMap

JS_CLASS_PAT = re.compile(
    r"^\s*class\s+([A-Za-z_]\w*)(?:\s+extends\s+([A-Za-z_]\w*))?",
    re.MULTILINE,
)
JS_FN_PATS = (
    re.compile(r"^\s*function\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE),
    re.compile(r"^\s*const\s+([A-Za-z_]\w*)\s*=\s*\([^)]*\)\s*=>", re.MULTILINE),
    re.compile(r"^\s*([A-Za-z_]\w*)\s*:\s*function\s*\(", re.MULTILINE),
)
JS_CALL_PAT = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

log = logging.getLogger(__name__)

MAX_SEMANTIC_LINES = 24


class CodeInsightEngine:
    def __init__(
        self,
        repo: RepoInfo,
        enable_semantic_minimap: bool = True,
        enable_lint_heatmap: bool = True,
        linter_timeout: int = 20,
    ):
        self.repo = repo
        self.enable_semantic_minimap = enable_semantic_minimap
        self.enable_lint_heatmap = enable_lint_heatmap
        self.linter_timeout = linter_timeout
        self._resolved_root = self.repo.root.resolve()
        self._scanned_paths = {self._normalize_path(info.path) for info in self.repo.files}
        self._scanned_paths_ci = {k.lower(): k for k in self._scanned_paths} if os.name == "nt" else {}
        self._cache_root = self._resolved_root / ".pixrep_cache"
        self._semantic_cache_dir = self._cache_root / "semantic"
        self._lint_cache_dir = self._cache_root / "lint"
        self._semantic_cache_dir.mkdir(parents=True, exist_ok=True)
        self._lint_cache_dir.mkdir(parents=True, exist_ok=True)

    def enrich_repo(self):
        """Populate semantic maps and lint issues onto RepoInfo.file entries."""
        lint_map = self._collect_lint_issues() if self.enable_lint_heatmap else {}
        lint_map_ci = {k.lower(): v for k, v in lint_map.items()} if os.name == "nt" else {}
        matched = 0

        semantic_maps: dict[int, SemanticMap] = {}
        if self.enable_semantic_minimap and self.repo.files:
            workers = min(4, max(1, os.cpu_count() or 1))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {
                    pool.submit(self._build_semantic_map_cached, info): idx
                    for idx, info in enumerate(self.repo.files)
                }
                for fut in concurrent.futures.as_completed(future_map):
                    idx = future_map[fut]
                    try:
                        semantic_maps[idx] = fut.result()
                    except Exception:
                        semantic_maps[idx] = SemanticMap(kind="callgraph", lines=["(analysis failed)"], node_count=0, edge_count=0)

        for idx, info in enumerate(self.repo.files):
            if self.enable_semantic_minimap:
                info.semantic_map = semantic_maps.get(idx, SemanticMap())
            else:
                info.semantic_map = SemanticMap()
            key = self._normalize_path(info.path)
            issues = lint_map.get(key)
            if issues is None and lint_map_ci:
                issues = lint_map_ci.get(key.lower())
            info.lint_issues = issues or []
            if info.lint_issues:
                matched += 1

        if self.enable_lint_heatmap and lint_map and matched == 0:
            log.warning(
                "Linter found %d files with issues but none matched scanned files. Path normalization mismatch?",
                len(lint_map),
            )
            sample_lint = next(iter(lint_map))
            sample_file = self._normalize_path(self.repo.files[0].path) if self.repo.files else "(none)"
            log.debug("sample lint path=%r, sample file path=%r", sample_lint, sample_file)

    def _collect_lint_issues(self) -> dict[str, list[LintIssue]]:
        issues: dict[str, list[LintIssue]] = defaultdict(list)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_ruff = pool.submit(self._collect_ruff)
            fut_eslint = pool.submit(self._collect_eslint)
            done, not_done = concurrent.futures.wait(
                {fut_ruff, fut_eslint},
                timeout=max(1, self.linter_timeout * 2),
            )

            for fut in done:
                partial = fut.result() or {}
                for rel, rel_issues in partial.items():
                    issues[rel].extend(rel_issues)

            for fut in not_done:
                fut.cancel()
        return dict(issues)

    def _run_json_command(self, cmd: list[str], *, cwd: Path, tool: str):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=self.linter_timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            log.debug("%s invocation failed or timed out", tool)
            return None

        payload = (proc.stdout or "").strip()
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            log.debug("%s output was not valid json", tool)
            return None

    def _collect_ruff(self) -> dict[str, list[LintIssue]]:
        issues: dict[str, list[LintIssue]] = defaultdict(list)
        if not shutil.which("ruff"):
            return {}
        targets = [self._normalize_path(info.path) for info in self.repo.files if info.language == "python"]
        if not targets:
            return {}

        cache_key = self._tool_cache_key("ruff", targets)
        cached = self._load_lint_cache("ruff", cache_key)
        if cached is not None:
            return cached

        cmd = ["ruff", "check", "--output-format", "json", *targets]
        data = self._run_json_command(cmd, cwd=self.repo.root, tool="ruff")
        if not data:
            return {}
        for item in data:
            filename = item.get("filename")
            location = item.get("location", {})
            row = int(location.get("row", 1))
            code = str(item.get("code", "RUFF"))
            message = str(item.get("message", "ruff finding"))
            rel = self._relative_to_repo(filename)
            if not rel:
                continue
            issues[rel].append(
                LintIssue(
                    line=max(1, row),
                    severity=self._ruff_severity(code),
                    tool="ruff",
                    code=code,
                    message=message,
                )
            )
        result = dict(issues)
        self._save_lint_cache("ruff", cache_key, result)
        return result

    def _collect_eslint(self) -> dict[str, list[LintIssue]]:
        issues: dict[str, list[LintIssue]] = defaultdict(list)
        if not shutil.which("eslint"):
            return {}
        targets = [
            self._normalize_path(info.path)
            for info in self.repo.files
            if info.language in {"javascript", "typescript"}
        ]
        if not targets:
            return {}

        cache_key = self._tool_cache_key("eslint", targets)
        cached = self._load_lint_cache("eslint", cache_key)
        if cached is not None:
            return cached

        cmd = [
            "eslint",
            "--format",
            "json",
            *targets,
        ]
        files = self._run_json_command(cmd, cwd=self.repo.root, tool="eslint")
        if not files:
            return {}
        for entry in files:
            rel = self._relative_to_repo(entry.get("filePath", ""))
            if not rel:
                continue
            for msg in entry.get("messages", []):
                line = int(msg.get("line", 1))
                sev = int(msg.get("severity", 1))
                code = str(msg.get("ruleId") or "ESLINT")
                text = str(msg.get("message", "eslint finding"))
                issues[rel].append(
                    LintIssue(
                        line=max(1, line),
                        severity="high" if sev >= 2 else "medium",
                        tool="eslint",
                        code=code,
                        message=text,
                    )
                )
        result = dict(issues)
        self._save_lint_cache("eslint", cache_key, result)
        return result

    def _build_semantic_map(self, info: FileInfo) -> SemanticMap:
        content = info.load_content()
        if info.language == "python":
            return self._python_semantic_map(content)
        if info.language in {"javascript", "typescript"}:
            return self._js_semantic_map(content)
        return self._generic_semantic_map(content, info.language)

    def _build_semantic_map_cached(self, info: FileInfo) -> SemanticMap:
        cache_key = self._semantic_cache_key(info)
        cache_path = self._semantic_cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                return SemanticMap(
                    kind=str(payload.get("kind", "none")),
                    lines=[str(line) for line in payload.get("lines", [])],
                    node_count=int(payload.get("node_count", 0)),
                    edge_count=int(payload.get("edge_count", 0)),
                    truncated=bool(payload.get("truncated", False)),
                )
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                pass

        semantic_map = self._build_semantic_map(info)
        try:
            cache_path.write_text(
                json.dumps(
                    {
                        "kind": semantic_map.kind,
                        "lines": semantic_map.lines,
                        "node_count": semantic_map.node_count,
                        "edge_count": semantic_map.edge_count,
                        "truncated": semantic_map.truncated,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        return semantic_map

    def _python_semantic_map(self, content: str) -> SemanticMap:
        content = content.lstrip("\ufeff")
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return SemanticMap(kind="callgraph", lines=["(parse failed)"], node_count=0, edge_count=0)

        collector = _PyDefCollector()
        collector.visit(tree)

        classes: dict[str, list[str]] = collector.classes
        inherits: list[tuple[str, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    bname = self._ast_name(base)
                    if bname:
                        inherits.append((node.name, bname))

        defined = set(collector.module_funcs) | set(collector.qualified_methods)
        walker = _PyCallVisitor(defined, collector.class_methods)
        walker.visit(tree)
        edges = walker.edges

        lines: list[str] = []
        if classes:
            lines.append("UML:")
            for class_name, class_methods in list(classes.items())[:6]:
                lines.append(f"[Class] {class_name}")
                for method in class_methods[:4]:
                    lines.append(f"  - {method}()")
            for child, parent in inherits[:6]:
                lines.append(f"{child} <|-- {parent}")
        if edges:
            lines.append("Call Graph:")
            for src, dst in list(sorted(edges))[:10]:
                lines.append(f"{src} -> {dst}")
        if not lines:
            lines = ["(no classes/functions detected)"]

        lines, truncated = self._limit_semantic_lines(lines)
        return SemanticMap(
            kind="uml+callgraph" if classes else "callgraph",
            lines=lines,
            node_count=len(defined),
            edge_count=len(edges),
            truncated=truncated,
        )

    def _js_semantic_map(self, content: str) -> SemanticMap:
        classes = JS_CLASS_PAT.findall(content)
        func_spans = self._js_function_spans(content)
        funcs = {name for name, _, _ in func_spans}

        call_edges: set[tuple[str, str]] = set()
        max_edges = 64
        js_keywords = {"if", "for", "while", "switch", "catch", "function", "return", "new"}
        for src, start, end in func_spans:
            body = content[start:end]
            for callee in JS_CALL_PAT.findall(body)[:400]:
                if callee in js_keywords:
                    continue
                if callee in funcs and callee != src:
                    call_edges.add((src, callee))
                    if len(call_edges) >= max_edges:
                        break
            if len(call_edges) >= max_edges:
                break

        lines: list[str] = []
        if classes:
            lines.append("UML:")
            for class_name, parent in classes[:6]:
                lines.append(f"[Class] {class_name}")
                if parent:
                    lines.append(f"{class_name} <|-- {parent}")
        if funcs:
            lines.append("Functions:")
            for func_name in sorted(funcs)[:8]:
                lines.append(f"  - {func_name}()")
        if call_edges:
            lines.append("Call Graph:")
            for src, dst in sorted(call_edges)[:10]:
                lines.append(f"{src} -> {dst}")
        if not lines:
            lines = ["(no symbols detected)"]

        lines, truncated = self._limit_semantic_lines(lines)
        return SemanticMap(
            kind="uml+callgraph" if classes else "callgraph",
            lines=lines,
            node_count=len(funcs) + len(classes),
            edge_count=len(call_edges),
            truncated=truncated,
        )

    def _generic_semantic_map(self, content: str, language: str) -> SemanticMap:
        if language in {"text", "json", "yaml", "toml", "markdown", "ini"}:
            return SemanticMap(kind="none", lines=[])
        sigs = re.findall(r"^\s*(?:def|fn|func|function)\s+([A-Za-z_]\w*)", content, flags=re.MULTILINE)
        lines = ["Functions:"] + [f"  - {name}()" for name in sigs[:12]] if sigs else ["(no symbols detected)"]
        lines, truncated = self._limit_semantic_lines(lines)
        return SemanticMap(
            kind="callgraph",
            lines=lines,
            node_count=len(sigs),
            edge_count=0,
            truncated=truncated,
        )

    @staticmethod
    def _ruff_severity(code: str) -> str:
        if code.startswith(("F", "E", "B", "SIM", "PLR")):
            return "high"
        if code.startswith(("W", "RUF", "C90")):
            return "medium"
        return "medium"

    def _relative_to_repo(self, path_value: str) -> str | None:
        if not path_value:
            return None

        root = self._resolved_root
        p = Path(path_value)
        candidate = p if p.is_absolute() else (root / p)
        try:
            resolved = candidate.resolve()
        except OSError:
            return None

        try:
            if not resolved.is_relative_to(root):
                return None
            rel = resolved.relative_to(root)
            norm = self._normalize_path(rel)
            if norm in self._scanned_paths:
                return norm
            if os.name == "nt":
                return self._scanned_paths_ci.get(norm.lower())
            return None
        except (ValueError, OSError):
            return None

    @staticmethod
    def _normalize_path(path_value: str | Path) -> str:
        return normalize_posix_path(path_value)

    def _semantic_cache_key(self, info: FileInfo) -> str:
        try:
            st = info.abs_path.stat()
            sig = f"{self._normalize_path(info.path)}|{st.st_mtime_ns}|{st.st_size}|v2"
        except OSError:
            sig = f"{self._normalize_path(info.path)}|missing|v2"
        return hashlib.sha1(sig.encode("utf-8")).hexdigest()

    def _tool_cache_key(self, tool: str, targets: list[str]) -> str:
        records: list[str] = []
        for rel in sorted(set(targets)):
            abs_path = self._resolved_root / Path(rel)
            try:
                st = abs_path.stat()
                records.append(f"{rel}|{st.st_mtime_ns}|{st.st_size}")
            except OSError:
                records.append(f"{rel}|missing")
        payload = f"{tool}|v2|" + "\n".join(records)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _load_lint_cache(self, tool: str, key: str) -> dict[str, list[LintIssue]] | None:
        path = self._lint_cache_dir / f"{tool}_{key}.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        restored: dict[str, list[LintIssue]] = {}
        for rel, entries in raw.items():
            restored[rel] = [
                LintIssue(
                    line=int(item.get("line", 1)),
                    severity=str(item.get("severity", "medium")),
                    tool=str(item.get("tool", tool)),
                    code=str(item.get("code", tool.upper())),
                    message=str(item.get("message", "")),
                )
                for item in entries
            ]
        return restored

    def _save_lint_cache(self, tool: str, key: str, issues: dict[str, list[LintIssue]]) -> None:
        path = self._lint_cache_dir / f"{tool}_{key}.json"
        payload = {
            rel: [
                {
                    "line": issue.line,
                    "severity": issue.severity,
                    "tool": issue.tool,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in rel_issues
            ]
            for rel, rel_issues in issues.items()
        }
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return

    @staticmethod
    def _limit_semantic_lines(lines: list[str]) -> tuple[list[str], bool]:
        if len(lines) > MAX_SEMANTIC_LINES:
            return lines[:MAX_SEMANTIC_LINES], True
        return lines, False

    @staticmethod
    def _ast_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    @staticmethod
    def _js_function_spans(content: str) -> list[tuple[str, int, int]]:
        """
        Extract function spans for JS/TS using brace-balance to find exact
        function body end positions.

        This replaces the previous heuristic of treating the next function
        definition's start as the current function's end — which was wrong for
        nested functions, arrow functions, and closures.
        """
        hits: list[tuple[str, int]] = []
        for pat in JS_FN_PATS:
            for m in pat.finditer(content):
                hits.append((m.group(1), m.start()))

        if not hits:
            return []

        ordered = sorted(hits, key=lambda t: t[1])
        content_len = len(content)
        spans: list[tuple[str, int, int]] = []

        for name, start in ordered:
            # Find the opening brace of the function body.
            brace_start = content.find("{", start)
            if brace_start == -1:
                continue

            end = CodeInsightEngine._balanced_brace_end(content, brace_start)
            end = min(end, content_len)

            spans.append((name, start, end))

        return spans

    @staticmethod
    def _balanced_brace_end(content: str, brace_start: int) -> int:
        depth = 0
        i = brace_start
        in_string: str | None = None
        template_expr_depth = 0
        escaped = False
        length = len(content)

        while i < length:
            ch = content[i]
            nxt = content[i + 1] if i + 1 < length else ""

            if in_string:
                if escaped:
                    escaped = False
                    i += 1
                    continue
                if ch == "\\":
                    escaped = True
                    i += 1
                    continue
                if in_string == "`" and ch == "$" and nxt == "{" and template_expr_depth >= 0:
                    template_expr_depth += 1
                    i += 2
                    continue
                if in_string == "`" and ch == "}" and template_expr_depth > 0:
                    template_expr_depth -= 1
                    i += 1
                    continue
                if in_string == "`" and template_expr_depth > 0:
                    i += 1
                    continue
                if ch == in_string:
                    in_string = None
                i += 1
                continue

            if ch == "/" and nxt == "/":
                nl = content.find("\n", i + 2)
                if nl == -1:
                    return length
                i = nl + 1
                continue
            if ch == "/" and nxt == "*":
                end = content.find("*/", i + 2)
                if end == -1:
                    return length
                i = end + 2
                continue

            if ch in {'"', "'", "`"}:
                in_string = ch
                escaped = False
                template_expr_depth = 0
                i += 1
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return length


class _PyDefCollector(ast.NodeVisitor):
    def __init__(self):
        self.class_stack: list[str] = []
        self.function_depth = 0
        self.classes: dict[str, list[str]] = {}
        self.class_methods: dict[str, set[str]] = defaultdict(set)
        self.module_funcs: set[str] = set()
        self.qualified_methods: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef):
        self.class_stack.append(node.name)
        self.classes.setdefault(node.name, [])
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._record_function(node.name)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._record_function(node.name)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def _record_function(self, name: str):
        if self.class_stack and self.function_depth == 0:
            cls = self.class_stack[-1]
            self.classes.setdefault(cls, []).append(name)
            self.class_methods[cls].add(name)
            self.qualified_methods.add(f"{cls}.{name}")
        elif not self.class_stack and self.function_depth == 0:
            self.module_funcs.add(name)


class _PyCallVisitor(ast.NodeVisitor):
    def __init__(self, defined_symbols: set[str], class_methods: dict[str, set[str]]):
        self.defined = defined_symbols
        self.scope: list[str] = ["(module)"]
        self.class_stack: list[str] = []
        self.class_methods = class_methods
        self.edges: set[tuple[str, str]] = set()

    def visit_ClassDef(self, node: ast.ClassDef):
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if self.class_stack:
            current = f"{self.class_stack[-1]}.{node.name}"
        else:
            current = node.name
        self.scope.append(current)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        # Treat async defs like regular functions for best-effort call graphs.
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_Call(self, node: ast.Call):
        callee = self._call_name(node.func)
        current = self.scope[-1]
        if callee and callee in self.defined:
            self.edges.add((current, callee))
        self.generic_visit(node)

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            owner = node.value
            method = node.attr
            if isinstance(owner, ast.Name):
                if owner.id in {"self", "cls"} and self.class_stack:
                    cls = self.class_stack[-1]
                    if method in self.class_methods.get(cls, set()):
                        return f"{cls}.{method}"
                if owner.id in self.class_methods and method in self.class_methods.get(owner.id, set()):
                    return f"{owner.id}.{method}"
        return ""
