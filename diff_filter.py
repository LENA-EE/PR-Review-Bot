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

Здесь же (spec 011) живут две функции режима «полный файл как контекст»: разметка
файла теми же метками, что и diff, и сверка номеров строк файла с диффом.
Общий знаменатель модуля — координаты изменённых строк, кто бы их ни спрашивал.

Чистый модуль без I/O — легко тестируется.
"""

import re
from typing import Optional

# Метка ставится в начале строки f["text"]; `+` сразу после `] ` = добавленная строка.
# CONTEXT-строки имеют вид `[L<n>] <код>` (пробел вместо `+`) и СЮДА НЕ попадают.
_ADDED_LINE_RE = re.compile(r"^\[L(\d+)\] \+", re.MULTILINE)

# То же самое, но с захватом самого кода — нужен для сверки полного файла с диффом
# (render_file_with_diff_marks / verify_file_matches_diff). Отдельный шаблон, а не
# доработка _ADDED_LINE_RE: тот используется в горячем пути и должен остаться дешёвым.
_ADDED_LINE_WITH_TEXT_RE = re.compile(r"^\[L(\d+)\] \+(.*)$", re.MULTILINE)


def changed_lines_from_diff_text(diff_text: str) -> set[int]:
    """Множество номеров ДОБАВЛЕННЫХ строк новой версии (TO-сторона).

    Только ADDED-строки (`[L<n>] +`). CONTEXT-строки (`[L<n>] ` без `+`) игнорируются.
    diff JSON повторно не парсим — переиспользуем destination-номер из меток.
    """
    if not diff_text:
        return set()
    return {int(m.group(1)) for m in _ADDED_LINE_RE.finditer(diff_text)}


def added_lines_with_text(diff_text: Optional[str]) -> list[tuple[int, str]]:
    """Добавленные строки как (номер, код) — для сверки диффа с полным файлом."""
    if not diff_text:
        return []
    return [(int(m.group(1)), m.group(2)) for m in _ADDED_LINE_WITH_TEXT_RE.finditer(diff_text)]


def to_line_number(value) -> Optional[int]:
    """Номер строки из недоверенного источника → int или None.

    Модель возвращает в поле "line" что угодно: число, строку "42", "42-45",
    "unknown", None. Приводим терпимо; всё, что не приводится, → None (вызывающий
    решает, что с этим делать — потерять привязку, но не потерять само замечание).
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _issue_line(issue: dict) -> Optional[int]:
    """Достаёт номер строки из нарушения perlcritic, терпимо к кривым значениям."""
    return to_line_number(issue.get("line"))


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


# ── Полный файл как контекст ревью (spec 011) ───────────────────────────────
# Ниже — две чистые функции для режима REVIEW_CONTEXT_MODE=file: модели показывают
# файл ЦЕЛИКОМ, а изменённые строки помечают, чтобы она ревьюила только их.
# Живут здесь, а не в отдельном модуле, сознательно: diff_filter уже деплоится в банк
# и уже отвечает за «координаты diff'а», а лишний файл в ручном копипаст-деплое —
# это риск «забыли скопировать» с тихой деградацией целого слоя.


def render_file_with_diff_marks(file_text: Optional[str], changed: set[int]) -> str:
    """Полный текст файла с построчными метками — в том же формате, что и diff.

    ВАЖНО (иначе тихий баг): формат обязан совпадать с parse_bitbucket_diff:
      • добавленная строка → `[L<n>] +<код>`  (ОДИН пробел, затем `+`)
      • прочая строка      → `[L<n>]  <код>`  (ДВА пробела: метка + маркер-пробел)
    Если у контекстной строки оставить один пробел, то строка файла, начинающаяся
    с `+` (продолжение выражения, here-doc, литерал — в Perl это обычное дело),
    будет распознана и моделью, и _ADDED_LINE_RE как добавленная, и замечание
    прилипнет к чужому коду.
    """
    if not file_text:
        return ""
    lines = file_text.split("\n")
    return "\n".join(
        f"[L{num}] {'+' if num in changed else ' '}{code}"
        for num, code in enumerate(lines, start=1)
    )


def verify_file_matches_diff(
    file_text: Optional[str],
    diff_text: Optional[str],
) -> tuple[bool, str]:
    """Сверяет: номера строк диффа действительно указывают на эти строки файла.

    Зачем: разметка полного файла целиком держится на допущении «номер из diff'а ==
    номер строки в файле на fromRef.latestCommit». Допущение ломается, если между
    запросом diff'а и загрузкой файла в ветку что-то запушили, либо если файл
    декодировался/нормализовался иначе, чем его отдал Bitbucket. Цена ошибки —
    ВСЕ замечания встанут на чужие строки, поэтому проверяем до отправки в модель.

    Сверяем ВСЕ добавленные строки, а не выборку: в Perl слишком много одинаковых
    строк (`}`, `);`, пустых), и сдвиг на одну-две строки выборка не заметит.
    Сравнение после rstrip — хвостовые пробелы не значимы и разъезжаются
    при нормализации переводов строк.

    Возвращает (совпало, причина_расхождения). Пустой дифф/файл → (False, причина):
    сверять нечего, а значит нельзя и утверждать, что разметка корректна.
    """
    if not file_text:
        return False, "пустой файл"
    added = added_lines_with_text(diff_text)
    if not added:
        return False, "в диффе нет добавленных строк"

    file_lines = file_text.split("\n")
    total = len(file_lines)
    for num, code in added:
        if num < 1 or num > total:
            return False, f"строка {num} за пределами файла ({total} строк)"
        if file_lines[num - 1].rstrip() != code.rstrip():
            return False, f"строка {num} в файле не совпала со строкой из диффа"
    return True, ""
