import unittest
from pathlib import Path
import shutil
import uuid

from pixcode.scanner import RepoScanner
from pixcode.onepdf_pack import pack_repo_to_one_pdf


class TestScannerAndOnepdf(unittest.TestCase):
    def test_scanner_skips_binary_and_size(self):
        tmp_root = Path(__file__).resolve().parents[1] / ".test_scratch"
        tmp_root.mkdir(parents=True, exist_ok=True)
        root = tmp_root / f"repo_{uuid.uuid4().hex}"
        try:
            root.mkdir()
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("print('hi')\n", encoding="utf-8")
            (root / "src" / "bin.dat").write_bytes(b"abc\x00def")
            (root / "big.txt").write_text("x" * 5000, encoding="utf-8")

            scanner = RepoScanner(str(root), max_file_size=1024)  # 1KB
            repo = scanner.scan(include_content=True)

            paths = {str(f.path).replace("\\", "/") for f in repo.files}
            self.assertIn("src/a.py", paths)
            self.assertNotIn("src/bin.dat", paths)
            self.assertNotIn("big.txt", paths)
            self.assertEqual(repo.language_stats.get("python", {}).get("files"), 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_onepdf_pack_writes_pdf(self):
        tmp_root = Path(__file__).resolve().parents[1] / ".test_scratch"
        tmp_root.mkdir(parents=True, exist_ok=True)
        root = tmp_root / f"repo_{uuid.uuid4().hex}"
        try:
            root.mkdir()
            (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
            (root / "b.js").write_text("function g(){ return f(); }\n", encoding="utf-8")

            out_pdf = tmp_root / f"out_{uuid.uuid4().hex}.pdf"
            stats = pack_repo_to_one_pdf(
                repo_root=root,
                out_pdf=out_pdf,
                prefer_git=False,
                core_only=False,
                include_tree=True,
                include_index=True,
            )
            self.assertTrue(out_pdf.exists())
            self.assertGreater(out_pdf.stat().st_size, 20)
            self.assertGreaterEqual(stats.get("pages", 0), 1)
            blob = out_pdf.read_bytes()
            self.assertTrue(blob.startswith(b"%PDF-"))
        finally:
            shutil.rmtree(root, ignore_errors=True)
            try:
                out_pdf.unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
