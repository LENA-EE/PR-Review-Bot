"""Тесты подключения профилей стеков в цикл ревью и вебхук (spec 022).

Сеть замокана целиком: Bitbucket, Феникс и mcp-drospr не вызываются.
Запуск из папки pr_review_bot:  python -m unittest test_review_profiles
"""

import asyncio
import unittest
from contextlib import ExitStack
from unittest import mock

import pr_review_bot as bot
import profiles

PM = {"path": "lib/Pay.pm", "text": "[L1] +sub calc { return 1; }", "added_lines": 1}
TSX = {"path": "front/Form.tsx", "text": "[L1] +const x = 1;", "added_lines": 1}
LOCK = {"path": "front/package-lock.json", "text": "[L1] +{}", "added_lines": 1}
SCRIPT = {"path": "bin/run_job", "text": "[L1] +#!/usr/bin/perl\n[L2] +1;", "added_lines": 2}


class _ReviewHarness(unittest.TestCase):
    """Запускает _do_review с замоканным окружением и собирает вызовы."""

    def run_review(self, files, config=None, resolve=None, fenix_results=None):
        cfg = profiles.parse_config(config or {})
        with ExitStack() as stack:
            p = lambda *a, **kw: stack.enter_context(mock.patch.object(*a, **kw))  # noqa: E731
            p(bot.profiles, "load", return_value=cfg)
            self.get_diff = p(bot, "get_pr_diff", return_value=files)
            p(bot, "load_styleguide", return_value="СТАЙЛГАЙД")
            p(bot, "REVIEW_CONTEXT_MODE", "hunks")
            p(bot, "PERLCRITIC_ENABLED", True)
            p(bot, "IMPACT_ENABLED", True)
            p(bot, "MCP_DROSPR_URL", "http://mcp.local")
            p(bot, "STYLEGUIDE_RULES_ENABLED", False)
            self.raw = p(bot.bitbucket_files, "get_file_content", return_value="sub calc { return 1; }")
            self.perlcritic = p(bot.mcp_client, "analyze_perlcritic", return_value=[])
            self.callers = p(bot.mcp_client, "get_callers", return_value=[])
            self.fenix = p(bot, "ask_fenix", side_effect=fenix_results, return_value=[])
            if fenix_results is None:
                self.fenix.side_effect = None
            if resolve is not None:
                p(bot, "_resolve_review_context", side_effect=resolve)
            p(bot, "get_existing_comment_keys", return_value=set())
            p(bot, "post_comment")
            p(bot, "post_general_comment")
            bot._do_review("PROJ", "repo", 7, "PROJ", "repo", "abc123")

    def fenix_calls_by_path(self) -> dict:
        """{первая строка diff → kwargs вызова} — чтобы различать файлы."""
        out = {}
        for call in self.fenix.call_args_list:
            out[call.args[0].split("\n")[0]] = {"styleguide": call.args[1], **call.kwargs}
        return out


class TestReviewLoopProfiles(_ReviewHarness):
    def test_mixed_pr_each_file_gets_its_profile(self):
        self.run_review([PM, TSX, LOCK])
        calls = self.fenix_calls_by_path()
        self.assertEqual(len(calls), 2)

        pm = calls[PM["text"]]
        self.assertEqual(pm["profile"], profiles.PERL_LITE)
        self.assertEqual(pm["styleguide"], "СТАЙЛГАЙД")

        tsx = calls[TSX["text"]]
        self.assertEqual(tsx["profile"], profiles.GENERIC)
        self.assertEqual(tsx["file_ext"], ".tsx")
        self.assertEqual(tsx["styleguide"], "")

    def test_skipped_file_is_not_downloaded(self):
        self.run_review([LOCK, PM])
        fetched = [c.args[5] for c in self.raw.call_args_list]
        self.assertNotIn(LOCK["path"], fetched)

    def test_perlcritic_only_for_perl(self):
        self.run_review([PM, TSX])
        checked = [c.args[2] for c in self.perlcritic.call_args_list]
        self.assertEqual(checked, [PM["path"]])

    def test_impact_off_for_perl_lite(self):
        self.run_review([PM])
        self.callers.assert_not_called()

    def test_impact_on_for_perl_profile(self):
        self.run_review([PM], config={"repos": {"PROJ/repo": {"perl_profile": "perl"}}})
        self.callers.assert_called()

    def test_folder_rule_overrides_extension(self):
        cfg = {"repos": {"PROJ/repo": {"paths": [{"glob": "lib/**", "profile": "generic"}]}}}
        self.run_review([PM], config=cfg)
        self.assertEqual(self.fenix_calls_by_path()[PM["text"]]["profile"], profiles.GENERIC)
        self.perlcritic.assert_not_called()

    def test_shebang_script_reviewed_as_perl(self):
        self.run_review([SCRIPT])
        self.assertEqual(self.fenix_calls_by_path()["[L1] +#!/usr/bin/perl"]["profile"], profiles.PERL_LITE)

    def test_fallback_retry_keeps_profile(self):
        # Полный файл не прошёл в Феникс → повтор по ханкам с тем же профилем.
        resolve = lambda f, *a: ("const x = 1;", f["text"], "file", "")  # noqa: E731
        self.run_review([TSX], resolve=resolve, fenix_results=[None, []])
        self.assertEqual(self.fenix.call_count, 2)
        retry = self.fenix.call_args_list[1].kwargs
        self.assertEqual(retry["profile"], profiles.GENERIC)
        self.assertEqual(retry["file_ext"], ".tsx")
        self.assertEqual(self.fenix.call_args_list[1].args[1], "")

    def test_disabled_repo_makes_no_requests(self):
        self.run_review([PM], config={"repos": {"PROJ/repo": {"enabled": False}}})
        self.get_diff.assert_not_called()
        self.fenix.assert_not_called()


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class _FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


class TestWebhookDisabledRepo(unittest.TestCase):
    def _post(self, title: str, config: dict) -> tuple[dict, _FakeBackgroundTasks]:
        payload = {
            "eventKey": "pr:opened",
            "pullRequest": {
                "id": 7, "title": title,
                "toRef": {"repository": {"slug": "repo", "project": {"key": "PROJ"}}},
            },
        }
        background = _FakeBackgroundTasks()
        with mock.patch.object(bot.profiles, "load", return_value=profiles.parse_config(config)):
            result = asyncio.run(bot.bitbucket_webhook(_FakeRequest(payload), background))
        return result, background

    def test_disabled_repo_skipped_before_wip_notice(self):
        off = {"repos": {"PROJ/repo": {"enabled": False}}}
        for title in ("обычный PR", "WIP: черновик"):
            with self.subTest(title=title):
                result, background = self._post(title, off)
                self.assertEqual(result["reason"], "repo disabled")
                self.assertEqual(background.tasks, [])

    def test_enabled_repo_queued(self):
        result, background = self._post("обычный PR", {})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(background.tasks), 1)


if __name__ == "__main__":
    unittest.main()
