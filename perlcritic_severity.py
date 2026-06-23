"""
Маппинг severity perlcritic → severity бота.

ВНИМАНИЕ: у perlcritic в mcp-drospr шкала ИНВЕРТИРОВАНА относительно интуиции
(проверено по коду mcp-drospr v1.0.0):
    1 = самый строгий набор (gentle, докладывает все нарушения)
    5 = только критические (severe)
То есть чем БОЛЬШЕ число — тем КРИТИЧНЕЕ нарушение.

Бот же показывает разработчику три уровня: error / warning / suggestion.
Этот модуль — единственное место, где живёт направление маппинга, чтобы его
нельзя было перепутать «по месту» (ARCHITECTURE.md §2.10, предусловие 3).

Чистая функция без I/O — пороги приходят аргументами (их читает оркестратор из ENV
PERLCRITIC_SEVERITY_ERROR_MIN / PERLCRITIC_SEVERITY_WARNING_MIN), чтобы тюнить без правки кода.
"""

from typing import Union

# Дефолты порогов — совпадают с ENV-дефолтами плана (§4).
DEFAULT_ERROR_MIN = 4      # severity >= 4  → error  (4-5 = серьёзно/критично)
DEFAULT_WARNING_MIN = 2    # severity >= 2  → warning (ниже → suggestion)

ERROR = "error"
WARNING = "warning"
SUGGESTION = "suggestion"


def to_bot_severity(
    perlcritic_severity: Union[int, str, None],
    error_min: int = DEFAULT_ERROR_MIN,
    warning_min: int = DEFAULT_WARNING_MIN,
) -> str:
    """Переводит severity perlcritic (1..5, инвертированная) в уровень бота.

    severity >= error_min   → "error"
    severity >= warning_min → "warning"
    иначе                   → "suggestion"

    Некорректное/непреобразуемое значение (None, "abc") трактуем как самый мягкий
    уровень "suggestion" — не валим ревью из-за кривого поля (AES §7.3).
    """
    try:
        sev = int(perlcritic_severity)
    except (TypeError, ValueError):
        return SUGGESTION

    if sev >= error_min:
        return ERROR
    if sev >= warning_min:
        return WARNING
    return SUGGESTION
