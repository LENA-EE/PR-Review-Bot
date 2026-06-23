"""Юнит-тесты diff-фильтрации (ARCHITECTURE.md §2.9, BLOCKER-фикс B1).

Запуск из папки pr_review_bot:  python -m unittest test_diff_filter
"""

import unittest

from diff_filter import changed_lines_from_diff_text, filter_issues_by_lines


# Образец f["text"] из parse_bitbucket_diff: ADDED помечены `[L<n>] +`,
# CONTEXT — `[L<n>] ` (без `+`), REMOVED — без метки (с `-`).
SAMPLE_DIFF_TEXT = "\n".join([
    "[L10]  my $x = 1;",          # CONTEXT (есть метка, но НЕТ '+')
    "[L11] +my $y = 2;",          # ADDED
    "       -my $old = 0;",       # REMOVED (без метки)
    "[L12] +my $z = 3;",          # ADDED
    "[L13]  return $x;",          # CONTEXT
])


class TestChangedLines(unittest.TestCase):
    def test_only_added_lines(self):
        self.assertEqual(changed_lines_from_diff_text(SAMPLE_DIFF_TEXT), {11, 12})

    def test_context_lines_excluded_B1(self):
        # КЛЮЧЕВОЙ негативный тест B1: контекстные строки 10 и 13 имеют метку [L<n>],
        # но НЕ должны попасть в множество тронутых — иначе чужой код прилипнет к автору.
        changed = changed_lines_from_diff_text(SAMPLE_DIFF_TEXT)
        self.assertNotIn(10, changed)
        self.assertNotIn(13, changed)

    def test_empty(self):
        self.assertEqual(changed_lines_from_diff_text(""), set())
        self.assertEqual(changed_lines_from_diff_text(None), set())


class TestFilterIssues(unittest.TestCase):
    def setUp(self):
        self.changed = {11, 12}
        self.issues = [
            {"line": 11, "policy": "A"},   # на тронутой строке → on_diff
            {"line": 12, "policy": "B"},   # на тронутой строке → on_diff
            {"line": 10, "policy": "C"},   # чужая строка        → pre_existing
            {"line": 99, "policy": "D"},   # вне diff            → pre_existing
        ]

    def test_split_on_diff_vs_preexisting(self):
        on_diff, pre = filter_issues_by_lines(self.issues, self.changed)
        self.assertEqual([i["policy"] for i in on_diff], ["A", "B"])
        self.assertEqual([i["policy"] for i in pre], ["C", "D"])

    def test_bad_line_goes_to_preexisting(self):
        # Непривязываемое нарушение НЕ вешаем на автора.
        on_diff, pre = filter_issues_by_lines(
            [{"line": None, "policy": "X"}, {"line": "oops", "policy": "Y"}],
            self.changed,
        )
        self.assertEqual(on_diff, [])
        self.assertEqual(len(pre), 2)

    def test_empty_issues(self):
        on_diff, pre = filter_issues_by_lines([], self.changed)
        self.assertEqual((on_diff, pre), ([], []))


if __name__ == "__main__":
    unittest.main()
