"""
Тесты поимённого отчёта о покрытии (spec 020): пометки о частично и вовсе не
проверенных файлах уезжают ИНЛАЙН в сами файлы, в сводке остаётся строка
счётчиков без перечня имён.

Проверяется чистая раскладка build_coverage_report и выбор якоря _coverage_anchor.
Постинг/дедуп/dry-run — это I/O в _do_review; здесь проверяется, что раскладка
даёт данные, на которых существующий механизм дедупа (`_comment_key`) стабилен.

Запуск:  python -m unittest test_coverage_named_files
"""

import unittest

from pr_review_bot import (
    COVERAGE_WARN_HUNKS,
    COVERAGE_WARN_NOT_REVIEWED,
    COVERAGE_WARN_TRUNCATED,
    _comment_key,
    _coverage_key,
    _named_list,
    _coverage_anchor,
    build_coverage_report,
)


def _diff(added_lines: list[int], context_lines: list[int] | None = None) -> str:
    """Фейковый f['text'] в формате parse_bitbucket_diff: `[L<n>] +` / `[L<n>] `."""
    context_lines = context_lines or []
    rows = []
    for n in sorted(set(added_lines) | set(context_lines)):
        marker = "+code" if n in added_lines else " ctx"
        rows.append(f"[L{n}] {marker}")
    return "\n".join(rows)


class TestCoverageAnchor(unittest.TestCase):
    """FR-001: якорь — первая ДОБАВЛЕННАЯ строка файла."""

    def test_first_added_line_is_chosen(self):
        text = _diff(added_lines=[7, 9], context_lines=[5])
        self.assertEqual(_coverage_anchor(text), 7)

    def test_context_line_is_not_an_anchor(self):
        # Только контекст, ни одной `+` строки → якорить некуда.
        text = _diff(added_lines=[], context_lines=[3, 4])
        self.assertIsNone(_coverage_anchor(text))

    def test_empty_text(self):
        self.assertIsNone(_coverage_anchor(""))
        self.assertIsNone(_coverage_anchor(None))


class TestInlinePerFile(unittest.TestCase):
    """FR-001: по одному инлайн-предупреждению на проблемный файл, с якорем."""

    def test_one_warning_per_file_anchored_to_first_added_line(self):
        text_by_path = {
            "A.pm": _diff([10, 12]),
            "B.pm": _diff([3]),
        }
        inline, _ = build_coverage_report(
            fenix_failed=["A.pm"],
            truncated_files=[("B.pm", 500)],
            hunks_fallback=[],
            text_by_path=text_by_path,
            reviewed=1,
            limit=10,
        )
        by_file = {w["file"]: w for w in inline}
        self.assertEqual(set(by_file), {"A.pm", "B.pm"})
        self.assertEqual(by_file["A.pm"]["line"], 10)
        self.assertEqual(by_file["B.pm"]["line"], 3)

    def test_file_in_two_categories_gets_single_warning_by_priority(self):
        # Файл и не проверен Фениксом, и обрезан — приоритет у «не проверен».
        text_by_path = {"A.pm": _diff([2])}
        inline, _ = build_coverage_report(
            fenix_failed=["A.pm"],
            truncated_files=[("A.pm", 900)],
            hunks_fallback=["A.pm"],
            text_by_path=text_by_path,
            reviewed=0,
            limit=10,
        )
        self.assertEqual(len(inline), 1)
        self.assertEqual(inline[0]["text"], COVERAGE_WARN_NOT_REVIEWED)


class TestThreeDistinctTexts(unittest.TestCase):
    """FR-002: три случая различимы; первые два — дыра, третий — меньше контекста."""

    def test_texts_are_pairwise_distinct(self):
        self.assertNotEqual(COVERAGE_WARN_NOT_REVIEWED, COVERAGE_WARN_TRUNCATED)
        self.assertNotEqual(COVERAGE_WARN_TRUNCATED, COVERAGE_WARN_HUNKS)
        self.assertNotEqual(COVERAGE_WARN_NOT_REVIEWED, COVERAGE_WARN_HUNKS)

    def test_category_maps_to_expected_text(self):
        text_by_path = {"N.pm": _diff([1]), "T.pm": _diff([1]), "H.pm": _diff([1])}
        inline, _ = build_coverage_report(
            fenix_failed=["N.pm"],
            truncated_files=[("T.pm", 999)],
            hunks_fallback=["H.pm"],
            text_by_path=text_by_path,
            reviewed=2,
            limit=10,
        )
        by_file = {w["file"]: w["text"] for w in inline}
        self.assertEqual(by_file["N.pm"], COVERAGE_WARN_NOT_REVIEWED)
        self.assertEqual(by_file["T.pm"], COVERAGE_WARN_TRUNCATED)
        self.assertEqual(by_file["H.pm"], COVERAGE_WARN_HUNKS)

    def test_тексты_трёх_категорий_различны(self):
        """Формулировки не проверяем дословно: они утверждаются владельцем.

        Тест на точную фразу ломался бы от редакторской правки, не найдя при
        этом ни одной ошибки в коде. Проверяем структурное свойство: три случая
        различимы и ни один не пустой.
        """
        тексты = [COVERAGE_WARN_NOT_REVIEWED, COVERAGE_WARN_TRUNCATED, COVERAGE_WARN_HUNKS]
        self.assertEqual(len(set(тексты)), 3)
        for текст in тексты:
            self.assertTrue(текст.strip())


class TestSummaryLine(unittest.TestCase):
    """FR-003 / FR-004: строка счётчиков без имён; пустая при полном покрытии."""

    def test_summary_absent_when_all_reviewed_fully(self):
        inline, summary = build_coverage_report(
            fenix_failed=[], truncated_files=[], hunks_fallback=[],
            text_by_path={}, reviewed=5, limit=10,
        )
        self.assertEqual(inline, [])
        self.assertEqual(summary, "")

    def test_counts_partial_is_truncated_or_hunks_and_full_is_remainder(self):
        text_by_path = {"N.pm": _diff([1]), "T.pm": _diff([1]), "H.pm": _diff([1])}
        # reviewed=4: T и H отревьюены частично, ещё двое — полностью. N не проверен.
        _, summary = build_coverage_report(
            fenix_failed=["N.pm"],
            truncated_files=[("T.pm", 999)],
            hunks_fallback=["H.pm"],
            text_by_path=text_by_path,
            reviewed=4,
            limit=10,
        )
        self.assertIn("полностью просмотрено 2", summary)
        self.assertIn("частично 2", summary)
        self.assertIn("не проверено вовсе 1", summary)

    def test_summary_has_no_file_names(self):
        text_by_path = {"secret_name.pm": _diff([1])}
        _, summary = build_coverage_report(
            fenix_failed=[], truncated_files=[("secret_name.pm", 999)],
            hunks_fallback=[], text_by_path=text_by_path, reviewed=1, limit=10,
        )
        # Имя ушло инлайн; в сводке (пока файл отмечен инлайн) имени быть не должно.
        self.assertNotIn("secret_name.pm", summary)


class TestLimit(unittest.TestCase):
    """FR-008: число инлайн-пометок ограничено, остаток — в сводке."""

    def test_inline_capped_and_remainder_reported(self):
        files = {f"f{i}.pm": _diff([1]) for i in range(15)}
        inline, summary = build_coverage_report(
            fenix_failed=list(files),
            truncated_files=[], hunks_fallback=[],
            text_by_path=files, reviewed=0, limit=10,
        )
        self.assertEqual(len(inline), 10)
        # Остаток не просто посчитан, а НАЗВАН: файл, не отмеченный инлайн и не
        # названный в сводке, не виден разработчику нигде.
        self.assertIn("превышен лимит пометок (10)", summary)
        self.assertIn("f10.pm", summary)
        self.assertIn("f14.pm", summary)


class TestNoAnchorGoesToSummary(unittest.TestCase):
    """FR-010: файл без добавленных строк не теряется — уходит в сводку поимённо."""

    def test_file_without_added_lines_named_in_summary(self):
        text_by_path = {"C.pm": _diff(added_lines=[], context_lines=[2, 3])}
        inline, summary = build_coverage_report(
            fenix_failed=["C.pm"],
            truncated_files=[], hunks_fallback=[],
            text_by_path=text_by_path, reviewed=0, limit=10,
        )
        self.assertEqual(inline, [])
        self.assertIn("C.pm", summary)
        self.assertIn("поставить некуда", summary)


class TestDedupKeyStable(unittest.TestCase):
    """FR-009: пометка проходит существующую дедупликацию `_comment_key`.

    Два одинаковых прогона дают один и тот же ключ → повторный pr:modified не
    создаст дубль (в _do_review ключ ищется в множестве уже висящих комментариев).
    """

    def test_ключ_не_зависит_от_смещения_якоря(self):
        """Регрессия: автор дописал строки выше — якорь уехал, пометка та же.

        _comment_key включает номер строки, поэтому по нему сместившаяся пометка
        выглядела бы новой и бот вешал бы копию на каждый push. Текст пометки —
        константа, файл тот же, значит это дубль, и ключ обязан совпасть.
        """
        первый = build_coverage_report(
            fenix_failed=[], truncated_files=[], hunks_fallback=["A.pm"],
            text_by_path={"A.pm": _diff([10])}, reviewed=1, limit=10,
        )[0][0]
        # Тот же файл после коммита, добавившего пять строк выше первого ханка.
        второй = build_coverage_report(
            fenix_failed=[], truncated_files=[], hunks_fallback=["A.pm"],
            text_by_path={"A.pm": _diff([15])}, reviewed=1, limit=10,
        )[0][0]

        self.assertNotEqual(первый["line"], второй["line"])
        self.assertEqual(
            _coverage_key(первый["file"], первый["text"]),
            _coverage_key(второй["file"], второй["text"]),
        )
        # Контроль: старый ключ со строкой этот случай не ловил.
        self.assertNotEqual(
            _comment_key(первый["file"], первый["line"], первый["text"]),
            _comment_key(второй["file"], второй["line"], второй["text"]),
        )

    def test_разные_файлы_дают_разные_ключи(self):
        self.assertNotEqual(
            _coverage_key("A.pm", COVERAGE_WARN_HUNKS),
            _coverage_key("B.pm", COVERAGE_WARN_HUNKS),
        )

    def test_same_warning_yields_same_key(self):
        text_by_path = {"A.pm": _diff([4])}
        args = dict(
            fenix_failed=["A.pm"], truncated_files=[], hunks_fallback=[],
            text_by_path=text_by_path, reviewed=0, limit=10,
        )
        first = build_coverage_report(**args)[0][0]
        second = build_coverage_report(**args)[0][0]
        key1 = _comment_key(first["file"], first["line"], first["text"])
        key2 = _comment_key(second["file"], second["line"], second["text"])
        self.assertEqual(key1, key2)


class TestNamedList(unittest.TestCase):
    """FR-008/FR-010: файлы без инлайн-пометки названы поимённо, но с потолком."""

    def test_короткий_список_перечислен_целиком(self):
        self.assertEqual(_named_list(["A.pm", "B.pm"], 10), "A.pm, B.pm")

    def test_длинный_список_обрезан_со_счётчиком(self):
        listed = _named_list([f"F{i}.pm" for i in range(25)], 10)
        self.assertIn("F0.pm", listed)
        self.assertIn("и ещё 15", listed)
        self.assertNotIn("F10.pm", listed)

    def test_остаток_сверх_лимита_назван_поимённо(self):
        """Регрессия: раньше в сводке было только число, и эти файлы не были
        названы нигде, кроме лога — дыра в информировании, сдвинутая за лимит."""
        paths = [f"F{i}.pm" for i in range(12)]
        _inline, summary = build_coverage_report(
            fenix_failed=paths, truncated_files=[], hunks_fallback=[],
            text_by_path={p: _diff([1]) for p in paths}, reviewed=0, limit=10,
        )
        self.assertIn("F10.pm", summary)
        self.assertIn("F11.pm", summary)


if __name__ == "__main__":
    unittest.main()
