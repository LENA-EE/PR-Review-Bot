"""
Извлечение имён изменённых функций из готового diff-текста (FR-009, импакт-анализ).

Зачем: чтобы спросить у mcp-drospr `get_callers` «кто зовёт эти функции» и предупредить
о поломке вызывающих мест (возможно вне diff).

Источник — `f["text"]` из parse_bitbucket_diff. Берём `sub <имя>` среди ИЗМЕНЁННЫХ строк:
  • ДОБАВЛЕННЫХ  — `[L<n>] +...`  (или `+...` без метки);
  • УДАЛЁННЫХ    — `       -...`  (ведущие пробелы + `-`).
Обе стороны нужны: при ПЕРЕИМЕНОВАНИИ старое имя (у которого есть существующие callers,
которые сломаются) живёт только на удалённой строке.

Чистый модуль: только разбор строки, без I/O.
"""

import re
from typing import Optional

# Добавленная строка: опц. метка [L<n>] + пробел, затем '+'.
_ADDED_RE = re.compile(r"^(?:\[L\d+\]\s)?\+(.*)$")
# Удалённая строка: ведущие пробелы, затем '-' (parse_bitbucket_diff пишет 7 пробелов).
_REMOVED_RE = re.compile(r"^\s+-(.*)$")
# Объявление сабрутины.
_SUB_RE = re.compile(r"\bsub\s+(\w+)")


def changed_subs_from_diff_text(diff_text: Optional[str]) -> set[str]:
    """Множество имён сабов, объявления которых затронуты диффом (добавлены или удалены)."""
    names: set[str] = set()
    if not diff_text:
        return names

    for line in diff_text.splitlines():
        m = _ADDED_RE.match(line)
        if m:
            body = m.group(1)
        else:
            m = _REMOVED_RE.match(line)
            if not m:
                continue
            body = m.group(1)

        sub = _SUB_RE.search(body)
        if sub:
            names.add(sub.group(1))

    return names
