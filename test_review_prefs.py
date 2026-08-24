"""Тесты разбора метки @jarvis (spec 016, AC-010)."""

import unittest

import review_prefs as rp


class TestParseMarker(unittest.TestCase):
    """Разбор описания PR."""

    def test_no_description(self):
        for value in (None, "", "   ", 42, {"text": "@jarvis all"}):
            prefs = rp.parse_marker(value)
            self.assertFalse(prefs.from_marker, repr(value))
            self.assertEqual(prefs.min_severity, "warning")
            self.assertIsNone(prefs.unknown_command)

    def test_description_without_marker(self):
        prefs = rp.parse_marker("Обычное описание PR.\nПочинил валидацию логина.")
        self.assertFalse(prefs.from_marker)
        self.assertEqual(prefs.min_severity, "warning")

    def test_all_levels(self):
        cases = {"errors": "error", "warnings": "warning", "all": "suggestion"}
        for word, expected in cases.items():
            prefs = rp.parse_marker(f"@jarvis {word}")
            self.assertTrue(prefs.from_marker, word)
            self.assertEqual(prefs.min_severity, expected, word)
            self.assertEqual(prefs.level_word, word)
            self.assertIsNone(prefs.unknown_command, word)

    def test_case_insensitive(self):
        prefs = rp.parse_marker("@JARVIS ALL")
        self.assertEqual(prefs.min_severity, "suggestion")
        self.assertEqual(prefs.level_word, "all")

    def test_marker_must_start_the_line(self):
        """Упоминание внутри строки — не команда, а обсуждение (FR-001)."""
        text = "Я написал @jarvis all, но ничего не поменялось"
        prefs = rp.parse_marker(text)
        self.assertFalse(prefs.from_marker)
        self.assertEqual(prefs.min_severity, "warning")

    def test_marker_on_later_line(self):
        text = "Правка логина.\n\n@jarvis all\n\nСсылка на задачу: ABC-1."
        prefs = rp.parse_marker(text)
        self.assertTrue(prefs.from_marker)
        self.assertEqual(prefs.min_severity, "suggestion")

    def test_leading_whitespace_allowed(self):
        prefs = rp.parse_marker("   @jarvis errors")
        self.assertTrue(prefs.from_marker)
        self.assertEqual(prefs.min_severity, "error")

    def test_trailing_free_text_ignored(self):
        """FR-005: лишний текст после команды не мешает."""
        for text in ("@jarvis all, пожалуйста", "@jarvis all — хочу видеть всё"):
            prefs = rp.parse_marker(text)
            self.assertEqual(prefs.min_severity, "suggestion", text)
            self.assertIsNone(prefs.unknown_command, text)

    def test_single_exclusion(self):
        prefs = rp.parse_marker("@jarvis all -codestyle")
        self.assertEqual(prefs.min_severity, "suggestion")
        self.assertEqual(prefs.excluded_sources, frozenset({"codestyle"}))

    def test_several_exclusions(self):
        prefs = rp.parse_marker("@jarvis warnings -perlcritic -codestyle")
        self.assertEqual(prefs.min_severity, "warning")
        self.assertEqual(prefs.excluded_sources, frozenset({"perlcritic", "codestyle"}))

    def test_exclusion_without_level(self):
        """Уровень необязателен — остаётся дефолтный."""
        prefs = rp.parse_marker("@jarvis -codestyle", default_min_severity="error")
        self.assertTrue(prefs.from_marker)
        self.assertEqual(prefs.min_severity, "error")
        self.assertEqual(prefs.excluded_sources, frozenset({"codestyle"}))
        self.assertIsNone(prefs.unknown_command)

    def test_crlf_description(self):
        """Bitbucket отдаёт описание с \r\n — возврат каретки не должен
        прилипать к последнему токену и превращать его в непонятную команду."""
        prefs = rp.parse_marker("текст\r\n@jarvis all -codestyle\r\nещё")
        self.assertEqual(prefs.min_severity, "suggestion")
        self.assertEqual(prefs.excluded_sources, frozenset({"codestyle"}))
        self.assertIsNone(prefs.unknown_command)

    def test_unicode_dashes_accepted(self):
        """Автозамена в Word/Confluence превращает дефис в тире — понимаем и его."""
        for dash in ("-", "–", "—", "−"):
            prefs = rp.parse_marker(f"@jarvis all {dash}codestyle")
            self.assertEqual(prefs.excluded_sources, frozenset({"codestyle"}), dash)
            self.assertIsNone(prefs.unknown_command, dash)

    def test_standalone_dash_is_punctuation(self):
        """Тире без имени после него — знак препинания, а не сломанная команда."""
        prefs = rp.parse_marker("@jarvis all — хочу видеть всё")
        self.assertEqual(prefs.min_severity, "suggestion")
        self.assertIsNone(prefs.unknown_command)

    def test_two_levels_first_wins(self):
        prefs = rp.parse_marker("@jarvis errors all")
        self.assertEqual(prefs.min_severity, "error")

    def test_duplicate_exclusion_idempotent(self):
        prefs = rp.parse_marker("@jarvis all -codestyle -codestyle")
        self.assertEqual(prefs.excluded_sources, frozenset({"codestyle"}))

    def test_marker_as_prefix_not_matched(self):
        self.assertFalse(rp.parse_marker("@jarvisX all").from_marker)

    def test_extra_whitespace(self):
        for text in ("@jarvis  all", "	@jarvis all"):
            self.assertEqual(rp.parse_marker(text).min_severity, "suggestion", text)

    def test_non_string_service_args_do_not_raise(self):
        """Докстрока обещает, что функция не бросает — проверяем и служебные аргументы."""
        self.assertEqual(rp.parse_marker("@jarvis all", 123).min_severity, "suggestion")
        self.assertEqual(rp.parse_marker("@jarvis all", marker=None).min_severity, "suggestion")

    def test_unknown_command_falls_back(self):
        """FR-018: непонятное слово не проглатывается молча."""
        prefs = rp.parse_marker("@jarvis всё")
        self.assertTrue(prefs.from_marker)
        self.assertEqual(prefs.min_severity, "warning")
        self.assertEqual(prefs.unknown_command, "всё")

    def test_unknown_source_reported(self):
        prefs = rp.parse_marker("@jarvis all -стиль")
        self.assertEqual(prefs.min_severity, "suggestion")
        self.assertEqual(prefs.excluded_sources, frozenset())
        self.assertEqual(prefs.unknown_command, "-стиль")

    def test_only_first_unknown_reported(self):
        prefs = rp.parse_marker("@jarvis везде совсем")
        self.assertEqual(prefs.unknown_command, "везде")

    def test_bare_marker(self):
        prefs = rp.parse_marker("@jarvis")
        self.assertTrue(prefs.from_marker)
        self.assertEqual(prefs.min_severity, "warning")
        self.assertIsNone(prefs.unknown_command)

    def test_first_marker_wins(self):
        prefs = rp.parse_marker("@jarvis errors\n@jarvis all")
        self.assertEqual(prefs.min_severity, "error")

    def test_custom_marker(self):
        prefs = rp.parse_marker("@reviewer all", marker="@reviewer")
        self.assertTrue(prefs.from_marker)
        self.assertEqual(prefs.min_severity, "suggestion")

    def test_default_from_env_respected(self):
        prefs = rp.parse_marker(None, default_min_severity="suggestion")
        self.assertEqual(prefs.min_severity, "suggestion")
        self.assertEqual(prefs.level_word, "all")

    def test_broken_default_falls_back(self):
        prefs = rp.parse_marker(None, default_min_severity="мусор")
        self.assertEqual(prefs.min_severity, "warning")


class TestShouldPost(unittest.TestCase):
    """Решение по одному замечанию."""

    def test_threshold(self):
        prefs = rp.parse_marker("@jarvis warnings")
        self.assertTrue(rp.should_post(prefs, "error", "JARVIS"))
        self.assertTrue(rp.should_post(prefs, "warning", "JARVIS"))
        self.assertFalse(rp.should_post(prefs, "suggestion", "JARVIS"))

    def test_errors_only(self):
        prefs = rp.parse_marker("@jarvis errors")
        self.assertTrue(rp.should_post(prefs, "error", "JARVIS"))
        self.assertFalse(rp.should_post(prefs, "warning", "JARVIS"))
        self.assertFalse(rp.should_post(prefs, "suggestion", "JARVIS"))

    def test_all(self):
        prefs = rp.parse_marker("@jarvis all")
        for sev in ("error", "warning", "suggestion"):
            self.assertTrue(rp.should_post(prefs, sev, "JARVIS"), sev)

    def test_excluded_source(self):
        prefs = rp.parse_marker("@jarvis all -codestyle")
        self.assertFalse(rp.should_post(prefs, "suggestion", "codestyle"))
        self.assertFalse(rp.should_post(prefs, "warning", "codestyle"))
        self.assertTrue(rp.should_post(prefs, "suggestion", "perlcritic"))

    def test_error_beats_exclusion(self):
        """FR-012: минус источника не глушит ошибки этого источника."""
        prefs = rp.parse_marker("@jarvis all -codestyle")
        self.assertTrue(rp.should_post(prefs, "error", "codestyle"))

    def test_error_beats_threshold(self):
        prefs = rp.parse_marker("@jarvis errors -perlcritic -codestyle -jarvis")
        for source in ("perlcritic", "codestyle", "JARVIS"):
            self.assertTrue(rp.should_post(prefs, "error", source), source)

    def test_impact_never_filtered(self):
        """FR-013: факты графа вызовов публикуются при любых настройках."""
        prefs = rp.parse_marker("@jarvis errors")
        self.assertTrue(rp.should_post(prefs, "warning", "impact"))
        self.assertTrue(rp.should_post(prefs, "suggestion", "impact"))

    def test_source_case_insensitive(self):
        """В боте источник модели пишется как 'JARVIS', метка — строчными."""
        prefs = rp.parse_marker("@jarvis all -jarvis")
        self.assertFalse(rp.should_post(prefs, "suggestion", "JARVIS"))

    def test_unknown_severity_published(self):
        """Fail-open: неизвестная важность скорее 'важнее обычного', чем мелочь."""
        prefs = rp.parse_marker("@jarvis errors")
        self.assertTrue(rp.should_post(prefs, "critical", "JARVIS"))
        self.assertTrue(rp.should_post(prefs, None, "JARVIS"))


class TestDescribe(unittest.TestCase):
    """Строка эха для сводки."""

    def test_echo_names_included_levels(self):
        prefs = rp.parse_marker("@jarvis warnings")
        line = rp.describe(prefs, hidden_by_level=14)
        self.assertIn("warnings", line)
        self.assertIn("error + warning", line)
        self.assertIn("скрыто 14", line)
        self.assertIn("@jarvis all", line)

    def test_echo_when_nothing_hidden(self):
        """FR-017: строка присутствует всегда."""
        prefs = rp.parse_marker("@jarvis all")
        line = rp.describe(prefs)
        self.assertIn("скрытых замечаний нет", line)
        self.assertNotIn("Показать всё", line)

    def test_echo_lists_exclusions_sorted(self):
        prefs = rp.parse_marker("@jarvis all -perlcritic -codestyle")
        line = rp.describe(prefs)
        self.assertIn("исключено: codestyle, perlcritic", line)

    def test_echo_reports_unknown_command(self):
        prefs = rp.parse_marker("@jarvis всё")
        line = rp.describe(prefs)
        self.assertIn("«всё»", line)
        self.assertIn("не понял", line)

    def test_echo_is_stable(self):
        """Текст входит в ключ дедупликации сводки — не должен плавать."""
        prefs = rp.parse_marker("@jarvis all -codestyle -perlcritic")
        first = rp.describe(prefs, 3, 4)
        for _ in range(5):
            self.assertEqual(rp.describe(rp.parse_marker(
                "@jarvis all -perlcritic -codestyle"), 3, 4), first)

    def test_hidden_counts_are_summed(self):
        prefs = rp.parse_marker("@jarvis warnings -codestyle")
        self.assertIn("скрыто 7", rp.describe(prefs, hidden_by_level=5, hidden_by_source=2))


if __name__ == "__main__":
    unittest.main()
