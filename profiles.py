"""
Профили стеков (spec 022): какой промпт и какие слои применять к файлу PR.

Профиль считается ДЛЯ КАЖДОГО ФАЙЛА: вебхук Bitbucket вешается на репозиторий
целиком, а в монорепо в одном PR встречаются Perl, фронт и что угодно ещё.

  perl       — Perl-промпт, стайлгайд, perlcritic, impact
  perl-lite  — то же без impact (граф вызовов один и не знает репозиторий)
  generic    — нейтральный промпт без линтеров
  skip       — файл в ревью не идёт (lock-файлы, бандлы, бинарники)

Модуль на стандартной библиотеке: новые pip-зависимости — это внесение в контур.
"""

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("jarvis-pr-review")

PERL = "perl"
PERL_LITE = "perl-lite"
GENERIC = "generic"
SKIP = "skip"
PROFILES = (PERL, PERL_LITE, GENERIC, SKIP)
PERL_PROFILES = (PERL, PERL_LITE)

PERL_EXTENSIONS = (".pl", ".pm", ".t")

REASON_DEFAULT = "по умолчанию"
REASON_EXTENSION = "расширение"
REASON_SHEBANG = "shebang"
REASON_MINIFIED = "похоже на минифицированный/сгенерированный файл"

STATUS_ABSENT = "absent"
STATUS_OK = "ok"
STATUS_ERROR = "error"

# Эвристика минификации (FR-011): одна длинная строка (SQL, base64 в тесте) при
# нормальной средней длине файл НЕ пропускает — нужна ещё и большая средняя.
MINIFIED_MAX_LINE = 1000
MINIFIED_AVG_LINE = 300
MINIFIED_MIN_LINES = 3

GLOB_MAX_LEN = 200
GLOB_MAX_STARS = 8

BUILTIN_SKIP_FILES = (
    "*.lock", "*-lock.json", "*.lock.json", "npm-shrinkwrap.json", "go.sum",
    "*.min.js", "*.min.css", "*.map", "*.snap",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.svg", "*.pdf",
    "*.woff", "*.woff2", "*.ttf", "*.eot",
    "*.zip", "*.tar", "*.gz", "*.jar", "*.war", "*.class", "*.so", "*.dll", "*.exe",
)
# Каталоги — на любой глубине. К Perl по расширению не применяются: Perl-код в
# каталоге `vendor/` или `build/` терять нельзя.
BUILTIN_SKIP_DIRS = (
    "**/node_modules/**", "**/vendor/**", "**/dist/**", "**/build/**",
    "**/target/**", "**/__pycache__/**",
)

_ADDED_LINE_RE = re.compile(r"^\[L\d+\] \+(.*)$", re.MULTILINE)
_FIRST_LINE_RE = re.compile(r"^\[L1\] [+ ](.*)$", re.MULTILINE)


class ConfigError(Exception):
    """Конфиг профилей не прошёл проверку схемы."""


# ── Расширение и glob ──────────────────────────────────────

def file_ext(path: str) -> str:
    """Расширение файла в нижнем регистре с точкой, либо "".

    Точка в начале имени (`.bashrc`) расширением не считается. Одна функция на
    все места (выбор профиля, промпт), чтобы они не разошлись в определениях.
    """
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    return name[dot:].lower()


def _glob_to_regex(pattern: str) -> str:
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == n:
            out.append("/.*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class Glob:
    """Шаблон пути (FR-015).

    Без `/` — сравнивается с именем файла на любой глубине; с `/` — с полным
    путём от корня репо. `**` — любые каталоги, `*` и `?` не переходят `/`.
    """
    pattern: str
    regex: "re.Pattern[str]" = field(compare=False)
    basename_only: bool = field(compare=False)

    @classmethod
    def compile(cls, pattern: object) -> "Glob":
        if not isinstance(pattern, str) or not pattern:
            raise ConfigError(f"шаблон должен быть непустой строкой: {pattern!r}")
        if len(pattern) > GLOB_MAX_LEN:
            raise ConfigError(f"шаблон длиннее {GLOB_MAX_LEN} символов: {pattern[:40]!r}…")
        if pattern.count("*") > GLOB_MAX_STARS:
            raise ConfigError(f"в шаблоне больше {GLOB_MAX_STARS} символов '*': {pattern!r}")
        return cls(
            pattern=pattern,
            regex=re.compile(_glob_to_regex(pattern), re.DOTALL),
            basename_only="/" not in pattern,
        )

    def matches(self, path: str) -> bool:
        target = path.rsplit("/", 1)[-1] if self.basename_only else path
        return self.regex.fullmatch(target) is not None


_BUILTIN_FILE_GLOBS = tuple(Glob.compile(p) for p in BUILTIN_SKIP_FILES)
_BUILTIN_DIR_GLOBS = tuple(Glob.compile(p) for p in BUILTIN_SKIP_DIRS)


# ── Конфиг ─────────────────────────────────────────────────

@dataclass(frozen=True)
class RepoConfig:
    perl_profile: str = PERL_LITE
    paths: tuple[tuple[Glob, str], ...] = ()
    enabled: bool = True


DEFAULT_REPO = RepoConfig()


@dataclass(frozen=True)
class ProfilesConfig:
    skip: tuple[Glob, ...] = ()
    repos: dict[str, RepoConfig] = field(default_factory=dict)

    def repo(self, repo_key: str) -> RepoConfig:
        """Настройки репо по ключу `PROJECT/slug`; нет в конфиге — умолчания."""
        return self.repos.get(repo_key.lower(), DEFAULT_REPO)


DEFAULT_CONFIG = ProfilesConfig()

_ROOT_KEYS = {"skip", "repos"}
_REPO_KEYS = {"perl_profile", "paths", "enabled"}
_PATH_KEYS = {"glob", "profile"}


def _check_keys(obj: object, allowed: set[str], where: str) -> dict:
    if not isinstance(obj, dict):
        raise ConfigError(f"{where}: ожидается объект")
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ConfigError(f"{where}: неизвестные поля {unknown}")
    return obj


def _parse_repo(key: str, raw: object) -> RepoConfig:
    where = f"repos.{key}"
    obj = _check_keys(raw, _REPO_KEYS, where)

    perl_profile = obj.get("perl_profile", PERL_LITE)
    if perl_profile not in PERL_PROFILES:
        raise ConfigError(f"{where}.perl_profile: ожидается {list(PERL_PROFILES)}, получено {perl_profile!r}")

    enabled = obj.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{where}.enabled: ожидается true/false, получено {enabled!r}")

    raw_paths = obj.get("paths", [])
    if not isinstance(raw_paths, list):
        raise ConfigError(f"{where}.paths: ожидается список")
    paths: list[tuple[Glob, str]] = []
    for i, rule in enumerate(raw_paths):
        rule_where = f"{where}.paths[{i}]"
        rule_obj = _check_keys(rule, _PATH_KEYS, rule_where)
        if set(rule_obj) != _PATH_KEYS:
            raise ConfigError(f"{rule_where}: нужны ровно поля {sorted(_PATH_KEYS)}")
        profile = rule_obj["profile"]
        if profile not in PROFILES:
            raise ConfigError(f"{rule_where}.profile: ожидается {list(PROFILES)}, получено {profile!r}")
        # perl включает impact; без индекса этого репо impact покажет чужие места вызова.
        if profile == PERL and perl_profile != PERL:
            raise ConfigError(
                f"{rule_where}.profile: 'perl' допустим только при perl_profile 'perl' у репо"
            )
        paths.append((Glob.compile(rule_obj["glob"]), profile))

    return RepoConfig(perl_profile=perl_profile, paths=tuple(paths), enabled=enabled)


def parse_config(data: object) -> ProfilesConfig:
    """Проверяет схему (spec 022 FR-020) и строит конфиг. Ошибка — ConfigError."""
    obj = _check_keys(data, _ROOT_KEYS, "корень")

    raw_skip = obj.get("skip", [])
    if not isinstance(raw_skip, list):
        raise ConfigError("skip: ожидается список шаблонов")
    skip = tuple(Glob.compile(p) for p in raw_skip)

    raw_repos = obj.get("repos", {})
    if not isinstance(raw_repos, dict):
        raise ConfigError("repos: ожидается объект")
    repos: dict[str, RepoConfig] = {}
    for key, raw in raw_repos.items():
        lowered = key.lower()
        if lowered in repos:
            raise ConfigError(f"repos: ключи, различающиеся только регистром: {key!r}")
        repos[lowered] = _parse_repo(key, raw)

    return ProfilesConfig(skip=skip, repos=repos)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ConfigError(f"повторяющийся ключ {key!r}")
        seen[key] = value
    return seen


def default_path() -> str:
    """Путь к конфигу: ENV `PROFILES_PATH` либо `profiles.json` рядом с модулем.

    НЕ текущий каталог: CLI запускается из клона проверяемого репозитория, и
    относительный путь подхватил бы конфиг, который пишет автор PR.
    """
    env = os.getenv("PROFILES_PATH", "").strip()
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles.json")


_lock = threading.Lock()
_last_good: Optional[ProfilesConfig] = None
_status = STATUS_ABSENT


def status() -> str:
    """Состояние последней загрузки: absent | ok | error (для /health и dry-run)."""
    with _lock:
        return _status


def _read(path: str) -> Optional[ProfilesConfig]:
    """None — файла нет (штатно). ConfigError — файл есть, но битый."""
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        raise ConfigError(f"PROFILES_PATH: ожидается абсолютный путь, получено {path!r}")
    resolved = os.path.realpath(expanded)
    if os.path.isdir(resolved):
        # Docker при монтировании отсутствующего файла создаёт на его месте каталог.
        log.warning(f"⚠️ PROFILES_PATH: {resolved!r} — каталог, а не файл — профили по умолчанию")
        return None
    if not os.path.isfile(resolved):
        return None
    try:
        with open(resolved, encoding="utf-8") as fh:
            data = json.load(fh, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ConfigError(f"{resolved}: не читается как JSON ({type(e).__name__}: {e})") from e
    return parse_config(data)


def load(path: Optional[str] = None) -> ProfilesConfig:
    """Загружает конфиг. Не бросает исключений: бот без конфига работает на умолчаниях.

    Битый файл НЕ сбрасывает настройки на умолчания, если до этого была успешная
    загрузка: опечатка не должна снимать `enabled: false` с выключенных репо.
    """
    global _last_good, _status
    target = path if path is not None else default_path()
    try:
        config = _read(target)
    except ConfigError as e:
        with _lock:
            _status = STATUS_ERROR
            fallback = _last_good
        log.error(
            f"❌ profiles: {e} — "
            + ("работаю на последнем исправном конфиге" if fallback else "профили по умолчанию")
        )
        return fallback or DEFAULT_CONFIG

    with _lock:
        previous = _status
        if config is None:
            _status = STATUS_ABSENT
            _last_good = None
        else:
            _status = STATUS_OK
            _last_good = config
    # Конфиг читается на каждый вебхук и PR — пишем только смену состояния, не каждый раз.
    if config is None:
        if previous != STATUS_ABSENT:
            log.info(f"ℹ️ PROFILES_PATH: файл не найден ({target}) — профили по умолчанию")
        return DEFAULT_CONFIG
    if previous != STATUS_OK:
        log.info(f"✅ profiles: конфиг загружен ({target})")
    return config


# ── Выбор профиля ──────────────────────────────────────────

def is_minified(diff_text: str) -> bool:
    """Похож ли файл на минифицированный/сгенерированный по добавленным строкам diff."""
    lengths = [len(m.group(1)) for m in _ADDED_LINE_RE.finditer(diff_text or "")]
    if not lengths:
        return False
    avg = sum(lengths) / len(lengths)
    if avg <= MINIFIED_AVG_LINE:
        return False
    return max(lengths) > MINIFIED_MAX_LINE or len(lengths) >= MINIFIED_MIN_LINES


def pre_profile(
    path: str, diff_text: str, repo_cfg: RepoConfig, config: ProfilesConfig,
) -> tuple[str, str]:
    """Профиль файла БЕЗ загрузки файла из Bitbucket (FR-002). → (profile, reason).

    Первое совпадение побеждает. Файлы без расширения здесь получают generic и
    уточняются по shebang после загрузки (`refine_by_shebang`).
    """
    for glob in config.skip:
        if glob.matches(path):
            return SKIP, f"шаблон {glob.pattern}"

    perl_by_ext = file_ext(path) in PERL_EXTENSIONS
    for glob in _BUILTIN_FILE_GLOBS:
        if glob.matches(path):
            return SKIP, f"шаблон {glob.pattern}"
    if not perl_by_ext:
        for glob in _BUILTIN_DIR_GLOBS:
            if glob.matches(path):
                return SKIP, f"шаблон {glob.pattern}"

    for glob, profile in repo_cfg.paths:
        if glob.matches(path):
            return profile, f"правило папки {glob.pattern}"

    if perl_by_ext:
        return repo_cfg.perl_profile, REASON_EXTENSION

    if is_minified(diff_text):
        return SKIP, REASON_MINIFIED

    return GENERIC, REASON_DEFAULT


def _first_line(raw_code: Optional[str], diff_text: str) -> Optional[str]:
    if raw_code is not None:
        return raw_code.split("\n", 1)[0]
    m = _FIRST_LINE_RE.search(diff_text or "")
    return m.group(1) if m else None


def refine_by_shebang(
    profile: str, reason: str, path: str,
    raw_code: Optional[str], diff_text: str, repo_cfg: RepoConfig,
) -> tuple[str, str]:
    """Уточняет generic-по-умолчанию для файла без расширения по shebang (FR-006).

    Явные решения (правило папки, skip) не трогает.
    """
    if profile != GENERIC or reason != REASON_DEFAULT or file_ext(path):
        return profile, reason
    first = _first_line(raw_code, diff_text)
    if first is not None and first.startswith("#!") and "perl" in first:
        return repo_cfg.perl_profile, REASON_SHEBANG
    return profile, reason
