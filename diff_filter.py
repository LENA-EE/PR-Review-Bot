"""
Diff-фильтрация нарушений perlcritic (ARCHITECTURE.md §2.9).

perlcritic мыслит координатами ВСЕГО файла, а ревью живёт в координатах diff.
Чтобы не вешать замечания на чужой/немодифицированный код, нарушения пересекаются
с множеством строк, которые PR реально ДОБАВИЛ (new-side / TO-координаты).

Источник множества тронутых строк — уже готовый f["text"] из parse_bitbucket_diff:
добавленные строки помечены `[L<n>] +`, контекстные — `[L<n>] ` (без `+`).

BLOCKER-фикс B1: берём ТОЛЬКО добавленные строки (с `+` сразу после метки).
Если ловить просто `[L<n>]`, в множество попадут контекстные (немодифицированные)
строки — и нарушения на чужом коде прилипнут к автору. Якорь на `+` это исключает.

Чистый модуль без I/O — легко тестируется.
"""

import re
from typing import Optional

# Метка ставится в начале строки f["text"]; `+` сразу после `] ` = добавленная строка.
# CONTEXT-строки имеют вид `[L<n>] <код>` (пробел вместо `+`) и СЮДА НЕ попадают.
_ADDED_LINE_RE = re.compile(r"^\[L(\d+)\] \+", re.MULTILINE)


def changed_lines_from_diff_text(diff_text: str) -> set[int]:
    """Множество номеров ДОБАВЛЕННЫХ строк новой версии (TO-сторона).

    Только ADDED-строки (`[L<n>] +`). CONTEXT-строки (`[L<n>] ` без `+`) игнорируются.
    diff JSON повторно не парсим — переиспользуем destination-номер из меток.
    """
    if not diff_text:
        return set()
    return {int(m.group(1)) for m in _ADDED_LINE_RE.finditer(diff_text)}


def _issue_line(issue: dict) -> Optional[int]:
    """Достаёт номер строки из нарушения perlcritic, терпимо к кривым значениям."""
    try:
        return int(issue.get("line"))
    except (TypeError, ValueError):
        return None


def filter_issues_by_lines(
    issues: list[dict],
    changed: set[int],
) -> tuple[list[dict], list[dict]]:
    """Делит нарушения perlcritic на (on_diff, pre_existing).

    on_diff      — нарушение на строке, которую PR добавил (issue['line'] ∈ changed).
                   Только их разрешено вешать инлайн на автора (§2.9).
    pre_existing — всё остальное (чужой/немодифицированный код).

    Решение Human Architect (2026-06-17): pre_existing в MVP НЕ комментируем вообще
    (ни инлайн, ни сводкой). Функция всё равно возвращает оба списка — для тестов и
    на случай включения сводки позже (PERLCRITIC_PREEXISTING_SUMMARY). Оркестратор
    pre_existing просто отбрасывает.

    Нарушение с непреобразуемым/отсутствующим line уходит в pre_existing (не вешаем
    на автора то, что не смогли надёжно привязать к строке).
    """
    on_diff: list[dict] = []
    pre_existing: list[dict] = []
    for issue in issues:
        line = _issue_line(issue)
        if line is not None and line in changed:
            on_diff.append(issue)
        else:
            pre_existing.append(issue)
    return on_diff, pre_existing
