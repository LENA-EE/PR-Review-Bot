"""Юнит-тесты клиента mcp-drospr (моками, без сети).

Запуск из папки pr_review_bot:  python -m unittest test_mcp_client
"""

import unittest
from unittest import mock

import requests

import mcp_client
from mcp_client import analyze_perlcritic, McpUnavailable, _build_payload


def _resp(json_data):
    r = mock.Mock()
    r.raise_for_status = mock.Mock()
    r.json = mock.Mock(return_value=json_data)
    return r


class TestPayload(unittest.TestCase):
    def test_payload_shape(self):
        p = _build_payload("my $x;", "a.pl", 5, None)
        self.assertEqual(p["method"], "tools/call")
        self.assertEqual(p["params"]["name"], "perlcritic_analyze")
        self.assertEqual(p["params"]["arguments"]["code"], "my $x;")
        self.assertEqual(p["params"]["arguments"]["severity"], 5)
        self.assertNotIn("perlcriticrc", p["params"]["arguments"])  # None → не шлём

    def test_payload_with_config(self):
        p = _build_payload("c", "a.pl", 5, "severity = 5")
        self.assertEqual(p["params"]["arguments"]["perlcriticrc"], "severity = 5")


class TestAnalyze(unittest.TestCase):
    def _call(self, json_data):
        with mock.patch.object(mcp_client.requests, "post", return_value=_resp(json_data)):
            return analyze_perlcritic("http://mcp", "code", "a.pl", 5)

    def test_returns_issues(self):
        data = {"jsonrpc": "2.0", "id": 1,
                "result": {"report": {"issues": [
                    {"line": 10, "severity": 5, "policy": "X"},
                ]}}}
        issues = self._call(data)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["policy"], "X")

    def test_empty_issues_is_clean_code_B2(self):
        # КЛЮЧЕВОЙ тест B2: пустой issues = чистый код, НЕ ошибка.
        data = {"result": {"report": {"issues": []}}}
        self.assertEqual(self._call(data), [])

    def test_garbage_entries_filtered(self):
        data = {"result": {"report": {"issues": [{"line": 1}, "junk", None]}}}
        self.assertEqual(self._call(data), [{"line": 1}])

    def test_no_report_raises(self):
        # Старый сервер: только проза, нет структуры report.issues → деградация.
        data = {"result": {"content": [{"type": "text", "text": "..."}]}}
        with self.assertRaises(McpUnavailable):
            self._call(data)

    def test_jsonrpc_error_raises(self):
        data = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}
        with self.assertRaises(McpUnavailable):
            self._call(data)

    def test_empty_base_url_raises(self):
        with self.assertRaises(McpUnavailable):
            analyze_perlcritic("", "code", "a.pl", 5)

    def test_timeout_raises(self):
        with mock.patch.object(mcp_client.requests, "post",
                               side_effect=requests.exceptions.Timeout()):
            with self.assertRaises(McpUnavailable):
                analyze_perlcritic("http://mcp", "code", "a.pl", 5)

    def test_network_error_raises(self):
        with mock.patch.object(mcp_client.requests, "post",
                               side_effect=requests.exceptions.ConnectionError()):
            with self.assertRaises(McpUnavailable):
                analyze_perlcritic("http://mcp", "code", "a.pl", 5)

    def test_bad_json_raises(self):
        bad = mock.Mock()
        bad.raise_for_status = mock.Mock()
        bad.json = mock.Mock(side_effect=ValueError("not json"))
        with mock.patch.object(mcp_client.requests, "post", return_value=bad):
            with self.assertRaises(McpUnavailable):
                analyze_perlcritic("http://mcp", "code", "a.pl", 5)


if __name__ == "__main__":
    unittest.main()
