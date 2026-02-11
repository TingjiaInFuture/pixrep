import argparse
import sys
from pathlib import Path

from .scanner import RepoScanner
from .version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pixcode",
        description="Convert code repository to structured PDFs for LLM collaboration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pixcode .                          # Current directory
  pixcode /path/to/repo -o ./pdfs    # Specify output
  pixcode . --max-size 1024          # Max file size 1MB
  pixcode . --ignore "*.test.js"     # Extra ignore patterns
        """,
    )
    parser.add_argument("repo", nargs="?", default=".",
                        help="Path to code repository (default: .)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output directory")
    parser.add_argument("--max-size", type=int, default=512,
                        help="Max file size in KB (default: 512)")
    parser.add_argument("--ignore", nargs="*", default=[],
                        help="Extra file patterns to ignore")
    parser.add_argument("--list-only", action="store_true",
                        help="Only list files, don't generate PDFs")
    parser.add_argument("-V", "--version", action="version",
                        version=f"pixcode {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        print(f"❌ Error: '{args.repo}' is not a directory")
        return 1

    print(f"🔍 Scanning {repo_path}...")
    scanner = RepoScanner(str(repo_path),
                          max_file_size=args.max_size * 1024,
                          extra_ignore=args.ignore)
    repo = scanner.scan()

    if not repo.files:
        print("⚠️  No files found!")
        return 0

    if args.list_only:
        print(f"\n📦 {repo.name} ({len(repo.files)} files)\n")
        print(repo.tree_str)
        print(f"\n{'Language':<15} {'Files':>6} {'Lines':>8}")
        print("─" * 32)
        for lang, stats in repo.language_stats.items():
            print(f"{lang:<15} {stats['files']:>6} {stats['lines']:>8}")
        print("─" * 32)
        print(f"{'Total':<15} {len(repo.files):>6} {repo.total_lines:>8}")
        return 0

    output_dir = args.output or f"./pixcode_output/{repo.name}"
    from .pdf_generator import PDFGenerator
    generator = PDFGenerator(repo, output_dir)
    generator.generate_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
