# pixcode

pixcode converts code repositories into a layered PDF set so large language models
can scan a macro index first and then dive into per-file details on demand.

## Install (local)

```bash
pip install pixcode
```

## Usage

```bash
pixcode .                          # Current directory
pixcode /path/to/repo -o ./pdfs    # Specify output
pixcode . --max-size 1024          # Max file size 1MB
pixcode . --ignore "*.test.js"     # Extra ignore patterns
pixcode . --list-only              # Print tree + language stats
```

## Output

- `00_INDEX.pdf` for directory tree, stats, and file index
- One PDF per file, named `NNN_<path>.pdf`
