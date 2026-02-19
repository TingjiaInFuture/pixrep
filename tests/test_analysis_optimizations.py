import unittest
from pathlib import Path

from pixrep.analysis import CodeInsightEngine
from pixrep.models import RepoInfo


class TestAnalysisOptimizations(unittest.TestCase):
    def setUp(self):
        self.repo = RepoInfo(root=Path.cwd(), name="repo")
        self.engine = CodeInsightEngine(self.repo)

    def test_python_attribute_call_no_false_positive(self):
        content = "\n".join([
            "def get():",
            "    return 1",
            "",
            "def f():",
            "    requests.get('https://example.com')",
            "    return get()",
        ])
        semantic = self.engine._python_semantic_map(content)
        joined = "\n".join(semantic.lines)
        self.assertIn("f -> get", joined)

    def test_python_self_method_qualified_edge(self):
        content = "\n".join([
            "class A:",
            "    def m(self):",
            "        self.n()",
            "",
            "    def n(self):",
            "        return 1",
        ])
        semantic = self.engine._python_semantic_map(content)
        joined = "\n".join(semantic.lines)
        self.assertIn("A.m -> A.n", joined)

    def test_js_brace_balance_ignores_strings_and_comments(self):
        content = "\n".join([
            "function foo() {",
            "  const s = \"} {\";",
            "  // } in comment",
            "  /* } */",
            "  return bar();",
            "}",
            "function bar() {",
            "  return 1;",
            "}",
        ])
        semantic = self.engine._js_semantic_map(content)
        joined = "\n".join(semantic.lines)
        self.assertIn("foo -> bar", joined)


if __name__ == "__main__":
    unittest.main()
