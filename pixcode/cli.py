import argparse
import sys
from pathlib import Path

from .pdf_generator import PDFGenerator
from .scanner import RepoScanner
from .version import __version__


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
        choices=["generate", "list"],
        help="Command name / 命令名",
    )

    commands = {
        "generate": generate_parser,
        "list": list_parser,
        "help": help_parser,
    }
    return parser, commands


def _normalize_legacy_args(argv: list[str]) -> list[str]:
    if not argv:
        return ["generate"]

    known_commands = {"generate", "list", "help"}
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


def _scan_repo(args: argparse.Namespace, include_content: bool = True):
    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        print(f"Error: '{args.repo}' is not a directory")
        return None, 1

    print(f"Scanning {repo_path}...")
    scanner = RepoScanner(
        str(repo_path),
        max_file_size=args.max_size * 1024,
        extra_ignore=args.ignore,
    )
    repo = scanner.scan(include_content=include_content)
    if not repo.files:
        print("No files found.")
        return repo, 0
    stats = repo.scan_stats
    print(
        "Scan summary: "
        f"seen={stats.get('seen_files', 0)}, "
        f"loaded={len(repo.files)}, "
        f"ignored={stats.get('ignored_by_pattern', 0)}, "
        f"size/empty={stats.get('skipped_size_or_empty', 0)}, "
        f"binary={stats.get('skipped_binary', 0)}, "
        f"errors={stats.get('skipped_unreadable', 0)}"
    )
    return repo, 0


def _print_repo_list(repo, top_languages: int = 0):
    print(f"\n{repo.name} ({len(repo.files)} files)\n")
    print(repo.tree_str)
    print(f"\n{'Language':<15} {'Files':>6} {'Lines':>8}")
    print("-" * 32)

    items = list(repo.language_stats.items())
    if top_languages and top_languages > 0:
        items = items[:top_languages]

    for lang, stats in items:
        print(f"{lang:<15} {stats['files']:>6} {stats['lines']:>8}")

    print("-" * 32)
    shown_lines = sum(stats["lines"] for _, stats in items)
    if top_languages and top_languages > 0 and len(items) < len(repo.language_stats):
        print(f"{'Shown':<15} {'':>6} {shown_lines:>8}")
        print(f"{'Total':<15} {len(repo.files):>6} {repo.total_lines:>8}")
    else:
        print(f"{'Total':<15} {len(repo.files):>6} {repo.total_lines:>8}")


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
        generator._generate_index_pdf()  # pylint: disable=protected-access
        print("\nDone! Generated 1 PDF")
        return 0

    generator.generate_all()
    return 0


def _run_list(args: argparse.Namespace) -> int:
    repo, code = _scan_repo(args, include_content=False)
    if code != 0 or repo is None or not repo.files:
        return code
    _print_repo_list(repo, top_languages=args.top_languages)
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
    args = parser.parse_args(_normalize_legacy_args(raw_args))

    if args.command == "list":
        return _run_list(args)
    if args.command == "help":
        return _run_help(args, parser, commands)
    return _run_generate(args)


if __name__ == "__main__":
    sys.exit(main())
