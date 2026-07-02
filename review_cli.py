"""
JARVIS PR Review — CLI-переходник
=================================
Запуск ревью Pull Request из командной строки, БЕЗ входящего вебхука.

Зачем это нужно (TeamCity-путь):
  Входящий вебхук Bitbucket → бот требует открытия порта/ОТАР-заявки. Вместо
  этого мы едем по уже работающему ИСХОДЯЩЕМУ пути: TeamCity по SSH заходит на
  препрод-хост и дёргает этот скрипт. Скрипт делает ровно то же, что делал бы
  webhook-эндпоинт: собирает аргументы PR и зовёт чистое ядро
  `pr_review_bot.review_pull_request(...)`.

Что делает скрипт:
  1. Разбирает аргументы (номер PR напрямую ИЛИ имя ветки-источника).
  2. При режиме --branch сам находит открытый PR по ветке через Bitbucket API.
  3. Дотягивает FROM-сторону (fromRef) — нужна perlcritic/impact слоям.
  4. Вызывает ядро ревью бота (тянет diff, гоняет анализ, постит комментарии).

Конфигурация — только через ENV (см. pr_review_bot.py: BITBUCKET_URL,
BITBUCKET_TOKEN, FENIX_URL/TOKEN, MCP_DROSPR_URL, PERLCRITIC_ENABLED,
IMPACT_ENABLED и т.д.). Токены в аргументах командной строки НЕ передаются и
НИКОГДА не логируются.

Примеры запуска:
  # Номер PR известен TeamCity (включена фича Pull Requests в Bitbucket):
  python3 review_cli.py --project MYPROJ --repo my-repo --pr-id 42

  # Известна только ветка — скрипт сам найдёт открытый PR по этой ветке:
  python3 review_cli.py --project MYPROJ --repo my-repo --branch feature/login

  # project/repo можно не указывать, если заданы ENV BITBUCKET_PROJECT / BITBUCKET_REPO:
  BITBUCKET_PROJECT=MYPROJ BITBUCKET_REPO=my-repo python3 review_cli.py --pr-id 42

Коды возврата:
  0 — успех (в т.ч. «открытого PR по ветке нет — ревьюить нечего»);
  1 — реальная ошибка (нет токенов/сеть/невалидные аргументы/сбой Bitbucket API).
"""

import os
import sys
import argparse
import logging

# ВНИМАНИЕ: тяжёлые/сторонние импорты (requests, pr_review_bot→fastapi) делаются
# ЛЕНИВО — внутри функций, после разбора argparse. Это позволяет `--help` и
# валидации аргументов работать даже там, где сторонние пакеты ещё не установлены,
# а при их отсутствии в рабочем режиме — выдать понятную ошибку, а не трейсбек.

# Директорию самого скрипта кладём в sys.path ПЕРВОЙ, чтобы `import pr_review_bot`
# и его внутренние `import mcp_client` и т.п. находились независимо от того, из
# какого cwd TeamCity запустит скрипт по SSH (cwd там может быть любым).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis-review-cli")

# Таймаут маленьких запросов CLI к Bitbucket (список PR / детали PR). В секундах.
# Отдельно от таймаутов ядра бота — здесь только лёгкие GET-и.
BITBUCKET_CLI_TIMEOUT = int(os.getenv("BITBUCKET_CLI_TIMEOUT", "30"))


def parse_args(argv: "list[str] | None" = None) -> argparse.Namespace:
    """Разбирает аргументы командной строки.

    Взаимоисключающие режимы получения PR: --pr-id или --branch (ровно один).
    project/repo обязательны, но допускают fallback из ENV BITBUCKET_PROJECT /
    BITBUCKET_REPO (удобно, если TeamCity уже держит их как параметры сборки).
    """
    parser = argparse.ArgumentParser(
        prog="review_cli.py",
        description=(
            "Запуск JARVIS PR-ревью из CLI (замена входящего вебхука для TeamCity: "
            "SSH на препрод -> этот скрипт)."
        ),
    )

    parser.add_argument(
        "--project",
        default=os.getenv("BITBUCKET_PROJECT"),
        help=(
            "Ключ проекта Bitbucket (TO-сторона, куда мёржим и куда постим). "
            "По умолчанию берётся из ENV BITBUCKET_PROJECT."
        ),
    )
    parser.add_argument(
        "--repo",
        default=os.getenv("BITBUCKET_REPO"),
        help=(
            "Slug репозитория Bitbucket (TO-сторона). "
            "По умолчанию берётся из ENV BITBUCKET_REPO."
        ),
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--pr-id",
        type=int,
        help="Номер Pull Request (когда TeamCity знает номер PR напрямую).",
    )
    mode.add_argument(
        "--branch",
        help=(
            "Имя ветки-источника (source/fromRef). Скрипт сам найдёт открытый PR, "
            "у которого эта ветка является источником."
        ),
    )

    args = parser.parse_args(argv)

    # project/repo обязательны по смыслу, но могут прийти из ENV — проверяем вручную,
    # чтобы дать понятную ошибку (а не «required» на аргументе, который мог быть в ENV).
    missing = []
    if not args.project:
        missing.append("--project (или ENV BITBUCKET_PROJECT)")
    if not args.repo:
        missing.append("--repo (или ENV BITBUCKET_REPO)")
    if missing:
        parser.error("не заданы обязательные параметры: " + ", ".join(missing))

    return args


def _resolve_pr_by_branch(bitbucket_url: str, headers: dict, project: str,
                          repo: str, branch: str) -> "dict | None":
    """Находит открытый PR, у которого `branch` является источником (fromRef).

    direction=OUTGOING + at=refs/heads/<branch> = PR, где ветка — ИСТОЧНИК.
    Возвращает объект PR (для переиспользования fromRef) или None, если PR нет.
    Бросает requests-исключения наверх — обрабатываются в main().
    """
    import requests
    url = (
        f"{bitbucket_url}/rest/api/1.0"
        f"/projects/{project}/repos/{repo}/pull-requests"
    )
    params = {
        "state": "OPEN",
        "direction": "OUTGOING",
        "at": f"refs/heads/{branch}",
    }
    resp = requests.get(
        url, headers=headers, params=params,
        timeout=BITBUCKET_CLI_TIMEOUT, verify=False,
    )
    resp.raise_for_status()
    values = resp.json().get("values", []) or []

    if not values:
        return None

    if len(values) > 1:
        # Несколько открытых PR с одной ветки-источника — берём самый свежий по
        # updatedDate (fallback createdDate), чтобы поведение было детерминированным.
        log.warning(
            "По ветке %s найдено %d открытых PR — беру самый свежий.",
            branch, len(values),
        )
        values = sorted(
            values,
            key=lambda pr: pr.get("updatedDate") or pr.get("createdDate") or 0,
            reverse=True,
        )
    return values[0]


def _get_pr_details(bitbucket_url: str, headers: dict, project: str,
                    repo: str, pr_id: int) -> dict:
    """Тянет детали PR (нужен fromRef для perlcritic/impact).

    Бросает requests-исключения наверх — обрабатываются в main().
    """
    import requests
    url = (
        f"{bitbucket_url}/rest/api/1.0"
        f"/projects/{project}/repos/{repo}/pull-requests/{pr_id}"
    )
    resp = requests.get(
        url, headers=headers,
        timeout=BITBUCKET_CLI_TIMEOUT, verify=False,
    )
    resp.raise_for_status()
    return resp.json()


def _extract_source_side(pr: dict) -> "tuple[str | None, str | None, str | None]":
    """Достаёт FROM-сторону из объекта PR для perlcritic/impact.

    Возвращает (source_project, source_repo, source_ref), где:
      source_ref     = fromRef.latestCommit (КОММИТ-ХЕШ, не displayId);
      source_repo    = fromRef.repository.slug;
      source_project = fromRef.repository.project.key.
    Если fromRef неполный — соответствующие значения None (слой деградирует, это ок).
    """
    from_ref = pr.get("fromRef", {}) or {}
    source_ref = from_ref.get("latestCommit")
    repo_obj = from_ref.get("repository", {}) or {}
    source_repo = repo_obj.get("slug")
    source_project = (repo_obj.get("project", {}) or {}).get("key")
    return source_project, source_repo, source_ref


def main(argv: "list[str] | None" = None) -> int:
    args = parse_args(argv)

    # Ленивый импорт: pr_review_bot тянет fastapi и читает ENV при импорте.
    # Держим импорт ЗДЕСЬ, после argparse, чтобы `--help` и валидация аргументов
    # работали даже без тяжёлых зависимостей, а при их отсутствии — понятная ошибка.
    try:
        import requests
        import pr_review_bot
    except ImportError as e:
        log.error(
            "Не удалось импортировать зависимости (%s: %s). "
            "Проверь, что на хосте установлены пакеты requests и fastapi и "
            "что review_cli.py лежит рядом с pr_review_bot.py.",
            type(e).__name__, e,
        )
        return 1

    # Токены проверяем ДО сетевых запросов — быстрый и понятный отказ.
    if not pr_review_bot.BITBUCKET_TOKEN:
        log.error("Не задан BITBUCKET_TOKEN — ревью невозможно. Заведи секьюр-параметр в TeamCity.")
        return 1

    bitbucket_url = pr_review_bot.BITBUCKET_URL
    headers = pr_review_bot.bb_headers()
    project = args.project
    repo = args.repo

    # ── Определяем pr_id и (по возможности) сразу объект PR ──
    pr_details: "dict | None" = None
    try:
        if args.branch is not None:
            pr_details = _resolve_pr_by_branch(
                bitbucket_url, headers, project, repo, args.branch,
            )
            if pr_details is None:
                # Открытого PR по ветке нет — это НЕ ошибка, ревьюить просто нечего.
                log.info(
                    "По ветке %s нет открытого PR — ревьюить нечего, выхожу.",
                    args.branch,
                )
                return 0
            pr_id = pr_details.get("id")
            if pr_id is None:
                log.error("Bitbucket вернул PR без поля id — не могу продолжить.")
                return 1
            log.info("По ветке %s найден открытый PR #%s.", args.branch, pr_id)
        else:
            pr_id = args.pr_id
            # Детали нужны для FROM-стороны (fromRef).
            pr_details = _get_pr_details(bitbucket_url, headers, project, repo, pr_id)
    except requests.exceptions.RequestException as e:
        # Сеть/HTTP-ошибка Bitbucket — реальная ошибка, ненулевой код.
        # НЕ печатаем headers/токены, только тип и текст ошибки.
        log.error(
            "Ошибка обращения к Bitbucket API (%s: %s). "
            "Проверь доступность Bitbucket с препрод-хоста и права токена.",
            type(e).__name__, e,
        )
        return 1

    # ── FROM-сторона для perlcritic/impact (переиспользуем уже полученный PR) ──
    source_project, source_repo, source_ref = _extract_source_side(pr_details)
    if not source_ref:
        log.warning(
            "У PR #%s не удалось получить fromRef.latestCommit — perlcritic/impact "
            "деградируют (source-сторона недоступна).",
            pr_id,
        )

    # ── Запуск ядра ревью (тот же вызов, что делает webhook-эндпоинт) ──
    log.info("Запускаю ревью PR #%s в %s/%s...", pr_id, project, repo)
    try:
        pr_review_bot.review_pull_request(
            project, repo, pr_id,
            source_project, source_repo, source_ref,
        )
    except Exception as e:
        # review_pull_request сам ловит внутренние сбои и постит user-safe коммент,
        # но подстрахуемся: неожиданная ошибка на этом уровне = ненулевой код.
        log.error("Непредвиденная ошибка ревью PR #%s: %s: %s", pr_id, type(e).__name__, e)
        return 1

    log.info("Ревью PR #%s завершено.", pr_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
