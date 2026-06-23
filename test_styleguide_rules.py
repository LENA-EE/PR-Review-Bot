"""Юнит-тесты детерминированного слоя стайлгайда (ARCHITECTURE.md §2.2).

Запуск из папки pr_review_bot:  python -m unittest test_styleguide_rules
"""

import os
import tempfile
import unittest

from styleguide_rules import (
    parse_rules,
    added_lines_from_diff_text,
    scan,
    load_rules_file,
)

# Образец f["text"] из parse_bitbucket_diff: ADDED помечены `[L<n>] +`,
# CONTEXT — `[L<n>] ` (без `+`).
SAMPLE_DIFF_TEXT = "\n".join([
    "[L10]  my $dbh = get_dal();",        # CONTEXT — НЕ должно проверяться
    "[L11] +my $dbh = DBI->connect($dsn);",  # ADDED — нарушает правило DBI
    "[L12] +print \"debug\\n\";",           # ADDED — нарушает правило print
    "[L13]  return $dbh;",                  # CONTEXT
])

RULES_TEXT = "\n".join([
    "# демо-правила",
    "error | DBI->(connect|prepare) | прямой вызов DBI запрещён — используй DAL-wrapper",
    "suggestion | ^\\s*print\\b | print в коде — используй корпоративный логгер",
    "",
    "   ",
    "# битые строки ниже — должны быть пропущены, не уронить парсинг",
    "нет разделителей вообще",
    "warning | [ | message с невалидным regex",
])


class TestParseRules(unittest.TestCase):
    def test_valid_rules_parsed(self):
        rules = parse_rules(RULES_TEXT)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].severity, "error")
        self.assertEqual(rules[1].severity, "suggestion")

    def test_regex_with_alternation_preserved(self):
        # `|` внутри regex (DBI->(connect|prepare)) не должен резать правило.
        rules = parse_rules(RULES_TEXT)
        self.assertTrue(rules[0].pattern.search("my $x = DBI->connect($dsn);"))
        self.assertTrue(rules[0].pattern.search("DBI->prepare($sql)"))

    def test_bad_lines_skipped(self):
        # 'нет разделителей' и невалидный regex '[' пропускаются, валидных ровно 2.
        self.assertEqual(len(parse_rules(RULES_TEXT)), 2)

    def test_invalid_severity_becomes_suggestion(self):
        rules = parse_rules("WAT | foo | bar")
        self.assertEqual(rules[0].severity, "suggestion")

    def test_empty_input(self):
        self.assertEqual(parse_rules(""), [])
        self.assertEqual(parse_rules(None), [])


class TestAddedLines(unittest.TestCase):
    def test_only_added_lines_with_content(self):
        added = added_lines_from_diff_text(SAMPLE_DIFF_TEXT)
        self.assertEqual([n for n, _ in added], [11, 12])
        self.assertIn("DBI->connect", dict(added)[11])

    def test_context_lines_excluded(self):
        nums = [n for n, _ in added_lines_from_diff_text(SAMPLE_DIFF_TEXT)]
        self.assertNotIn(10, nums)
        self.assertNotIn(13, nums)


class TestScan(unittest.TestCase):
    def setUp(self):
        self.rules = parse_rules(RULES_TEXT)

    def test_findings_only_on_added_lines(self):
        findings = scan(SAMPLE_DIFF_TEXT, self.rules)
        lines = sorted(f["line"] for f in findings)
        # L11 (DBI) и L12 (print) ловятся; контекстные L10/L13 — нет.
        self.assertEqual(lines, [11, 12])

    def test_finding_shape(self):
        findings = scan(SAMPLE_DIFF_TEXT, self.rules)
        f = next(f for f in findings if f["line"] == 11)
        self.assertEqual(set(f), {"line", "severity", "message"})
        self.assertEqual(f["severity"], "error")
        self.assertNotIn("snippet", f)  # сниппет не включаем (стабильность дедупа)

    def test_no_rules_no_findings(self):
        self.assertEqual(scan(SAMPLE_DIFF_TEXT, []), [])

    def test_empty_diff(self):
        self.assertEqual(scan("", self.rules), [])
        self.assertEqual(scan(None, self.rules), [])


class TestLoadRulesFile(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(load_rules_file("/no/such/rules/file.txt"), [])
        self.assertEqual(load_rules_file(""), [])

    def test_reads_and_parses(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("error | DBI->connect | no direct DBI")
            rules = load_rules_file(path)
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].severity, "error")
        finally:
            os.unlink(path)

    def test_cp1251_does_not_crash(self):
        # Файл в cp1251 (как реальный styleguide на VM) — читается, не падает.
        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write("warning | foo | кириллица в сообщении".encode("cp1251"))
            rules = load_rules_file(path)
            self.assertEqual(len(rules), 1)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
