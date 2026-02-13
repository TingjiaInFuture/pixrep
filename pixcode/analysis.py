import ast
import json
import logging
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

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

    def enrich_repo(self):
        """Populate semantic maps and lint issues onto RepoInfo.file entries."""
        lint_map = self._collect_lint_issues() if self.enable_lint_heatmap else {}
        for info in self.repo.files:
            info.semantic_map = (
                self._build_semantic_map(info) if self.enable_semantic_minimap else SemanticMap()
            )
            info.lint_issues = lint_map.get(self._normalize_path(info.path), [])

    def _collect_lint_issues(self) -> dict[str, list[LintIssue]]:
        issues: dict[str, list[LintIssue]] = defaultdict(list)
        self._collect_ruff(issues)
        self._collect_eslint(issues)
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

    def _collect_ruff(self, issues: dict[str, list[LintIssue]]):
        if not shutil.which("ruff"):
            return
        cmd = ["ruff", "check", "--output-format", "json", str(self.repo.root)]
        data = self._run_json_command(cmd, cwd=self.repo.root, tool="ruff")
        if not data:
            return
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

    def _collect_eslint(self, issues: dict[str, list[LintIssue]]):
        if not shutil.which("eslint"):
            return
        cmd = [
            "eslint",
            "--format",
            "json",
            ".",
            "--ext",
            ".js,.jsx,.ts,.tsx",
        ]
        files = self._run_json_command(cmd, cwd=self.repo.root, tool="eslint")
        if not files:
            return
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

    def _build_semantic_map(self, info: FileInfo) -> SemanticMap:
        if info.language == "python":
            return self._python_semantic_map(info.content)
        if info.language in {"javascript", "typescript"}:
            return self._js_semantic_map(info.content)
        return self._generic_semantic_map(info.content, info.language)

    def _python_semantic_map(self, content: str) -> SemanticMap:
        content = content.lstrip("\ufeff")
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return SemanticMap(kind="callgraph", lines=["(parse failed)"], node_count=0, edge_count=0)

        classes: dict[str, list[str]] = {}
        inherits: list[tuple[str, str]] = []
        funcs: set[str] = set()
        methods: set[str] = set()
        edges: set[tuple[str, str]] = set()

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_name = node.name
                class_methods: list[str] = []
                for base in node.bases:
                    bname = self._ast_name(base)
                    if bname:
                        inherits.append((class_name, bname))
                for body_node in node.body:
                    if isinstance(body_node, ast.FunctionDef):
                        class_methods.append(body_node.name)
                        methods.add(f"{class_name}.{body_node.name}")
                        methods.add(body_node.name)
                classes[class_name] = class_methods
            elif isinstance(node, ast.FunctionDef):
                funcs.add(node.name)

        defined = funcs | methods
        walker = _PyCallVisitor(defined)
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
        return SemanticMap(
            kind="uml+callgraph" if classes else "callgraph",
            lines=lines[:16],
            node_count=len(defined),
            edge_count=len(edges),
        )

    def _js_semantic_map(self, content: str) -> SemanticMap:
        classes = JS_CLASS_PAT.findall(content)
        func_spans = self._js_function_spans(content)
        funcs = {name for name, _, _ in func_spans}

        call_edges: set[tuple[str, str]] = set()
        max_edges = 16
        for src, start, end in func_spans[:12]:
            body = content[start:end]
            for callee in JS_CALL_PAT.findall(body)[:200]:
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
        return SemanticMap(
            kind="uml+callgraph" if classes else "callgraph",
            lines=lines[:16],
            node_count=len(funcs) + len(classes),
            edge_count=len(call_edges),
        )

    def _generic_semantic_map(self, content: str, language: str) -> SemanticMap:
        if language == "text":
            return SemanticMap(kind="none", lines=["(semantic minimap not available)"])
        sigs = re.findall(r"^\s*(?:def|fn|func|function)\s+([A-Za-z_]\w*)", content, flags=re.MULTILINE)
        lines = ["Functions:"] + [f"  - {name}()" for name in sigs[:12]] if sigs else ["(no symbols detected)"]
        return SemanticMap(kind="callgraph", lines=lines[:16], node_count=len(sigs), edge_count=0)

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
        try:
            root = self.repo.root.resolve()
            p = Path(path_value)
            if not p.is_absolute():
                p = root / p
            resolved = p.resolve()
            if not resolved.is_relative_to(root):
                return None
            rel = resolved.relative_to(root)
            return self._normalize_path(rel)
        except (ValueError, OSError):
            return None

    @staticmethod
    def _normalize_path(path_value: Path) -> str:
        return str(path_value).replace("\\", "/")

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
        Extract best-effort function spans for JS/TS.

        This is heuristic (regex-based) but provides a much more useful call
        graph than connecting every call to arbitrary "top N" symbols.
        """
        hits: list[tuple[str, int]] = []
        for pat in JS_FN_PATS:
            for m in pat.finditer(content):
                hits.append((m.group(1), m.start()))

        if not hits:
            return []

        # Deduplicate by name: keep earliest definition.
        earliest: dict[str, int] = {}
        for name, pos in hits:
            if name not in earliest or pos < earliest[name]:
                earliest[name] = pos

        ordered = sorted(((n, p) for n, p in earliest.items()), key=lambda t: t[1])
        spans: list[tuple[str, int, int]] = []
        for idx, (name, start) in enumerate(ordered):
            end = ordered[idx + 1][1] if idx + 1 < len(ordered) else len(content)
            spans.append((name, start, end))
        return spans


class _PyCallVisitor(ast.NodeVisitor):
    def __init__(self, defined_symbols: set[str]):
        self.defined = defined_symbols
        self.scope: list[str] = ["<module>"]
        self.class_stack: list[str] = []
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
        if callee in self.defined:
            self.edges.add((current, callee))
        self.generic_visit(node)

    @staticmethod
    def _call_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""
