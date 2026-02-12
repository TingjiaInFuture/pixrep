import argparse
import logging
import sys
from pathlib import Path

from .pdf_generator import PDFGenerator
from .scanner import RepoScanner
from .onepdf import pack_repo_to_one_pdf
from .version import __version__


log = logging.getLogger(__name__)


def _build_common_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "repo",
        nargs="?",
        default=".",
        help="Path to code repository (default: .) / 仓库路径（默认当前目录）",
    )
    common.add_argument(
        "--max-size",
        type=int,
        default=512,
        metavar="KB",
        help="Max file size in KB (default: 512) / 单文件最大大小（KB）",
    )
    common.add_argument(
        "--ignore",
        nargs="*",
        default=[],
        metavar="PATTERN",
        help="Extra ignore patterns, e.g. '*.test.js' / 额外忽略规则",
    )
    common.add_argument(
        "--log-level",
        default="INFO",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Logging level (default: INFO) / 日志级别",
    )
    return common


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="pixcode",
        description=(
            "Convert code repositories into hierarchical PDFs for LLM collaboration "
            "/ 将代码仓库转换为分层 PDF 以支持 LLM 协作"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Examples:
  pixcode .                                # Backward-compatible, same as: pixcode generate .
  pixcode generate /path/to/repo -o ./pdfs
  pixcode list . --top-languages 10
  pixcode help generate
        """,
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"pixcode {__version__}"
    )

    common = _build_common_parser()
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    generate_parser = subparsers.add_parser(
        "generate",
        parents=[common],
        help="Generate index + file PDFs / 生成索引与文件 PDF",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    generate_parser.add_argument(
        "-o", "--output", default=None,
        help="Output directory (default: ./pixcode_output/<repo>) / 输出目录",
    )
    generate_parser.add_argument(
        "--index-only", action="store_true",
        help="Generate only 00_INDEX.pdf / 仅生成索引 PDF",
    )
    generate_parser.add_argument(
        "--disable-semantic-minimap",
        action="store_true",
        help="Disable semantic UML/callgraph blocks / 关闭语义微缩图",
    )
    generate_parser.add_argument(
        "--disable-lint-heatmap",
        action="store_true",
        help="Disable linter severity background heatmap / 关闭 linter 热力图",
    )
    generate_parser.add_argument(
        "--linter-timeout",
        type=int,
        default=20,
        metavar="SECONDS",
        help="Timeout for each linter command (default: 20) / 单次 linter 超时",
    )
    generate_parser.add_argument(
        "--list-only", action="store_true",
        help=argparse.SUPPRESS,
    )

    list_parser = subparsers.add_parser(
        "list",
        parents=[common],
        help="Print tree and language stats / 打印目录树和语言统计",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    list_parser.add_argument(
        "--top-languages",
        type=int,
        default=0,
        metavar="N",
        help="Show only top N languages by lines (0 = all) / 仅显示前 N 个语言",
    )

    help_parser = subparsers.add_parser(
        "help",
        help="Show help for commands / 查看命令帮助",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    help_parser.add_argument(
        "topic",
        nargs="?",
        choices=["generate", "list", "onepdf", "allinone"],
        help="Command name / 命令名",
    )

    def _add_onepdf_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        p = subparsers.add_parser(
            name,
            parents=[common],
            help=help_text,
            formatter_class=argparse.RawTextHelpFormatter,
        )
        p.add_argument(
            "-o",
            "--output",
            default=None,
            help="Output PDF path (default: ./pixcode_output/<repo>/ONEPDF_CORE.pdf) / 输出 PDF 路径",
        )
        p.add_argument(
            "--no-core-only",
            action="store_true",
            help="Include non-core files (docs/tests) / 也包含非核心文件（文档/测试）",
        )
        p.add_argument(
            "--no-git",
            action="store_true",
            help="Do not prefer git ls-files (fallback to walking) / 不优先使用 git ls-files",
        )
        p.add_argument(
            "--include",
            nargs="*",
            default=[],
            metavar="PATTERN",
            help="Only include paths matching these patterns / 仅包含匹配这些规则的路径",
        )
        p.add_argument(
            "--cols",
            type=int,
            default=120,
            metavar="N",
            help="Max columns per line for wrapping (default: 120) / 自动换行列数",
        )
        p.add_argument(
            "--no-wrap",
            action="store_true",
            help="Disable line wrapping / 禁用自动换行",
        )
        p.add_argument(
            "--no-tree",
            action="store_true",
            help="Do not include directory tree section / 不包含目录树",
        )
        p.add_argument(
            "--no-index",
            action="store_true",
            help="Do not include file index section / 不包含文件索引",
        )
        return p

    onepdf_parser = _add_onepdf_parser(
        "onepdf",
        "Pack core code into a single minimized PDF / 核心代码一键打包成单个极简 PDF",
    )
    allinone_parser = _add_onepdf_parser(
        "allinone",
        "Alias of onepdf / onepdf 的别名",
    )

    commands = {
        "generate": generate_parser,
        "list": list_parser,
        "help": help_parser,
        "onepdf": onepdf_parser,
        "allinone": allinone_parser,
    }
    return parser, commands


def _normalize_legacy_args(argv: list[str]) -> list[str]:
    if not argv:
        return ["generate"]

    known_commands = {"generate", "list", "help", "onepdf", "allinone"}
    first = argv[0]
    if first in known_commands:
        return argv

    if first in {"-h", "--help", "-V", "--version"}:
        return argv

    if "--list-only" in argv:
        filtered = [arg for arg in argv if arg != "--list-only"]
        return ["list", *filtered]

    if first.startswith("-"):
        return ["generate", *argv]

    return ["generate", *argv]


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stdout,
    )
    # Keep reportlab quiet unless explicitly debugging.
    logging.getLogger("reportlab").setLevel(logging.WARNING)


def _scan_repo(args: argparse.Namespace, include_content: bool = True):
    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        log.error("Error: '%s' is not a directory", args.repo)
        return None, 1

    log.info("Scanning %s...", repo_path)
    scanner = RepoScanner(
        str(repo_path),
        max_file_size=args.max_size * 1024,
        extra_ignore=args.ignore,
    )
    repo = scanner.scan(include_content=include_content)
    if not repo.files:
        log.info("No files found.")
        return repo, 0
    stats = repo.scan_stats
    log.info(
        "Scan summary: seen=%d, loaded=%d, ignored=%d, size/empty=%d, binary=%d, errors=%d",
        stats.get("seen_files", 0),
        len(repo.files),
        stats.get("ignored_by_pattern", 0),
        stats.get("skipped_size_or_empty", 0),
        stats.get("skipped_binary", 0),
        stats.get("skipped_unreadable", 0),
    )
    return repo, 0


def _print_repo_list(repo, top_languages: int = 0):
    log.info("")
    log.info("%s (%d files)", repo.name, len(repo.files))
    log.info("")
    log.info("%s", repo.tree_str)
    log.info("")
    log.info("%-15s %6s %8s", "Language", "Files", "Lines")
    log.info("%s", "-" * 32)

    items = list(repo.language_stats.items())
    if top_languages and top_languages > 0:
        items = items[:top_languages]

    for lang, stats in items:
        log.info("%-15s %6d %8d", lang, stats["files"], stats["lines"])

    log.info("%s", "-" * 32)
    shown_lines = sum(stats["lines"] for _, stats in items)
    if top_languages and top_languages > 0 and len(items) < len(repo.language_stats):
        log.info("%-15s %6s %8d", "Shown", "", shown_lines)
        log.info("%-15s %6d %8d", "Total", len(repo.files), repo.total_lines)
    else:
        log.info("%-15s %6d %8d", "Total", len(repo.files), repo.total_lines)


def _run_generate(args: argparse.Namespace) -> int:
    include_content = not (args.index_only or args.list_only)
    repo, code = _scan_repo(args, include_content=include_content)
    if code != 0 or repo is None or not repo.files:
        return code

    if args.list_only:
        _print_repo_list(repo)
        return 0

    output_dir = args.output or f"./pixcode_output/{repo.name}"
    generator = PDFGenerator(
        repo,
        output_dir,
        enable_semantic_minimap=not args.disable_semantic_minimap,
        enable_lint_heatmap=not args.disable_lint_heatmap,
        linter_timeout=args.linter_timeout,
    )
    if args.index_only:
        generator.generate_index_only()
        log.info("")
        log.info("Done! Generated 1 PDF")
        return 0

    generator.generate_all()
    return 0


def _run_list(args: argparse.Namespace) -> int:
    repo, code = _scan_repo(args, include_content=False)
    if code != 0 or repo is None or not repo.files:
        return code
    _print_repo_list(repo, top_languages=args.top_languages)
    return 0


def _run_onepdf(args: argparse.Namespace) -> int:
    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        log.error("Error: '%s' is not a directory", args.repo)
        return 1

    out_pdf = args.output
    if not out_pdf:
        out_pdf = f"./pixcode_output/{repo_path.name}/ONEPDF_CORE.pdf"

    stats = pack_repo_to_one_pdf(
        repo_root=repo_path,
        out_pdf=Path(out_pdf),
        max_file_size=args.max_size * 1024,
        extra_ignore=args.ignore,
        core_only=not args.no_core_only,
        prefer_git=not args.no_git,
        include_patterns=args.include,
        max_cols=args.cols,
        wrap=not args.no_wrap,
        include_tree=not args.no_tree,
        include_index=not args.no_index,
    )
    log.info(
        "onepdf summary: seen=%d, included=%d, ignored=%d, size/empty=%d, binary=%d, errors=%d, pages=%d, output=%d bytes",
        stats.get("seen_files", 0),
        stats.get("included", 0),
        stats.get("ignored_by_pattern", 0),
        stats.get("skipped_size_or_empty", 0),
        stats.get("skipped_binary", 0),
        stats.get("skipped_unreadable", 0),
        stats.get("pages", 0),
        stats.get("output_bytes", 0),
    )
    log.info("PDF: %s", Path(out_pdf).resolve())
    return 0


def _run_help(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    commands: dict[str, argparse.ArgumentParser],
) -> int:
    if args.topic:
        commands[args.topic].print_help()
    else:
        parser.print_help()
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_args = sys.argv[1:] if argv is None else argv
    parser, commands = build_parser()

    # Better error for `pixcode some_word` where `some_word` isn't a path.
    known_commands = {"generate", "list", "help", "onepdf", "allinone"}
    if raw_args:
        first = raw_args[0]
        looks_like_path = (
            first.startswith(".")
            or ("/" in first)
            or ("\\" in first)
            or (":" in first)
        )
        if (first not in known_commands) and (not first.startswith("-")):
            if looks_like_path and not Path(first).exists():
                sys.stderr.write(f"Error: '{first}' does not exist.\n\n")
                parser.print_help()
                return 2
            if (not looks_like_path) and not Path(first).exists():
                sys.stderr.write(f"Error: unknown command or path '{first}'.\n\n")
                parser.print_help()
                return 2

    args = parser.parse_args(_normalize_legacy_args(raw_args))
    _configure_logging(getattr(args, "log_level", "INFO"))

    if args.command == "list":
        return _run_list(args)
    if args.command in {"onepdf", "allinone"}:
        return _run_onepdf(args)
    if args.command == "help":
        return _run_help(args, parser, commands)
    return _run_generate(args)


if __name__ == "__main__":
    sys.exit(main())
