"""Юнит-тесты выбора профиля стека (spec 022).

Запуск из папки pr_review_bot:  python -m unittest test_profiles
"""

import json
import os
import tempfile
import unittest

import profiles
from profiles import (
    GENERIC, PERL, PERL_LITE, SKIP, ConfigError, Glob, file_ext, parse_config,
    pre_profile, refine_by_shebang,
)


def _diff(*lines: str) -> str:
    """Добавленные строки в формате parse_bitbucket_diff: `[L<n>] +код`."""
    return "\n".join(f"[L{i}] +{code}" for i, code in enumerate(lines, start=1))


def _profile(path: str, config: dict | None = None, repo: str = "PROJ/repo",
             diff_text: str = "") -> str:
    cfg = parse_config(config or {})
    return pre_profile(path, diff_text or _diff("x = 1"), cfg.repo(repo), cfg)[0]


class TestFileExt(unittest.TestCase):
    def test_cases(self):
        cases = {
            "a/Foo.PM": ".pm",
            "a.tar.gz": ".gz",
            "Makefile": "",
            ".bashrc": "",
            "dir.d/run_job": "",
            "src/app.test.tsx": ".tsx",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(file_ext(path), expected)


class TestGlob(unittest.TestCase):
    def test_semantics_table(self):
        # Таблица из spec 022 FR-015.
        cases = [
            ("*.lock", "a/b/yarn.lock", True),
            ("docs/**", "docs/a.md", True),
            ("docs/**", "docs/x/y.md", True),
            ("docs/**", "src/docs/a.md", False),
            ("docs/**", "docs", False),
            ("**/docs/**", "docs/a.md", True),
            ("**/docs/**", "src/docs/a.md", True),
            ("**/node_modules/**", "node_modules/x/y.js", True),
            ("**/node_modules/**", "web/node_modules/x.js", True),
            ("backend/*.pm", "backend/a/B.pm", False),
            ("backend/*.pm", "backend/B.pm", True),
            ("a/**/b.txt", "a/b.txt", True),
            ("a/**/b.txt", "a/x/y/b.txt", True),
            ("file?.js", "file1.js", True),
            ("file?.js", "file10.js", False),
        ]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(Glob.compile(pattern).matches(path), expected)

    def test_regex_chars_are_literal(self):
        self.assertTrue(Glob.compile("a+b.(x)").matches("a+b.(x)"))
        self.assertFalse(Glob.compile("a.b").matches("axb"))

    def test_newline_in_path_does_not_bypass_skip(self):
        self.assertTrue(Glob.compile("**/dist/**").matches("dist/a\nb.js"))

    def test_case_sensitive(self):
        self.assertFalse(Glob.compile("docs/**").matches("Docs/a.md"))

    def test_limits(self):
        for bad in ("", 5, None, "a" * 201, "*" * 9):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    Glob.compile(bad)


class TestPreProfile(unittest.TestCase):
    def test_perl_extensions_default_to_perl_lite(self):
        for path in ("lib/A.pm", "bin/x.pl", "t/a.t", "lib/B.PM"):
            with self.subTest(path=path):
                self.assertEqual(_profile(path), PERL_LITE)

    def test_perl_profile_from_config(self):
        cfg = {"repos": {"PROJ/repo": {"perl_profile": "perl"}}}
        self.assertEqual(_profile("lib/A.pm", cfg), PERL)

    def test_repo_key_case_insensitive(self):
        cfg = {"repos": {"proj/MonoRepo": {"perl_profile": "perl"}}}
        self.assertEqual(_profile("lib/A.pm", cfg, repo="PROJ/monorepo"), PERL)

    def test_unknown_repo_gets_defaults(self):
        cfg = {"repos": {"PROJ/other": {"perl_profile": "perl"}}}
        self.assertEqual(_profile("lib/A.pm", cfg), PERL_LITE)

    def test_other_stacks_are_generic(self):
        for path in ("src/Form.tsx", "App.java", "x.py", "Makefile"):
            with self.subTest(path=path):
                self.assertEqual(_profile(path), GENERIC)

    def test_builtin_skip(self):
        for path in ("package-lock.json", "a/b/yarn.lock", "web/app.min.js",
                     "node_modules/x/y.js", "src/node_modules/x.js", "img/logo.png"):
            with self.subTest(path=path):
                self.assertEqual(_profile(path), SKIP)

    def test_builtin_dir_skip_does_not_hit_perl(self):
        self.assertEqual(_profile("vendor/Foo.pm"), PERL_LITE)
        self.assertEqual(_profile("vendor/foo.js"), SKIP)

    def test_config_skip_hits_perl_too(self):
        cfg = {"skip": ["**/vendor/**"]}
        self.assertEqual(_profile("vendor/Foo.pm", cfg), SKIP)

    def test_builtin_file_skip_beats_folder_rule(self):
        cfg = {"repos": {"PROJ/repo": {"paths": [{"glob": "front/**", "profile": "generic"}]}}}
        self.assertEqual(_profile("front/package-lock.json", cfg), SKIP)

    def test_folder_rule_beats_extension(self):
        cfg = {"repos": {"PROJ/repo": {"paths": [{"glob": "tools/**", "profile": "generic"}]}}}
        self.assertEqual(_profile("tools/Gen.pm", cfg), GENERIC)

    def test_folder_rules_first_match_wins(self):
        cfg = {"repos": {"PROJ/repo": {"paths": [
            {"glob": "docs/api/**", "profile": "generic"},
            {"glob": "docs/**", "profile": "skip"},
        ]}}}
        self.assertEqual(_profile("docs/api/a.md", cfg), GENERIC)
        self.assertEqual(_profile("docs/b.md", cfg), SKIP)

    def test_reason_names_the_rule(self):
        cfg = parse_config({"repos": {"PROJ/repo": {"paths": [{"glob": "docs/**", "profile": "skip"}]}}})
        _, reason = pre_profile("docs/a.md", "", cfg.repo("PROJ/repo"), cfg)
        self.assertEqual(reason, "правило папки docs/**")


class TestMinified(unittest.TestCase):
    def test_single_huge_line_is_minified(self):
        self.assertEqual(_profile("web/bundle.js", diff_text=_diff("x" * 1500)), SKIP)

    def test_one_long_line_among_normal_is_not(self):
        lines = ["y" * 40] * 20 + ["x" * 1500]
        self.assertEqual(_profile("src/q.js", diff_text=_diff(*lines)), GENERIC)

    def test_many_long_lines_are_minified(self):
        self.assertEqual(_profile("web/a.js", diff_text=_diff(*(["z" * 400] * 3))), SKIP)

    def test_perl_never_minified(self):
        self.assertEqual(_profile("lib/A.pm", diff_text=_diff("x" * 1500)), PERL_LITE)

    def test_context_lines_ignored(self):
        text = "[L1]  " + "x" * 5000 + "\n[L2] +short"
        self.assertEqual(_profile("src/a.js", diff_text=text), GENERIC)


class TestShebang(unittest.TestCase):
    def _refine(self, path: str, raw: str | None, diff_text: str = "",
                config: dict | None = None) -> tuple[str, str]:
        cfg = parse_config(config or {})
        repo_cfg = cfg.repo("PROJ/repo")
        profile, reason = pre_profile(path, diff_text, repo_cfg, cfg)
        return refine_by_shebang(profile, reason, path, raw, diff_text, repo_cfg)

    def test_shebang_from_raw(self):
        for first in ("#!/usr/bin/perl", "#!/usr/bin/env perl", "#!/usr/bin/perl -w"):
            with self.subTest(first=first):
                self.assertEqual(self._refine("bin/run_job", first + "\nuse strict;")[0], PERL_LITE)

    def test_shebang_from_diff(self):
        self.assertEqual(self._refine("bin/run_job", None, "[L1] +#!/usr/bin/perl\n[L2] +1;")[0], PERL_LITE)
        self.assertEqual(self._refine("bin/run_job", None, "[L1]  #!/usr/bin/perl\n[L2] +1;")[0], PERL_LITE)

    def test_shebang_uses_repo_perl_profile(self):
        cfg = {"repos": {"PROJ/repo": {"perl_profile": "perl"}}}
        self.assertEqual(self._refine("bin/run_job", "#!/usr/bin/perl", config=cfg)[0], PERL)

    def test_other_shebang_stays_generic(self):
        self.assertEqual(self._refine("bin/run.sh_x", "#!/bin/bash")[0], GENERIC)
        self.assertEqual(self._refine("bin/run_job", "#!/bin/bash")[0], GENERIC)

    def test_first_line_unavailable(self):
        self.assertEqual(self._refine("bin/run_job", None, "[L5] +1;"), (GENERIC, "по умолчанию"))

    def test_file_with_extension_not_refined(self):
        self.assertEqual(self._refine("bin/x.sh", "#!/usr/bin/perl")[0], GENERIC)

    def test_explicit_folder_rule_not_refined(self):
        cfg = {"repos": {"PROJ/repo": {"paths": [{"glob": "bin/**", "profile": "generic"}]}}}
        self.assertEqual(self._refine("bin/run_job", "#!/usr/bin/perl", config=cfg)[0], GENERIC)


class TestParseConfig(unittest.TestCase):
    def test_valid_full_example(self):
        cfg = parse_config({
            "skip": ["docs/generated/**", "*.pb.go"],
            "repos": {
                "PROJ/perl-repo": {"perl_profile": "perl"},
                "PROJ/monorepo": {"paths": [
                    {"glob": "docs/**", "profile": "skip"},
                    {"glob": "backend/legacy/**", "profile": "perl-lite"},
                ]},
                "PROJ/sandbox": {"enabled": False},
            },
        })
        self.assertEqual(len(cfg.skip), 2)
        self.assertFalse(cfg.repo("PROJ/sandbox").enabled)
        self.assertTrue(cfg.repo("PROJ/unknown").enabled)

    def test_invalid(self):
        bad_configs = {
            "не объект": [],
            "неизвестное поле в корне": {"skipp": []},
            "skip не список": {"skip": "*.lock"},
            "repos не объект": {"repos": []},
            "неизвестное поле репо": {"repos": {"P/r": {"enable": False}}},
            "enabled строкой": {"repos": {"P/r": {"enabled": "false"}}},
            "неизвестный perl_profile": {"repos": {"P/r": {"perl_profile": "generic"}}},
            "paths не список": {"repos": {"P/r": {"paths": {}}}},
            "правило без glob": {"repos": {"P/r": {"paths": [{"profile": "skip"}]}}},
            "лишнее поле правила": {"repos": {"P/r": {"paths": [
                {"glob": "a/**", "profile": "skip", "x": 1}]}}},
            "неизвестный профиль": {"repos": {"P/r": {"paths": [{"glob": "a/**", "profile": "java"}]}}},
            "perl без perl_profile": {"repos": {"P/r": {"paths": [{"glob": "a/**", "profile": "perl"}]}}},
            "ключи репо по регистру": {"repos": {"P/r": {}, "p/R": {}}},
            "пустой шаблон": {"skip": [""]},
        }
        for name, data in bad_configs.items():
            with self.subTest(name=name):
                with self.assertRaises(ConfigError):
                    parse_config(data)

    def test_example_file_is_valid(self):
        example = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles.example.json")
        with open(example, encoding="utf-8") as fh:
            parse_config(json.load(fh))

    def test_perl_in_paths_allowed_with_perl_profile(self):
        cfg = parse_config({"repos": {"P/r": {"perl_profile": "perl",
                                             "paths": [{"glob": "a/**", "profile": "perl"}]}}})
        self.assertEqual(cfg.repo("P/r").paths[0][1], PERL)


class TestLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "profiles.json")
        profiles._last_good = None
        profiles._status = profiles.STATUS_ABSENT

    def tearDown(self):
        self.tmp.cleanup()
        profiles._last_good = None
        profiles._status = profiles.STATUS_ABSENT

    def _write(self, text: str) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_absent_file_gives_defaults(self):
        cfg = profiles.load(self.path)
        self.assertEqual(cfg, profiles.DEFAULT_CONFIG)
        self.assertEqual(profiles.status(), "absent")

    def test_directory_gives_defaults(self):
        os.mkdir(self.path)
        with self.assertLogs("jarvis-pr-review", "WARNING"):
            cfg = profiles.load(self.path)
        self.assertEqual(cfg, profiles.DEFAULT_CONFIG)

    def test_relative_path_is_error(self):
        with self.assertLogs("jarvis-pr-review", "ERROR"):
            profiles.load("profiles.json")
        self.assertEqual(profiles.status(), "error")

    def test_ok(self):
        self._write(json.dumps({"repos": {"P/r": {"enabled": False}}}))
        cfg = profiles.load(self.path)
        self.assertFalse(cfg.repo("P/r").enabled)
        self.assertEqual(profiles.status(), "ok")

    def test_broken_first_load_gives_defaults(self):
        self._write("{ not json")
        with self.assertLogs("jarvis-pr-review", "ERROR"):
            cfg = profiles.load(self.path)
        self.assertEqual(cfg, profiles.DEFAULT_CONFIG)
        self.assertEqual(profiles.status(), "error")

    def test_broken_after_good_keeps_last_good(self):
        self._write(json.dumps({"repos": {"P/r": {"enabled": False}}}))
        profiles.load(self.path)
        self._write(json.dumps({"repos": {"P/r": {"enable": False}}}))
        with self.assertLogs("jarvis-pr-review", "ERROR"):
            cfg = profiles.load(self.path)
        self.assertFalse(cfg.repo("P/r").enabled)
        self.assertEqual(profiles.status(), "error")

    def test_duplicate_json_keys_are_error(self):
        self._write('{"repos": {"P/r": {"enabled": false}, "P/r": {}}}')
        with self.assertLogs("jarvis-pr-review", "ERROR"):
            profiles.load(self.path)
        self.assertEqual(profiles.status(), "error")

    def test_default_path_is_next_to_module_not_cwd(self):
        old = os.environ.pop("PROFILES_PATH", None)
        try:
            expected_dir = os.path.dirname(os.path.abspath(profiles.__file__))
            self.assertEqual(os.path.dirname(profiles.default_path()), expected_dir)
        finally:
            if old is not None:
                os.environ["PROFILES_PATH"] = old


if __name__ == "__main__":
    unittest.main()
