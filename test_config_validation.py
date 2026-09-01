"""Тесты валидации конфига и пути отчёта dry-run (spec 017 + spec 019).

Закрывают находки SAST: путь файла отчёта не должен собираться из входных
данных, а URL из ENV не должны уходить в requests непроверенными (017).
Плюс паритет проверки конфига между сервисом и CLI и валидация файловых
путей из ENV (019).

Запуск из папки pr_review_bot:  python -m unittest test_config_validation
"""

import asyncio
import datetime
import importlib
import json
import threading
import os
import shutil
import tempfile
import unittest
from unittest import mock

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

    def test_путь_один_при_конкурентном_первом_вызове(self):
        """Гонка: в webhook-режиме ревью идёт в нескольких потоках.

        Без блокировки два потока вычисляют разные метки времени, и записи
        одного прогона разъезжаются по двум файлам.
        """
        # Настоящие метки времени в пределах одной секунды совпали бы и без
        # блокировки — тест был бы ложно-зелёным. Подменяем часы так, чтобы
        # каждое обращение давало новое значение: тогда второе вычисление
        # обязано быть видимым.
        calls = []

        class _CountingClock:
            @staticmethod
            def now():
                calls.append(1)
                return datetime.datetime(2026, 1, 1, 0, 0, len(calls))

        self.addCleanup(setattr, bot, "datetime", bot.datetime)
        bot.datetime = _CountingClock

        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()          # стартуем максимально одновременно
            results.append(bot._dry_run_path())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(set(results)), 1, set(results))
        # Путь вычислен ровно один раз на весь прогон.
        self.assertEqual(len(calls), 1)

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


class CheckDirPathTest(unittest.TestCase):
    """FR-003: валидация каталога dry-run из ENV."""

    def test_пустое_значение_проблема(self):
        self.assertIsNotNone(bot._check_dir_path("D", ""))

    def test_относительный_путь_проблема(self):
        # Причина требования — не traversal, а разрешение от cwd (клон репозитория).
        self.assertIsNotNone(bot._check_dir_path("D", "reports"))
        self.assertIsNotNone(bot._check_dir_path("D", "./reports"))

    def test_абсолютный_существующий_каталог_ок(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertIsNone(bot._check_dir_path("D", d))

    def test_абсолютный_несуществующий_путь_ок(self):
        # Каталог создаётся лениво при записи — отсутствие пути не блокер.
        d = os.path.join(tempfile.mkdtemp(), "ещё_нет")
        self.addCleanup(shutil.rmtree, os.path.dirname(d), True)
        self.assertIsNone(bot._check_dir_path("D", d))

    def test_путь_ведёт_на_файл_проблема(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(os.remove, path)
        self.assertIsNotNone(bot._check_dir_path("D", path))

    def test_тильда_принимается_валидатором(self):
        # `~/reports` — абсолютный путь после раскрытия, блокировать его не за что.
        self.assertIsNone(bot._check_dir_path("D", "~/reports"))

    def test_тильда_раскрыта_в_самой_переменной(self):
        """Регрессия: валидатор раскрывал `~`, а DRY_RUN_DIR оставался с тильдой.

        Тогда os.makedirs создавал каталог с именем `~` в текущей директории —
        в клоне репозитория, ровно там, откуда отчёт и уводили. Проверяем не
        валидатор, а значение, которое реально уходит в файловые операции.
        """
        with mock.patch.dict(os.environ, {"JARVIS_DRY_RUN_DIR": "~/reports"}):
            reloaded = importlib.reload(bot)
        try:
            self.assertNotIn("~", reloaded.DRY_RUN_DIR)
            self.assertTrue(os.path.isabs(reloaded.DRY_RUN_DIR))
        finally:
            # Возвращаем модуль в состояние по умолчанию: остальные тесты
            # работают с этим же импортом.
            importlib.reload(bot)


class CheckFilePathTest(unittest.TestCase):
    """FR-004: валидация пути стайлгайда из ENV."""

    def test_существующий_файл_ок(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(os.remove, path)
        self.assertIsNone(bot._check_file_path("F", path))

    def test_несуществующий_файл_проблема(self):
        self.assertIsNotNone(
            bot._check_file_path("F", os.path.join(tempfile.gettempdir(), "нет_такого.md"))
        )

    def test_каталог_вместо_файла_проблема(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertIsNotNone(bot._check_file_path("F", d))

    def test_пустое_значение_проблема(self):
        self.assertIsNotNone(bot._check_file_path("F", ""))


class CheckConfigReturnTest(unittest.TestCase):
    """FR-001, FR-003: check_config() возвращает список проблем."""

    def setUp(self):
        # check_config читает module-level globals — сохраняем и восстанавливаем.
        self._saved = {
            k: getattr(bot, k)
            for k in ("BITBUCKET_TOKEN", "FENIX_TOKEN", "BITBUCKET_URL",
                      "FENIX_URL", "MCP_DROSPR_URL", "DRY_RUN", "DRY_RUN_DIR")
        }
        # Заведомо валидный конфиг — дальше точечно портим одно поле.
        bot.BITBUCKET_TOKEN = "t"
        bot.FENIX_TOKEN = "t"
        bot.BITBUCKET_URL = "http://host"
        bot.FENIX_URL = "http://host"
        bot.MCP_DROSPR_URL = ""
        bot.DRY_RUN = False
        bot.DRY_RUN_DIR = ""

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(bot, k, v)

    def test_возвращает_список(self):
        self.assertIsInstance(bot.check_config(), list)

    def test_валидный_конфиг_пустой_список(self):
        self.assertEqual(bot.check_config(), [])

    def test_отсутствие_токена_проблема(self):
        bot.BITBUCKET_TOKEN = ""
        problems = bot.check_config()
        self.assertTrue(any("BITBUCKET_TOKEN" in p for p in problems))

    def test_битый_url_проблема(self):
        bot.FENIX_URL = "не-url"
        problems = bot.check_config()
        self.assertTrue(any("FENIX_URL" in p for p in problems))

    def test_dry_run_относительный_каталог_блокирует(self):
        bot.DRY_RUN = True
        bot.DRY_RUN_DIR = "reports"
        problems = bot.check_config()
        self.assertTrue(any("JARVIS_DRY_RUN_DIR" in p for p in problems))

    def test_dry_run_абсолютный_каталог_не_блокирует(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        bot.DRY_RUN = True
        bot.DRY_RUN_DIR = d
        self.assertEqual(bot.check_config(), [])

    def test_каталог_не_проверяется_вне_dry_run(self):
        # DRY_RUN=False: пустой каталог не должен превращаться в проблему.
        bot.DRY_RUN = False
        bot.DRY_RUN_DIR = ""
        self.assertEqual(bot.check_config(), [])


class StyleguidePathParityTest(unittest.TestCase):
    """FR-005: проверяемый и открываемый путь стайлгайда совпадают."""

    def setUp(self):
        self._saved = bot.STYLEGUIDE_PATH

    def tearDown(self):
        bot.STYLEGUIDE_PATH = self._saved

    def test_валидатор_и_загрузчик_смотрят_на_один_файл(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "styleguide.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("правило: use strict")
        bot.STYLEGUIDE_PATH = path
        # Путь прошёл валидацию...
        self.assertIsNone(bot._check_file_path("STYLEGUIDE_PATH", bot.STYLEGUIDE_PATH))
        # ...и ровно он же успешно открылся загрузчиком.
        self.assertIn("use strict", bot.load_styleguide())

    def test_каталог_вместо_файла_не_роняет_ревью(self):
        """Критерий приёмки 019: каталог в STYLEGUIDE_PATH → предупреждение, не отказ.

        FileNotFoundError каталог не покрывает (там IsADirectoryError, на Windows
        PermissionError), поэтому раньше падало КАЖДОЕ ревью, а не только старт.
        """
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        bot.STYLEGUIDE_PATH = d
        self.assertIsNotNone(bot._check_file_path("STYLEGUIDE_PATH", d))
        self.assertEqual(bot.load_styleguide(), "")

    def test_несуществующий_файл_не_роняет_ревью(self):
        bot.STYLEGUIDE_PATH = os.path.join(tempfile.gettempdir(), "нет_такого_файла.md")
        self.assertEqual(bot.load_styleguide(), "")

    def test_путь_с_тильдой_нормализуется_одинаково(self):
        # _resolve_path раскрывает ~ и там, и там: проверенный путь = открытый путь.
        resolved = bot._resolve_path("~/styleguide.md")
        self.assertNotIn("~", resolved)
        self.assertTrue(os.path.isabs(resolved))


if __name__ == "__main__":
    unittest.main()
