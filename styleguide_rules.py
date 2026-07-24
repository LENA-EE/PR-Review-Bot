"""
Детерминированный слой стайлгайда команды — метка `[codestyle]` (ARCHITECTURE.md §2.2).

Зачем отдельный модуль, если есть styleguide.md:
  • styleguide.md — ПРОЗА, она уходит в промпт Феникса; его находки выходят под [JARVIS].
  • styleguide_rules.py — ДВИЖОК: гоняет regex-правила команды по добавленным строкам
    diff'а сам, мимо ИИ (0 токенов) → находки выходят под [codestyle].
Сами правила живут в ПРИМОНТИРОВАННОМ файле (как styleguide.md), движок их только
исполняет — менять правила можно заменой файла на VM, без ребилда и правок кода.

Привязка к строкам (§2.9): проверяем ТОЛЬКО ДОБАВЛЕННЫЕ строки (`[L<n>] +` в f["text"]),
ровно как diff_filter для perlcritic — чтобы не вешать замечания на чужой/контекстный код.

Чистый модуль (parse/scan — без I/O, легко тестируются); тонкий load_rules_file —
единственная I/O-функция, graceful: нет файла → пустой список правил (слой просто молчит).

Формат файла правил — одна строка = одно правило:
    severity | regex | message
  • severity ∈ error | warning | suggestion (иное → suggestion, ревью не валим)
  • regex МОЖЕТ содержать `|` (альтернацию): разделителями считаются ПЕРВЫЙ и
    ПОСЛЕДНИЙ `|`, всё между ними — это regex.
  • строки с `#` и пустые — игнорируются.
  • ведущие/хвостовые пробелы в regex обрезаются (для пробела на краю — `\\s`).
"""

import logging
import re
from typing import NamedTuple, Optional

# Словарь severity берём из единого источника, чтобы не плодить магические строки.
from perlcritic_severity import ERROR, WARNING, SUGGESTION

log = logging.getLogger("jarvis-pr-review")

_VALID_SEVERITY = {ERROR, WARNING, SUGGESTION}

# Добавленная строка в f["text"]: `[L<n>] +<код>`. Захватываем номер и сам код.
# CONTEXT-строки (`[L<n>] ` без `+`) сюда НЕ попадают — так же, как в diff_filter (B1).
_ADDED_LINE_RE = re.compile(r"^\[L(\d+)\] \+(.*)$", re.MULTILINE)


class Rule(NamedTuple):
    """Одно правило стайлгайда: скомпилированный regex + как его показать."""
    severity: str
    pattern: "re.Pattern[str]"
    message: str
    raw_pattern: str   # исходный текст regex — для логов/отладки, в дедуп не идёт


def _parse_severity(token: str) -> str:
    """severity из файла → валидный уровень бота. Кривое значение → suggestion (не валим)."""
    sev = token.strip().lower()
    return sev if sev in _VALID_SEVERITY else SUGGESTION


def parse_rules(text: Optional[str]) -> list[Rule]:
    """Парсит содержимое файла правил в список Rule. Без I/O.

    Битые строки (нет двух `|`, нераскомпилируемый regex) пропускаются с warning —
    одно плохое правило не должно ронять весь слой (AES §7.3).
    """
    rules: list[Rule] = []
    if not text:
        return rules

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split("|")
        if len(parts) < 3:
            log.warning("styleguide: правило пропущено (нужно severity|regex|message): %r", raw_line)
            continue

        severity = _parse_severity(parts[0])
        # regex — всё между ПЕРВЫМ и ПОСЛЕДНИМ `|`; внутренние `|` (альтернация) сохраняются.
        raw_pattern = "|".join(parts[1:-1]).strip()
        message = parts[-1].strip()

        if not raw_pattern or not message:
            log.warning("styleguide: правило пропущено (пустой regex или message): %r", raw_line)
            continue

        try:
            pattern = re.compile(raw_pattern)
        except re.error as e:
            log.warning("styleguide: правило пропущено (невалидный regex %r): %s", raw_pattern, e)
            continue

        rules.append(Rule(severity=severity, pattern=pattern, message=message, raw_pattern=raw_pattern))

    return rules


def added_lines_from_diff_text(diff_text: Optional[str]) -> list[tuple[int, str]]:
    """Список (номер_строки, код) для ДОБАВЛЕННЫХ строк новой версии (TO-сторона).

    Источник — готовый f["text"] из parse_bitbucket_diff. Только ADDED (`[L<n>] +`).
    """
    if not diff_text:
        return []
    return [(int(m.group(1)), m.group(2)) for m in _ADDED_LINE_RE.finditer(diff_text)]


def scan(diff_text: Optional[str], rules: list[Rule]) -> list[dict]:
    """Прогоняет правила по добавленным строкам diff'а.

    Возвращает находки [{line, severity, message}] — БЕЗ маркера и сниппета
    (маркер `[codestyle]` навешивает оркестратор; сниппет не включаем — стабильность
    дедупа, как у perlcritic M4). Несколько правил на одной строке → несколько находок;
    агрегация/лимит — забота оркестратора (как PERLCRITIC_MAX_COMMENTS).
    """
    if not rules:
        return []

    findings: list[dict] = []
    for line_num, code in added_lines_from_diff_text(diff_text):
        for rule in rules:
            if rule.pattern.search(code):
                findings.append({
                    "line": line_num,
                    "severity": rule.severity,
                    "message": rule.message,
                })
    return findings


def load_rules_file(path: str) -> list[Rule]:
    """Читает файл правил с диска и парсит. Единственная I/O-функция модуля.

    Graceful (AES §7.3): нет файла / ошибка чтения → пустой список (слой молчит, ревью
    идёт дальше). Декодирование терпимое (UTF-8 → cp1251 → replace), как load_styleguide:
    файл правят копипастом из Confluence/Windows, кодировка не должна валить ревью.
    """
    if not path:
        return []
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except (FileNotFoundError, IsADirectoryError):
        return []
    except OSError as e:
        log.warning("styleguide: не удалось прочитать %r: %s", path, e)
        return []

    for encoding in ("utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
        log.warning("styleguide: %r не в UTF-8/cp1251 — прочитан с заменой символов", path)

    return parse_rules(text)
