# pixcode

<div align="center">

# 📉 SAVE UP TO 90% TOKENS

### Turn Codebases into **Visual Context** for Multimodal LLMs
*According to **DeepSeek-OCR** research and local benchmarking, visual encoding (PDF) outperforms plain-text ingestion for massive repositories.*

[![PyPI version](https://img.shields.io/pypi/v/pixcode?color=blue)](https://pypi.org/project/pixcode/)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>

---

## 📖 Introduction

**pixcode** is a developer tool designed to bridge the gap between large code repositories and Multimodal Large Language Models.

Instead of feeding raw text that consumes massive context windows, **pixcode** converts your repository into a **structured, hierarchical set of PDFs**. This allows you to:

*   **Save 90% Tokens:** Visual encoding is far more efficient than text tokenization.
*   **Test for Free:** Easily share your entire codebase with premium models (like **Claude Opus 4.6**) on platforms like **arena.ai** without hitting text limits.


## 🚀 Why Visual Code? (The 90% Claim)

Traditional RAG (Retrieval-Augmented Generation) relies on raw text. However, recent research (including the **DeepSeek-OCR** paper) indicates that visual encoders can represent dense information more efficiently than textual tokenizers.

*   **Text Tokenization:** 1 page of dense code ≈ 500-800 text tokens.
*   **Visual Tokenization:** 1 page of code (PDF image) ≈ Fixed patch count (e.g., 85-256 tokens depending on the model).

**pixcode** creates a layered PDF structure:
1.  **Macro View (`00_INDEX.pdf`):** A visual map of the directory tree and project statistics.
2.  **Micro View (File PDFs):** Syntax-highlighted, line-numbered renderings of individual code files.

This approach enables an Agentic workflow: *Read the Index -> Identify relevant files -> Ingest only specific PDFs.*

## ✨ Features

*   **📉 High Efficiency:** Drastically reduces context window usage for large repos.
*   **🎨 Syntax Highlighting:** Supports 50+ languages (Python, JS, Rust, Go, C++, etc.) with a "One Dark" inspired theme.
*   **🗂️ Hierarchical Output:** Generates a clean `00_INDEX.pdf` summary and separate files for granular access.
*   **🌏 CJK Support:** Built-in font fallback for Chinese/Japanese/Korean characters (Auto-detects OS fonts).
*   **🛡️ Smart Filtering:** Respects `.gitignore` patterns and supports custom ignore rules.
*   **📊 Insightful Stats:** Calculates line counts and language distribution automatically.

## 📦 Installation

```bash
pip install pixcode
```

## 🛠️ Usage

### Quick Start
Convert the current directory to PDFs in the default output folder (`./pixcode_output/<repo_name>`):

```bash
pixcode .
```

### Common Commands

**Generate PDFs for a specific repo:**
```bash
pixcode generate /path/to/my-project -o ./my-project-pdfs
```

**Preview structure and stats (without generating PDFs):**
```bash
pixcode list /path/to/my-project
```

**Show only top 5 languages in the summary:**
```bash
pixcode list . --top-languages 5
```

### CLI Reference

| Argument | Description | Default |
| :--- | :--- | :--- |
| `repo` | Path to the code repository. | `.` (Current Dir) |
| `-o`, `--output` | Directory to save the generated PDFs. | `./pixcode_output/<repo>` |
| `--max-size` | Max file size to process (in KB). Files larger than this are skipped. | `512` KB |
| `--ignore` | Additional glob patterns to ignore (e.g., `*.json` `test/*`). | `[]` |
| `--index-only` | Generate only the `00_INDEX.pdf` (Directory tree & stats). | `False` |
| `--list-only` | Print the directory tree and stats to console, then exit. | `False` |
| `-V`, `--version` | Show version information. | - |

## 📂 Output Structure

After running `pixcode .`, you will get a folder structure optimized for LLM upload:

```text
pixcode_output/pixcode/
├── 00_INDEX.pdf             # <--- Upload this first! Contains tree & stats
├── 001_LICENSE.pdf
├── 002_README.md.pdf
├── 003_pixcode___init__.py.pdf
├── 005_pixcode_cli.py.pdf
└── ...
```

## 🧩 Supported Languages
Pixcode automatically detects and highlights syntax for:
*   **Core:** Python, C, C++, Java, Rust, Go
*   **Web:** HTML, CSS, JavaScript, TypeScript, Vue, Svelte
*   **Config:** JSON, YAML, TOML, XML, Dockerfile, Ini
*   **Scripting:** Bash, Lua, Perl, Ruby, PHP
*   **And more:** Swift, Kotlin, Scala, Haskell, OCaml, etc.

## 🤝 Contributing

We welcome contributions! Please feel free to submit a Pull Request.

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/AmazingFeature`).
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4.  Push to the branch (`git push origin feature/AmazingFeature`).
5.  Open a Pull Request.

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.

