"""
Дотягивание raw-контента файлов из Bitbucket (новая версия из source-ветки PR).

Нужно для perlcritic: ему нужен ПОЛНЫЙ файл новой версии (на куске diff многие
политики не компилируются). diff бот забирает отдельно (get_pr_diff); этот модуль —
только про сырое содержимое конкретного ref.

Bitbucket SERVER / Data Center (не Cloud!) отдаёт raw так:
    GET /rest/api/1.0/projects/{project}/repos/{repo}/raw/{path}?at={ref}
ref ДОЛЖЕН быть коммит-хешем (fromRef.latestCommit), не именем ветки — иначе файл и
diff могут оказаться из разных версий, и номера строк разъедутся (план M1).

Любая ошибка/404 → None: отсутствие файла НЕ роняет ревью (graceful degradation,
AES §7.3). Только GET — достаточно прав Repo:Read, новых прав не требует.

Зависимости передаются аргументами (base_url, headers) — модуль не импортирует
pr_review_bot (нет циклического импорта) и легко мокается в тестах.
"""

import logging
from typing import Optional

import requests

log = logging.getLogger("jarvis-pr-review")

PERLCRITICRC_PATH = ".perlcriticrc"


def _normalize_text(raw: bytes) -> str:
    """Декодирует терпимо и нормализует переводы строк/BOM (план M2).

    Номера строк perlcritic считаются по `\\n`; рассинхрон CRLF/BOM даёт off-by-one
    и сдвиг инлайн-привязки. Поэтому: снимаем BOM, CRLF→LF, CR→LF.
    Кодировка: UTF-8 → cp1251 → UTF-8 с заменой (perl-файлы из Windows бывают cp1251).
    """
    text: Optional[str] = None
    for encoding in ("utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")

    text = text.lstrip("﻿")       # BOM
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def get_file_content(
    base_url: str,
    headers: dict,
    project: str,
    repo: str,
    ref: str,
    path: str,
    timeout: int = 30,
    verify: bool = False,
) -> Optional[str]:
    """GET raw-контент файла из конкретного ref. None при 404/ошибке/пустом ref."""
    if not ref:
        log.warning(f"⚠️ raw {path}: пустой ref — пропускаю (нет версии для perlcritic)")
        return None
    url = (
        f"{base_url}/rest/api/1.0"
        f"/projects/{project}/repos/{repo}/raw/{path}"
    )
    try:
        resp = requests.get(
            url, headers=headers, params={"at": ref},
            timeout=timeout, verify=verify,
        )
        if resp.status_code == 404:
            log.info(f"ℹ️ raw {path}@{ref[:8]}: 404 (нет файла)")
            return None
        resp.raise_for_status()
        return _normalize_text(resp.content)
    except Exception as e:
        log.warning(f"⚠️ raw {path}@{ref[:8]}: {type(e).__name__}: {e} — пропускаю файл")
        return None


def get_perlcriticrc(
    base_url: str,
    headers: dict,
    project: str,
    repo: str,
    ref: str,
    timeout: int = 30,
    verify: bool = False,
) -> Optional[str]:
    """`.perlcriticrc` из корня репо (source-ветка). None → классический perlcritic (FR-006).

    Тянуть ОДИН раз на PR, не на каждый файл (бюджет токенов/времени, план риск 2).
    """
    return get_file_content(
        base_url, headers, project, repo, ref, PERLCRITICRC_PATH,
        timeout=timeout, verify=verify,
    )
