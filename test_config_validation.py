"""Тесты валидации конфига и пути отчёта dry-run (spec 017).

Закрывают находки SAST: путь файла отчёта не должен собираться из входных
данных, а URL из ENV не должны уходить в requests непроверенными.

Запуск из папки pr_review_bot:  python -m unittest test_config_validation
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest

import pr_review_bot as bot


class CheckUrlTest(unittest.TestCase):
    """FR-005: валидация URL из переменных окружения."""

    def test_принимает_http_и_https(self):
        for value in ("https://host", "http://host:8000", "http://host/path"):
            with self.subTest(value=value):
                self.assertIsNone(bot._check_url("X", value))

    def test_отвергает_чужие_схемы(self):
        for value in ("file:///etc/passwd", "ftp://host", "gopher://host"):
            with self.subTest(value=value):
                self.assertIsNotNone(bot._check_url("X", value))

    def test_отвергает_мусор_и_пустой_хост(self):
        for value in ("не-url", "https://", "/rest/api/1.0"):
            with self.subTest(value=value):
                self.assertIsNotNone(bot._check_url("X", value))

    def test_пустое_значение_зависит_от_required(self):
        self.assertIsNotNone(bot._check_url("X", "", required=True))
        self.assertIsNone(bot._check_url("X", "", required=False))

    def test_сообщение_называет_переменную(self):
        problem = bot._check_url("BITBUCKET_URL", "file:///etc/passwd")
        self.assertIn("BITBUCKET_URL", problem)


class DryRunPathTest(unittest.TestCase):
    """FR-001, FR-002: путь отчёта и номер PR внутри записи."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = (bot.DRY_RUN, bot.DRY_RUN_DIR, bot._DRY_RUN_FILE)
        bot.DRY_RUN = True
        bot.DRY_RUN_DIR = self.dir
        bot._DRY_RUN_FILE = None

    def tearDown(self):
        bot.DRY_RUN, bot.DRY_RUN_DIR, bot._DRY_RUN_FILE = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def _records(self):
        files = os.listdir(self.dir)
        self.assertEqual(len(files), 1, files)
        path = os.path.join(self.dir, files[0])
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_путь_стабилен_в_рамках_процесса(self):
        self.assertEqual(bot._dry_run_path(), bot._dry_run_path())

    def test_имя_содержит_время_и_pid(self):
        name = os.path.basename(bot._dry_run_path())
        self.assertRegex(name, r"^run_\d{8}_\d{6}_\d+\.jsonl$")
        self.assertIn(str(os.getpid()), name)

    def test_путь_не_зависит_от_pr_id(self):
        """Ключевая проверка находки SAST: имя файла одно для любого pr_id."""
        bot.dry_run_record(42, {"type": "comment"})
        bot.dry_run_record("../../etc/passwd", {"type": "comment"})
        bot.dry_run_record("/abs/olute", {"type": "comment"})

        files = os.listdir(self.dir)
        self.assertEqual(len(files), 1, files)
        self.assertTrue(files[0].startswith("run_"), files[0])
        # За пределами папки отчётов ничего не создано.
        self.assertFalse(os.path.exists(os.path.join(self.dir, "..", "etc")))

    def test_pr_id_проставляется_в_запись(self):
        bot.dry_run_record(42, {"type": "comment", "file": "a.pl"})
        # Запись типа `run` уже несёт свой pr_id — он не перетирается.
        bot.dry_run_record(42, {"type": "run", "pr_id": 7})

        records = self._records()
        self.assertEqual(records[0]["pr_id"], 42)
        self.assertEqual(records[1]["pr_id"], 7)

    def test_записи_разных_pr_разделимы(self):
        bot.dry_run_record(1, {"type": "comment"})
        bot.dry_run_record(2, {"type": "comment"})
        bot.dry_run_record(1, {"type": "comment"})

        ids = [r["pr_id"] for r in self._records()]
        self.assertEqual(ids, [1, 2, 1])

    def test_вне_dry_run_ничего_не_пишется(self):
        bot.DRY_RUN = False
        bot.dry_run_record(42, {"type": "comment"})
        self.assertEqual(os.listdir(self.dir), [])


class _FakeRequest:
    """Минимальная замена fastapi.Request: webhook читает только .json()."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class _FakeBackgroundTasks:
    """Замена fastapi.BackgroundTasks: ревью не запускаем, только фиксируем факт."""

    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


class WebhookPrIdTest(unittest.TestCase):
    """FR-004: нечисловой pr_id из недоверенного webhook отвергается."""

    def _post(self, payload):
        self.background = _FakeBackgroundTasks()
        return asyncio.run(bot.bitbucket_webhook(_FakeRequest(payload), self.background))

    @staticmethod
    def _payload(pr_id):
        return {
            "eventKey": "pr:opened",
            "pullRequest": {
                "id": pr_id,
                "title": "обычный заголовок",
                "toRef": {"repository": {"slug": "repo", "project": {"key": "PROJ"}}},
            },
        }

    def test_путь_в_id_отвергается(self):
        result = self._post(self._payload("../../etc/passwd"))
        self.assertEqual(result["status"], "error")

    def test_null_отвергается(self):
        result = self._post(self._payload(None))
        self.assertEqual(result["status"], "error")

    def test_словарь_отвергается(self):
        result = self._post(self._payload({"вложенный": "объект"}))
        self.assertEqual(result["status"], "error")

    def test_строковое_число_принимается(self):
        """Строковый числовой id — законный ввод, запрещать его незачем."""
        result = self._post(self._payload("42"))
        self.assertNotEqual(result["status"], "error")
        # Ревью действительно поставлено в очередь, причём с числовым id.
        self.assertEqual(len(self.background.tasks), 1)
        self.assertIn(42, self.background.tasks[0][1])


if __name__ == "__main__":
    unittest.main()
