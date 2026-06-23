"""
Клиент mcp-drospr для запуска perlcritic (Вариант B, ARCHITECTURE.md §2.10).

Бот — JSON-RPC 2.0 клиент: POST {MCP_DROSPR_URL}/sse, method tools/call,
name=perlcritic_analyze. Контракт сверен по коду mcp-drospr v1.0.0:
  • вход:  arguments.code = СОДЕРЖИМОЕ файла (НЕ путь), + severity (1=все..5=критич.)
  • выход: result.report.issues[] = [{file, line, col, severity, policy, snippet}]
           (рядом есть result.content[].text — проза для LLM, её НЕ используем)

Модуль знает только контракт mcp-drospr и переводит любой сбой в McpUnavailable —
оркестратор по нему деградирует (ревью без perlcritic + пометка), но НЕ падает.

BLOCKER-фикс B2: пустой issues (issues == []) — это «нарушений нет» (ЧИСТЫЙ КОД),
норма, НЕ деградация. McpUnavailable бросаем только при сетевой ошибке/таймауте/
невалидном ответе/ОТСУТСТВИИ структуры report.issues (старый сервер = только проза).
"""

import logging
from typing import Optional

import requests

log = logging.getLogger("jarvis-pr-review")

_JSONRPC_ID = 1
_TOOL_NAME = "perlcritic_analyze"


class McpUnavailable(Exception):
    """mcp-drospr недоступен или вернул нераспознаваемый ответ — повод к деградации."""


def _build_payload(code: str, filename: str, severity: int,
                   perlcriticrc: Optional[str]) -> dict:
    arguments: dict = {
        "code": code,
        "filename": filename,
        "severity": severity,
    }
    # Кастомный конфиг mcp-drospr v1.0.0 пока НЕ принимает (нет параметра в схеме).
    # Передаём только если задан — forward-compat; до доработки сервера тут всегда None.
    if perlcriticrc is not None:
        arguments["perlcriticrc"] = perlcriticrc
    return {
        "jsonrpc": "2.0",
        "id": _JSONRPC_ID,
        "method": "tools/call",
        "params": {"name": _TOOL_NAME, "arguments": arguments},
    }


def _extract_issues(data: dict) -> list[dict]:
    """Достаёт report.issues[] из JSON-RPC ответа. Бросает McpUnavailable при сбое контракта.

    Различаем (фикс B2):
      • есть report.issues == []  → чистый код, вернуть []
      • нет report/issues вовсе   → старый сервер (только проза) → McpUnavailable
      • JSON-RPC error            → McpUnavailable
    """
    if not isinstance(data, dict):
        raise McpUnavailable("ответ mcp-drospr не JSON-объект")
    if data.get("error") is not None:
        raise McpUnavailable(f"JSON-RPC error: {data.get('error')}")

    # tools/call оборачивает результат тула в result; на случай отсутствия обёртки — сам data.
    result = data.get("result", data)
    report = result.get("report") if isinstance(result, dict) else None
    if not isinstance(report, dict) or "issues" not in report:
        raise McpUnavailable(
            "в ответе нет структуры report.issues — вероятно, версия mcp-drospr "
            "отдаёт только прозу (нужно структурное поле issues)"
        )
    issues = report.get("issues")
    if issues is None:
        return []
    if not isinstance(issues, list):
        raise McpUnavailable(f"report.issues не список: {type(issues).__name__}")
    # Оставляем только корректные записи-словари, мусор отбрасываем (не валим ревью).
    return [i for i in issues if isinstance(i, dict)]


def analyze_perlcritic(
    base_url: str,
    code: str,
    filename: str,
    severity: int,
    perlcriticrc: Optional[str] = None,
    timeout: int = 20,
    verify: bool = False,
) -> list[dict]:
    """Запускает perlcritic в mcp-drospr и возвращает report.issues[].

    Пустой список = нарушений нет (чистый код). Бросает McpUnavailable при любом сбое.
    """
    if not base_url:
        raise McpUnavailable("MCP_DROSPR_URL не задан")

    endpoint = f"{base_url}/sse"
    payload = _build_payload(code, filename, severity, perlcriticrc)
    try:
        resp = requests.post(endpoint, json=payload, timeout=timeout, verify=verify)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout as e:
        raise McpUnavailable(f"таймаут mcp-drospr ({timeout}с)") from e
    except requests.exceptions.RequestException as e:
        raise McpUnavailable(f"сетевая ошибка mcp-drospr: {type(e).__name__}: {e}") from e
    except ValueError as e:  # resp.json() не распарсился
        raise McpUnavailable(f"ответ mcp-drospr не JSON: {e}") from e

    return _extract_issues(data)
