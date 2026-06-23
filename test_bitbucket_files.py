"""Юнит-тесты дотягивания raw-файлов из Bitbucket (моками, без сети).

Запуск из папки pr_review_bot:  python -m unittest test_bitbucket_files
"""

import unittest
from unittest import mock

import bitbucket_files
from bitbucket_files import get_file_content, get_perlcriticrc


def _resp(status=200, content=b""):
    r = mock.Mock()
    r.status_code = status
    r.content = content
    r.raise_for_status = mock.Mock()
    return r


class TestGetFileContent(unittest.TestCase):
    def test_ok_normalizes_crlf(self):
        with mock.patch.object(bitbucket_files.requests, "get",
                               return_value=_resp(200, b"a\r\nb\rc\n")) as g:
            out = get_file_content("http://bb", {}, "P", "R", "deadbeef", "x.pl")
        self.assertEqual(out, "a\nb\nc\n")          # CRLF и CR → LF
        g.assert_called_once()

    def test_strips_bom(self):
        with mock.patch.object(bitbucket_files.requests, "get",
                               return_value=_resp(200, "﻿use strict;".encode("utf-8"))):
            out = get_file_content("http://bb", {}, "P", "R", "ref", "x.pl")
        self.assertEqual(out, "use strict;")

    def test_cp1251_fallback(self):
        # cp1251-байты (комментарий по-русски) не должны ронять чтение.
        raw = "# комментарий".encode("cp1251")
        with mock.patch.object(bitbucket_files.requests, "get",
                               return_value=_resp(200, raw)):
            out = get_file_content("http://bb", {}, "P", "R", "ref", "x.pl")
        self.assertIn("комментарий", out)

    def test_404_returns_none(self):
        with mock.patch.object(bitbucket_files.requests, "get",
                               return_value=_resp(404)):
            self.assertIsNone(get_file_content("http://bb", {}, "P", "R", "ref", "x.pl"))

    def test_exception_returns_none(self):
        with mock.patch.object(bitbucket_files.requests, "get",
                               side_effect=Exception("boom")):
            self.assertIsNone(get_file_content("http://bb", {}, "P", "R", "ref", "x.pl"))

    def test_empty_ref_no_request(self):
        with mock.patch.object(bitbucket_files.requests, "get") as g:
            self.assertIsNone(get_file_content("http://bb", {}, "P", "R", "", "x.pl"))
        g.assert_not_called()


class TestGetPerlcriticrc(unittest.TestCase):
    def test_requests_root_path(self):
        with mock.patch.object(bitbucket_files, "get_file_content",
                               return_value="severity = 5") as gf:
            out = get_perlcriticrc("http://bb", {}, "P", "R", "ref")
        self.assertEqual(out, "severity = 5")
        # путь — именно .perlcriticrc
        self.assertEqual(gf.call_args.args[5], ".perlcriticrc")

    def test_missing_returns_none(self):
        with mock.patch.object(bitbucket_files, "get_file_content", return_value=None):
            self.assertIsNone(get_perlcriticrc("http://bb", {}, "P", "R", "ref"))


if __name__ == "__main__":
    unittest.main()
