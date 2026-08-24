"""
Тесты режима «полный файл как контекст ревью» (spec 011).

Проверяются чистые функции diff_filter: разметка файла, сверка номеров строк,
терпимое приведение номера строки из ответа модели. Оркестратор (pr_review_bot)
здесь не участвует — он тянет fastapi и сеть.

Запуск:  python3 test_full_file_context.py
"""

import unittest

from diff_filter import (
    added_lines_with_text,
    changed_lines_from_diff_text,
    render_file_with_diff_marks,
    to_line_number,
    verify_file_matches_diff,
)


class TestRenderFileWithDiffMarks(unittest.TestCase):
    """Формат разметки обязан совпадать с parse_bitbucket_diff — иначе тихий баг."""

    def test_added_line_has_single_space_then_plus(self):
        out = render_file_with_diff_marks("my $x = 1;", {1})
        self.assertEqual(out, "[L1] +my $x = 1;")

    def test_context_line_has_two_spaces(self):
        # Метка + пробел-маркер: `[L1]` + ` ` + ` ` + код. Ровно как у бота.
        out = render_file_with_diff_marks("my $x = 1;", set())
        self.assertEqual(out, "[L1]  my $x = 1;")

    def test_numbering_is_one_based_and_continuous(self):
        out = render_file_with_diff_marks("a\nb\nc", {2})
        self.assertEqual(out.split("\n"), ["[L1]  a", "[L2] +b", "[L3]  c"])

    def test_unchanged_line_starting_with_plus_is_not_seen_as_added(self):
        """Главная ловушка: в Perl строка кода вполне может начинаться с `+`.

        При разметке одним пробелом такая строка стала бы «добавленной» и для модели,
        и для changed_lines_from_diff_text — замечание прилипло бы к чужому коду.
        """
        rendered = render_file_with_diff_marks("+ $total;\nmy $y = 2;", {2})
        self.assertEqual(changed_lines_from_diff_text(rendered), {2})

    def test_empty_input(self):
        self.assertEqual(render_file_with_diff_marks("", {1}), "")
        self.assertEqual(render_file_with_diff_marks(None, {1}), "")


class TestVerifyFileMatchesDiff(unittest.TestCase):
    """Сверка «номер из диффа == та же строка в файле»."""

    def test_matching_file_and_diff(self):
        diff = "[L1]  use strict;\n[L2] +my $x = 1;"
        ok, why = verify_file_matches_diff("use strict;\nmy $x = 1;", diff)
        self.assertTrue(ok, why)
        self.assertEqual(why, "")

    def test_shift_by_one_is_detected(self):
        # Файл сдвинулся на строку (кто-то запушил между запросом диффа и загрузкой).
        diff = "[L2] +my $x = 1;"
        ok, why = verify_file_matches_diff("use strict;\n\nmy $x = 1;", diff)
        self.assertFalse(ok)
        self.assertIn("2", why)

    def test_line_beyond_end_of_file(self):
        ok, why = verify_file_matches_diff("one line", "[L7] +что-то")
        self.assertFalse(ok)
        self.assertIn("за пределами файла", why)

    def test_trailing_whitespace_is_not_a_mismatch(self):
        diff = "[L1] +my $x = 1;"
        ok, why = verify_file_matches_diff("my $x = 1;   ", diff)
        self.assertTrue(ok, why)

    def test_all_added_lines_are_checked_not_a_sample(self):
        # Первые строки совпадают, расходится только последняя — выборка бы это
        # пропустила, а в Perl полно одинаковых строк вроде `}` и `);`.
        file_text = "}\n}\n}\nmy $right = 1;"
        diff = "[L1] +}\n[L2] +}\n[L3] +}\n[L4] +my $wrong = 1;"
        ok, why = verify_file_matches_diff(file_text, diff)
        self.assertFalse(ok)
        self.assertIn("4", why)

    def test_empty_inputs_are_not_confirmed(self):
        self.assertFalse(verify_file_matches_diff("", "[L1] +x")[0])
        self.assertFalse(verify_file_matches_diff("x", "")[0])
        self.assertFalse(verify_file_matches_diff("x", "[L1]  только контекст")[0])


class TestAddedLinesWithText(unittest.TestCase):

    def test_extracts_number_and_code(self):
        diff = "[L3] +my $x = 1;\n[L4]  context\n       -removed"
        self.assertEqual(added_lines_with_text(diff), [(3, "my $x = 1;")])

    def test_empty(self):
        self.assertEqual(added_lines_with_text(None), [])


class TestToLineNumber(unittest.TestCase):
    """Модель возвращает в поле line что угодно — приводим терпимо."""

    def test_int_and_numeric_string(self):
        self.assertEqual(to_line_number(42), 42)
        self.assertEqual(to_line_number("42"), 42)

    def test_garbage_becomes_none(self):
        for bad in ("unknown", "42-45", None, "", [], {}):
            self.assertIsNone(to_line_number(bad), f"ожидался None для {bad!r}")

    def test_numeric_string_matches_changed_set(self):
        """Ради чего всё: "42" in {42} → False, и фильтр выбросил бы верное замечание."""
        changed = {42}
        self.assertIn(to_line_number("42"), changed)


if __name__ == "__main__":
    unittest.main()
