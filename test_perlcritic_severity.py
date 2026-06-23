"""Юнит-тесты маппинга severity perlcritic → severity бота.

Запуск из папки pr_review_bot:  python -m unittest test_perlcritic_severity
"""

import unittest

from perlcritic_severity import to_bot_severity, ERROR, WARNING, SUGGESTION


class TestToBotSeverity(unittest.TestCase):
    def test_default_thresholds_full_scale(self):
        # Инвертированная шкала perlcritic: 5=критич … 1=мягкий. Дефолты error_min=4, warning_min=2.
        self.assertEqual(to_bot_severity(5), ERROR)
        self.assertEqual(to_bot_severity(4), ERROR)
        self.assertEqual(to_bot_severity(3), WARNING)
        self.assertEqual(to_bot_severity(2), WARNING)
        self.assertEqual(to_bot_severity(1), SUGGESTION)

    def test_pilot_severity_5_maps_to_error(self):
        # На пилоте PERLCRITIC_SEVERITY=5 → сервер вернёт только severity-5 → всё error.
        self.assertEqual(to_bot_severity(5), ERROR)

    def test_custom_thresholds(self):
        # Если опустить пороги — маппинг сдвигается.
        self.assertEqual(to_bot_severity(3, error_min=3, warning_min=1), ERROR)
        self.assertEqual(to_bot_severity(2, error_min=3, warning_min=1), WARNING)
        self.assertEqual(to_bot_severity(1, error_min=3, warning_min=1), WARNING)

    def test_string_number(self):
        self.assertEqual(to_bot_severity("5"), ERROR)

    def test_invalid_values_fall_to_suggestion(self):
        self.assertEqual(to_bot_severity(None), SUGGESTION)
        self.assertEqual(to_bot_severity("abc"), SUGGESTION)
        self.assertEqual(to_bot_severity(""), SUGGESTION)


if __name__ == "__main__":
    unittest.main()
