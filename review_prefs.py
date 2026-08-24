"""
Разбор метки `@jarvis` из описания PR — автор выбирает, что бот публикует (spec 016).

Модуль намеренно НИЧЕГО не знает про Bitbucket, Феникс и переменные окружения:
на вход приходит текст описания и дефолтный порог, на выход — решение. Благодаря
этому он покрывается юнит-тестами целиком, без заглушек и сети (образец —
styleguide_rules.py).

Синтаксис метки (spec 016 FR-002):

    @jarvis <уровень> [-<источник> ...]

Уровень — ПОРОГ «и выше», как в уровнях логирования и в шкале самого perlcritic:

    @jarvis errors     -> error
    @jarvis warnings   -> error + warning        (дефолт развёртывания)
    @jarvis all        -> error + warning + suggestion

Минус исключает источник целиком, КРОМЕ его ошибок:

    @jarvis all -codestyle   -> всё, кроме стилевых warning/suggestion;
                                стилевые error всё равно публикуются.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

# Шкала важности. Числа нужны только для сравнения «не ниже порога».
SEVERITY_ORDER = {"suggestion": 1, "warning": 2, "error": 3}

# Слово в метке -> МИНИМАЛЬНАЯ публикуемая важность.
LEVELS = {
    "errors": "error",
    "warnings": "warning",
    "all": "suggestion",
}

# Источники, которыми автор может управлять. Совпадают со значениями поля
# "source" в комментариях бота (см. _inspect_file и сборку замечаний Феникса).
SOURCES = ("perlcritic", "codestyle", "jarvis")

# Источники, не подлежащие фильтрации ни при каких настройках (FR-013).
# impact — это детерминированный факт из графа вызовов («функция вызывается в
# 5 местах»), а не мнение модели. Прятать его по severity бессмысленно: он не
# «замечание», по которому можно спорить, а справка о последствиях правки.
NEVER_FILTERED = ("impact",)

DEFAULT_MARKER = "@jarvis"

# Всё это считаем минусом. Люди готовят описание PR копипастом из Confluence и
# Word, где автозамена превращает дефис в длинное тире. Понять такое дешевле и
# полезнее, чем сообщить об ошибке: автор явно писал исключение источника.
DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"

# Мусор, которым люди обрамляют слова в свободном тексте: «@jarvis all,», «@jarvis all.»
_TRIM = " \t,.;:!?«»\"'()[]"


@dataclass(frozen=True)
class ReviewPrefs:
    """Решение о том, что публиковать. Неизменяемо: собирается один раз на PR."""

    min_severity: str = "warning"
    excluded_sources: frozenset[str] = field(default_factory=frozenset)
    # Слово уровня ровно как его понял бот — для эха в сводке (FR-016).
    level_word: str = "warnings"
    # Нераспознанное слово из метки. Не None => бот работает на дефолте и обязан
    # сказать об этом в сводке (FR-018). Без автодополнения в Bitbucket это
    # единственный способ для автора узнать, что он опечатался.
    unknown_command: Optional[str] = None
    # Метка в описании найдена (иначе действует дефолт развёртывания).
    from_marker: bool = False


def _level_word_for(min_severity: str) -> str:
    """Обратное отображение порога в слово метки — чтобы эхо и дефолт совпадали."""
    for word, sev in LEVELS.items():
        if sev == min_severity:
            return word
    return "warnings"


def _marker_regex(marker: str) -> "re.Pattern[str]":
    # Строка должна НАЧИНАТЬСЯ с метки (FR-001): иначе сработаем на цитате внутри
    # описания или на обсуждении вида «я написал @jarvis all, но не помогло».
    # Хвост после метки — до конца строки, разбирается отдельно.
    return re.compile(
        r"^[ \t]*" + re.escape(marker) + r"(?=[\s,:]|$)[ \t]*(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )


def parse_marker(
    description: Optional[str],
    default_min_severity: str = "warning",
    marker: str = DEFAULT_MARKER,
) -> ReviewPrefs:
    """Разбирает описание PR.

    Метки нет, описание пустое или не строка -> дефолт развёртывания, молча:
    это штатный случай, а не ошибка.

    Метка есть, но слово не распознано -> дефолт + unknown_command (FR-018).
    Никогда не бросает исключений: фильтр — удобство, а сорванное из-за него
    ревью — потеря (AES §7.3).
    """
    # Оба служебных аргумента приходят из кода бота, но докстрока обещает, что
    # функция не бросает — значит и здесь не бросаем, а откатываемся на дефолт.
    if not isinstance(default_min_severity, str):
        default_min_severity = "warning"
    if not isinstance(marker, str) or not marker.strip():
        marker = DEFAULT_MARKER

    default_min_severity = default_min_severity.strip().lower()
    if default_min_severity not in SEVERITY_ORDER:
        default_min_severity = "warning"
    fallback = ReviewPrefs(
        min_severity=default_min_severity,
        level_word=_level_word_for(default_min_severity),
    )

    if not isinstance(description, str) or not description.strip():
        return fallback

    match = _marker_regex(marker).search(description)
    if match is None:
        return fallback

    # Берём ПЕРВУЮ метку. Вторая — почти наверняка цитата или след правок; молча
    # склеивать их значило бы получить настройку, которую автор не писал.
    tail = match.group(1) or ""

    min_severity = default_min_severity
    level_word = _level_word_for(default_min_severity)
    excluded = set()
    unknown: Optional[str] = None
    level_seen = False

    for raw in tail.split():
        token = raw.strip(_TRIM).lower()
        if not token:
            continue

        if token[0] in DASHES:
            name = token[1:].strip(_TRIM)
            if name in SOURCES:
                excluded.add(name)
            elif not name:
                # Одиночное тире — знак препинания, а не команда: «@jarvis all —
                # хочу видеть всё». Жаловаться на него значило бы ругать человека
                # за нормальную русскую пунктуацию.
                pass
            elif unknown is None:
                unknown = token
            continue

        if not level_seen and token in LEVELS:
            min_severity = LEVELS[token]
            level_word = token
            level_seen = True
            continue

        # Первое непонятное слово запоминаем, дальше молчим: «@jarvis all, будь
        # добр» не должно порождать три жалобы. Всё, что после распознанного
        # уровня, — свободный текст (FR-005), он игнорируется.
        if not level_seen and unknown is None:
            unknown = token

    return ReviewPrefs(
        min_severity=min_severity,
        excluded_sources=frozenset(excluded),
        level_word=level_word,
        unknown_command=unknown,
        from_marker=True,
    )


def should_post(prefs: ReviewPrefs, severity: Optional[str], source: Optional[str]) -> bool:
    """Публиковать ли одно замечание.

    Порядок проверок зафиксирован планом и является ЧАСТЬЮ КОНТРАКТА, а не
    деталью реализации:

      1. источник вне фильтрации            -> да   (FR-013)
      2. severity == error                  -> да   (FR-012, сильнее исключения)
      3. источник исключён меткой           -> нет  (FR-004)
      4. severity не ниже порога            -> да   (FR-003)

    Пункт 2 стоит ВЫШЕ пункта 3 намеренно: «-codestyle» убирает у источника
    только warning и suggestion, но не ошибки. Автор не может заглушить
    критичное — ни порогом, ни минусом.

    Неизвестная severity публикуется (fail-open): такие значения приходят от
    модели и скорее означают «важнее обычного» (critical, major), чем «мелочь».
    Спрятать реальную находку хуже, чем показать лишнюю.
    """
    # Регистр приводим ОДИН раз и сравниваем только приведённое: в боте источник
    # модели пишется как "JARVIS", а в метке — строчными. Две разные нормализации
    # внутри одной функции — мина на будущее, даже если сегодня совпадает.
    src = (source or "").strip().lower()
    if src in NEVER_FILTERED:
        return True

    sev = (severity or "").strip().lower()
    if sev == "error":
        return True

    if src in prefs.excluded_sources:
        return False

    rank = SEVERITY_ORDER.get(sev)
    if rank is None:
        return True

    return rank >= SEVERITY_ORDER[prefs.min_severity]


def describe(prefs: ReviewPrefs, hidden_by_level: int = 0, hidden_by_source: int = 0) -> str:
    """Строка для сводки: как бот понял команду и что скрыл (FR-016/017/018).

    Присутствует ВСЕГДА, в том числе когда ничего не скрыто (FR-017): это
    единственное подтверждение автору, что его метку прочитали, и единственная
    подсказка тому, кто метку не ставил.

    Текст обязан быть СТАБИЛЬНЫМ между прогонами при неизменном PR — он попадает
    в ключ дедупликации сводки. Поэтому здесь нет ничего плавающего: только
    слово уровня, отсортированный список источников и счётчики находок.
    """
    included = " + ".join(
        s for s, rank in sorted(SEVERITY_ORDER.items(), key=lambda kv: -kv[1])
        if rank >= SEVERITY_ORDER[prefs.min_severity]
    )
    parts = [f"**Фильтр:** {prefs.level_word} ({included})"]

    if prefs.excluded_sources:
        parts.append("исключено: " + ", ".join(sorted(prefs.excluded_sources)))

    hidden = (hidden_by_level or 0) + (hidden_by_source or 0)
    if hidden:
        parts.append(f"скрыто {hidden} замечаний")
    else:
        parts.append("скрытых замечаний нет")

    line = ". ".join(parts) + "."

    if prefs.unknown_command:
        # Называем слово прямым текстом: без автодополнения в Bitbucket автор
        # иначе не узнает, что опечатался, и будет ждать ревью, которого не будет.
        line += (
            f"\n_Команду «{prefs.unknown_command}» не понял, работаю на уровне "
            f"{prefs.level_word}._"
        )

    if hidden:
        line += "\n_Показать всё — строка «@jarvis all» в описании PR._"

    return line
