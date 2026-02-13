import unittest

from pixcode.flowables import CodeBlockChunk


class _DummyFonts:
    # CodeBlockChunk only needs these during drawing; tests only touch mask logic.
    mono = "Courier"
    mono_bold = "Courier-Bold"
    bold = "Helvetica-Bold"
    normal = "Helvetica"


class TestJsMultilineMask(unittest.TestCase):
    def test_backtick_multiline_mask_toggles(self):
        lines = [
            "const s = `hello",
            "world`;",
            "const x = 1;",
        ]
        chunk = CodeBlockChunk(lines=lines, language="javascript", fonts=_DummyFonts())
        self.assertEqual(chunk._ml_string_mask, [True, True, False])

    def test_escaped_backtick_does_not_toggle(self):
        lines = [
            r"const s = \`not a template\`;",
            "const x = 1;",
        ]
        chunk = CodeBlockChunk(lines=lines, language="typescript", fonts=_DummyFonts())
        self.assertEqual(chunk._ml_string_mask, [False, False])


if __name__ == "__main__":
    unittest.main()

