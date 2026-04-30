"""
JARVIS PR Review Bot
====================
Автоматическое ревью Pull Request через Феникс (Qwen).

Как работает:
  1. Bitbucket присылает webhook когда открывается PR
  2. Бот забирает diff из Bitbucket API
  3. Отправляет diff в Феникс на анализ
  4. Постит комментарии прямо в PR к нужным строкам

Токены передаются через переменные окружения — НЕ в коде!
"""

import os
import json
import requests
import logging
from fastapi import FastAPI, Request
from typing import Optional

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis-pr-review")

app = FastAPI()

# ── Настройки — берутся из ENV, токенов в коде нет ─────────
BITBUCKET_URL   = os.getenv("BITBUCKET_URL",   "http://bitbucket.bank.ru")
BITBUCKET_TOKEN = os.getenv("BITBUCKET_TOKEN", "")

FENIX_URL   = os.getenv("FENIX_URL",   "http://fenix.bank.ru/api/chat")
FENIX_TOKEN = os.getenv("FENIX_TOKEN", "")
FENIX_MODEL = os.getenv("FENIX_MODEL", "qwen")

# Максимум строк диффа за один запрос — экономим токены Феникса
MAX_DIFF_LINES = int(os.getenv("MAX_DIFF_LINES", "300"))
# ────────────────────────────────────────────────────────────


# ── Проверка конфига при старте ─────────────────────────────
def check_config():
    missing = []
    if not BITBUCKET_TOKEN:
        missing.append("BITBUCKET_TOKEN")
    if not FENIX_TOKEN:
        missing.append("FENIX_TOKEN")
    if missing:
        log.error(f"❌ Не заданы переменные окружения: {', '.join(missing)}")
        log.error("Создай .env файл на сервере и перезапусти контейнер")
    else:
        log.info("✅ Конфиг загружен, все токены на месте")


# ── Bitbucket API ───────────────────────────────────────────

def bb_headers() -> dict:
    return {
        "Authorization": f"Bearer {BITBUCKET_TOKEN}",
        "Content-Type": "application/json",
    }


def get_pr_diff(project: str, repo: str, pr_id: int) -> str:
    """Забирает diff Pull Request из Bitbucket."""
    url = (
        f"{BITBUCKET_URL}/rest/api/1.0"
        f"/projects/{project}/repos/{repo}"
        f"/pull-requests/{pr_id}/diff"
    )
    resp = requests.get(url, headers=bb_headers(), timeout=30, verify=False)
    resp.raise_for_status()
    return parse_bitbucket_diff(resp.json())


def parse_bitbucket_diff(diff_json: dict) -> str:
    """Конвертирует Bitbucket diff JSON в читаемый текст."""
    lines = []
    for diff in diff_json.get("diffs", []):
        path = diff.get("destination", {}).get("toString", "unknown")
        lines.append(f"\n--- Файл: {path} ---")
        for hunk in diff.get("hunks", []):
            for segment in hunk.get("segments", []):
                seg_type = segment.get("type", "")
                prefix = (
                    "+" if seg_type == "ADDED"
                    else "-" if seg_type == "REMOVED"
                    else " "
                )
                for line in segment.get("lines", []):
                    lines.append(f"{prefix}{line.get('line', '')}")
    return "\n".join(lines)


def post_comment(
    project: str,
    repo: str,
    pr_id: int,
    text: str,
    file_path: Optional[str] = None,
    line: Optional[int] = None,
) -> dict:
    """Постит комментарий в PR — к строке или общий."""
    url = (
        f"{BITBUCKET_URL}/rest/api/1.0"
        f"/projects/{project}/repos/{repo}"
        f"/pull-requests/{pr_id}/comments"
    )
    body: dict = {"text": text}
    if file_path and line:
        body["anchor"] = {
            "line": line,
            "lineType": "ADDED",
            "fileType": "TO",
            "path": file_path,
        }
    resp = requests.post(url, headers=bb_headers(), json=body, timeout=15, verify=False)
    resp.raise_for_status()
    return resp.json()


def post_general_comment(project: str, repo: str, pr_id: int, text: str):
    """Постит общий комментарий к PR."""
    post_comment(project, repo, pr_id, text)


# ── Феникс API ──────────────────────────────────────────────

# Стайлгайд — загружается из файла если есть
# Файл кладётся на сервере: /app/styleguide.md
# Обновляется вручную или скриптом из Confluence
STYLEGUIDE_PATH = "/app/styleguide.md"

def load_styleguide() -> str:
    try:
        with open(STYLEGUIDE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
            log.info("✅ Стайлгайд загружен")
            return content
    except FileNotFoundError:
        log.warning("⚠️ Стайлгайд не найден, работаю без него")
        return ""


def build_prompt(diff: str) -> str:
    styleguide = load_styleguide()

    styleguide_section = ""
    if styleguide:
        styleguide_section = f"""
Команда использует следующий стайлгайд — соблюдение обязательно:
───────────────────────────────
{styleguide}
───────────────────────────────
"""

    return f"""
Ты опытный Perl разработчик и делаешь code review.
Смотри ТОЛЬКО на добавленные строки (начинаются с +).
Не комментируй удалённые строки и контекст.
{styleguide_section}
Проверяй:
- Валидация входных параметров (нет проверки undef, пустых строк)
- Обработка ошибок (нет eval/die там где нужно)
- Безопасность (SQL инъекции, небезопасные операции)
- Perl best practices (use strict, use warnings)
- Читаемость (слишком сложная логика, нет комментариев)

ВАЖНО:
1. НЕ ПИШИ НИКАКИХ ПОЯСНЕНИЙ, МЫСЛЕЙ ИЛИ ДУМАНИЙ (THINKING).
2. ОТВЕТ ДОЛЖЕН НАЧИНАТЬСЯ С '[' И ЗАКАНЧИВАТЬСЯ ']'.
3. НИКАКОГО MARKDOWN (без ```json).

Формат ответа (валидный JSON массив):
[
  {{
    "file": "имя файла",
    "line": номер_строки,
    "severity": "error|warning|suggestion",
    "comment": "конкретное замечание понятным языком"
  }}
]

Если замечаний нет — верни пустой массив: []
Максимум 10 замечаний — только самые важные.
Будь конкретным. Не придирайся к стилю если логика правильная.

Diff для ревью:
{diff}
"""


def ask_fenix(diff: str) -> Optional[list[dict]]:
    """Отправляет diff в Феникс, получает список замечаний.
    Возвращает None в случае ошибки, [] если замечаний нет.
    """

    # LiteLLM требует полного пути, даже если в ENV дано /v1
    fenix_endpoint = FENIX_URL
    if fenix_endpoint.endswith("/v1"):
        fenix_endpoint += "/chat/completions"

    # Обрезаем если diff большой — экономим токены
    diff_lines = diff.split("\n")
    if len(diff_lines) > MAX_DIFF_LINES:
        diff = "\n".join(diff_lines[:MAX_DIFF_LINES])
        diff += f"\n\n[... обрезано, первые {MAX_DIFF_LINES} строк ...]"
        log.warning(f"Diff обрезан до {MAX_DIFF_LINES} строк")

    prompt = build_prompt(diff)

    try:
        resp = requests.post(
            fenix_endpoint,
            headers={
                "Authorization": f"Bearer {FENIX_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "model": FENIX_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
                "temperature": 0.1,
            },
            timeout=60,
            verify=False,
        )
        resp.raise_for_status()

        data = resp.json()
        log.info(f"Ответ Феникса: {json.dumps(data)[:200]}")

        # Пробуем разные форматы ответа
        # Формат OpenAI-совместимый
        raw = (
            data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
        )
        # Если не OpenAI — пробуем прямой формат
        if not raw:
            raw = data.get("response", "")
        if not raw:
            raw = data.get("content", "")
        if not raw:
            raw = str(data)

        # Чистим markdown если модель завернула
        raw = raw.strip()
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:]
                part = part.strip()
                if part.startswith("[") or part.startswith("{"):
                    raw = part
                    break

        # Фикс для моделей, которые возвращают одинарные кавычки (как в Python)
        # json.loads требует двойных кавычек
        if raw.startswith("[") or raw.startswith("{"):
            # Заменяем одинарные кавычки на двойные (грубый фикс, но рабочий для простых строк)
            # Лучше искать JSON блок через regex, но попробуем replace для начала
            # Внимание: это может сломать если внутри строк есть одинарные кавычки, 
            # но Qwen обычно не ставит их в JSON ключах.
            # Для надежности лучше использовать ast.literal_eval если json.loads падает.
            pass

        try:
            comments = json.loads(raw)
        except json.JSONDecodeError:
            # Пробуем распарсить как Python dict/list (одинарные кавычки)
            import ast
            try:
                # ast.literal_eval безопаснее eval, он выполнит только литералы
                comments = ast.literal_eval(raw)
            except Exception:
                raise # Если и это не помогло, пробрасываем оригинальную ошибку

        log.info(f"Феникс вернул {len(comments)} замечаний")
        return comments

    except json.JSONDecodeError as e:
        log.error(f"Феникс вернул не JSON: {e}\nОтвет: {raw[:300]}")
        return None
    except Exception as e:
        log.error(f"Ошибка запроса к Фениксу: {e}")
        return None


# ── Основная логика ревью ───────────────────────────────────

def review_pull_request(project: str, repo: str, pr_id: int):
    """Полный цикл ревью одного PR."""
    log.info(f"🔍 Начинаю ревью PR #{pr_id} в {project}/{repo}")

    # 1. Забираем diff
    try:
        diff = get_pr_diff(project, repo, pr_id)
    except Exception as e:
        log.error(f"Не удалось получить diff: {e}")
        post_general_comment(
            project, repo, pr_id,
            "🤖 **JARVIS Review**: Не удалось получить diff PR. "
            "Проверьте права токена Bitbucket."
        )
        return

    if not diff.strip():
        log.info("Diff пустой, пропускаю")
        return

    # 2. Феникс анализирует
    comments = ask_fenix(diff)

    # Обработка ошибки связи с ИИ
    if comments is None:
        post_general_comment(
            project, repo, pr_id,
            "🤖 **JARVIS Review**: Упс! Мой мозг (Феникс) не ответил. "
            "Проверка не удалась, попробуйте обновить PR позже. 🔌_error\n\n"
            "_Это автоматическое ревью. Обязательна проверка сеньором. "
            "ИИ пока не заменит кожаных! 🧠_"
        )
        return

    # 3. Нет замечаний
    if not comments:
        post_general_comment(
            project, repo, pr_id,
            "🤖 **JARVIS Review**: Автоматическая проверка завершена.\n\n"
            "✅ Критических замечаний не найдено.\n\n"
            "_Обязательна проверка сеньором._"
        )
        return

    # 4. Постим замечания к строкам
    severity_emoji = {
        "error":      "🔴",
        "warning":    "🟡",
        "suggestion": "💡",
    }

    posted = 0
    for item in comments:
        emoji = severity_emoji.get(item.get("severity", "suggestion"), "💡")
        text = (
            f"{emoji} **JARVIS Review** "
            f"[{item.get('severity', '?')}]\n\n"
            f"{item.get('comment', '')}"
        )
        try:
            post_comment(
                project, repo, pr_id,
                text=text,
                file_path=item.get("file"),
                line=item.get("line"),
            )
            posted += 1
        except Exception as e:
            # Не смогли привязать к строке — постим общим
            log.warning(f"Постим общим комментарием: {e}")
            try:
                post_general_comment(project, repo, pr_id, text)
                posted += 1
            except Exception as e2:
                log.error(f"Не удалось запостить: {e2}")

    # 5. Итоговый комментарий
    errors   = sum(1 for c in comments if c.get("severity") == "error")
    warnings = sum(1 for c in comments if c.get("severity") == "warning")
    tips     = sum(1 for c in comments if c.get("severity") == "suggestion")

    summary = (
        f"🤖 **JARVIS Review** — автоматическая проверка завершена\n\n"
        f"🔴 Ошибок: {errors} · "
        f"🟡 Предупреждений: {warnings} · "
        f"💡 Подсказок: {tips}\n\n"
        f"_Это автоматическое ревью. Обязательна проверка сеньором._"
    )
    post_general_comment(project, repo, pr_id, summary)
    log.info(f"✅ Ревью завершено. Запостил {posted} комментариев.")


# ── Webhook endpoint ────────────────────────────────────────

@app.post("/webhook")
async def bitbucket_webhook(request: Request):
    """Принимает webhook от Bitbucket."""
    try:
        payload = await request.json()
    except Exception:
        return {"status": "error", "message": "invalid json"}

    event = payload.get("eventKey", "")
    log.info(f"📨 Получен webhook: {event}")

    if event not in ("pr:opened", "pr:modified"):
        return {"status": "ignored", "event": event}

    pr        = payload.get("pullRequest", {})
    pr_id     = pr.get("id")
    repo      = pr.get("toRef", {}).get("repository", {})
    repo_slug = repo.get("slug")
    project_key = repo.get("project", {}).get("key")

    if not all([pr_id, repo_slug, project_key]):
        log.error(f"Не хватает данных в webhook")
        return {"status": "error", "message": "missing data"}

    review_pull_request(project_key, repo_slug, pr_id)
    return {"status": "ok", "pr_id": pr_id}


@app.get("/health")
async def health():
    """Проверка что бот живой."""
    return {
        "status": "ok",
        "bot": "JARVIS PR Review",
        "bitbucket": BITBUCKET_URL,
        "fenix": FENIX_URL,
        "tokens_loaded": bool(BITBUCKET_TOKEN and FENIX_TOKEN),
    }


# ── Запуск ──────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    check_config()


if __name__ == "__main__":
    import uvicorn
    print("""
    ╔══════════════════════════════════════╗
    ║     JARVIS PR Review Bot v1.0        ║
    ║     Данные не покидают периметр      ║
    ╚══════════════════════════════════════╝
    """)
    uvicorn.run(app, host="0.0.0.0", port=9000)
