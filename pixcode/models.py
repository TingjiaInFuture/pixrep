from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SemanticMap:
    kind: str = "none"
    lines: list[str] = field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0


@dataclass
class LintIssue:
    line: int
    severity: str
    tool: str
    code: str
    message: str


@dataclass
class FileInfo:
    path: Path
    abs_path: Path
    language: str
    size: int
    line_count: int = 0
    content: str = ""
    index: int = 0
    semantic_map: SemanticMap = field(default_factory=SemanticMap)
    lint_issues: list[LintIssue] = field(default_factory=list)


@dataclass
class RepoInfo:
    root: Path
    name: str
    files: list[FileInfo] = field(default_factory=list)
    total_lines: int = 0
    total_size: int = 0
    language_stats: dict = field(default_factory=dict)
    tree_str: str = ""
