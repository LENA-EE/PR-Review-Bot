# Консольный режим (`review_cli.py`) — установка и дебаг

Альтернативный путь запуска ревью без входящего вебхука: TeamCity по SSH заходит
на препрод-хост и напрямую вызывает `review_cli.py`, который зовёт то же ядро
`pr_review_bot.review_pull_request(...)`, что и webhook-эндпоинт.

Подставь свои реальные значения вместо плейсхолдеров `<...>` (адрес банковского
Bitbucket, имя папки, адрес сервера) — здесь они намеренно обезличены.

> ⚠️ Конфиг читается только через `os.getenv()` — в боте нет `python-dotenv`.
> Файл `.env` **не подхватывается автоматически** вне docker-compose, его нужно
> явно грузить в shell перед запуском (см. Часть 3).

---

## Часть 1. Локально → положить код в новую папку банковского Bitbucket

```bash
# 1. Клонируешь банковский репо (или переходишь в уже склонированный)
git clone https://<BANK_BITBUCKET_URL>/scm/<PROJECT>/<REPO>.git
cd <REPO>

# 2. Прибиваешь корп-identity для этого клона (чтобы не утекла личная почта)
git config user.email "<ТЫ>@<КОРП_ДОМЕН>"
git config user.name "Имя Фамилия"

# 3. Создаёшь новую папку под консольную версию
mkdir <НОВАЯ_ПАПКА>   # например console-bot
```

Копируешь **только рантайм-файлы** (без тестов, `.env`, `__pycache__`, `.bak`):

```bash
cp /путь/к/pr_review_bot/pr_review_bot.py       <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/review_cli.py          <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/mcp_client.py          <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/bitbucket_files.py     <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/diff_filter.py         <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/perlcritic_severity.py <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/styleguide_rules.py    <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/changed_symbols.py     <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/requirements.txt       <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/.env.example           <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/styleguide.md          <НОВАЯ_ПАПКА>/
cp /путь/к/pr_review_bot/styleguide_rules.txt   <НОВАЯ_ПАПКА>/
```

> Если хоть один из `mcp_client.py` / `bitbucket_files.py` / `diff_filter.py` /
> `perlcritic_severity.py` / `styleguide_rules.py` / `changed_symbols.py` не
> скопируешь — бот не упадёт (graceful degradation), но молча откатится к
> чистому Фениксу без `[perlcritic]`/`[styleguide]`/`[impact]`. Частая причина
> «а где мои слои?» на дебаге.

```bash
# 4. Коммит и пуш
git add <НОВАЯ_ПАПКА>/
git commit -m "feat: консольная версия бота для TeamCity-пути"
git push origin master
```

---

## Часть 2. Сервер → клонировать

```bash
# 5. SSH на сервер
ssh <твой_логин>@<АДРЕС_СЕРВЕРА>

# 6. Клонируешь туда тот же банковский репо
cd /куда/договорились   # например /opt/jarvis
git clone https://<BANK_BITBUCKET_URL>/scm/<PROJECT>/<REPO>.git
cd <REPO>/<НОВАЯ_ПАПКА>
```

---

## Часть 3. Окружение на сервере

```bash
# 7. venv + зависимости
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 8. .env — создаётся ТОЛЬКО на сервере, в git не идёт
cp .env.example .env
nano .env
```

Заполни минимум: `BITBUCKET_URL`, `BITBUCKET_TOKEN`, `FENIX_URL`, `FENIX_TOKEN`.
Если хочешь слои сразу включёнными — `PERLCRITIC_ENABLED=1`,
`MCP_DROSPR_URL=...`, `IMPACT_ENABLED=1`.

Ещё поправь путь под bare-запуск (в `.env.example` дефолт докеровский
`/app/...`, вне контейнера такого пути нет):

```bash
STYLEGUIDE_RULES_PATH=/полный/путь/к/<НОВАЯ_ПАПКА>/styleguide_rules.txt
```

**Ключевой момент**: `.env` сам не читается питоном. Перед каждым запуском
грузи его в shell:

```bash
set -a
source .env
set +a
```

---

## Часть 4. Первый прогон / дебаг

```bash
# 9. Проверка, что скрипт вообще живой (не требует токенов)
python3 review_cli.py --help

# 10. Реальный прогон — по номеру PR
python3 review_cli.py --project <PROJECT> --repo <REPO_SLUG> --pr-id 42

# ...или по ветке
python3 review_cli.py --project <PROJECT> --repo <REPO_SLUG> --branch feature/login

# 11. Код возврата — TeamCity будет на него смотреть
echo $?   # 0 = успех (в т.ч. «PR по ветке не найден»), 1 = реальная ошибка
```

### Частые ошибки на дебаге

| Что видишь | Причина | Что делать |
|---|---|---|
| `ImportError` при старте | не установлены `requests`/`fastapi` | `pip install -r requirements.txt` внутри активного venv |
| `Не задан BITBUCKET_TOKEN` | `.env` заполнен, но не загружен в shell | `set -a && source .env && set +a` перед запуском |
| Ревью прошло, но нет `[perlcritic]`/`[styleguide]`/`[impact]` | не скопирован соответствующий модуль ИЛИ `MCP_DROSPR_URL` пустой | проверь, что все `.py`-модули из папки в Части 1 на месте; проверь `MCP_DROSPR_URL` |
| «По ветке нет открытого PR» и выход 0 | это не ошибка, PR правда не найден | проверь номер/имя ветки вручную в Bitbucket UI |
| Ошибка обращения к Bitbucket API | сеть/токен/права | проверь, что с сервера есть доступ до `BITBUCKET_URL`, и что у токена права `Repo:Read + PR:Write` |

---

Когда шаги 10–11 будут вызываться из TeamCity — оберни их в shell-скрипт
(`source .env` + `python3 review_cli.py ...`), и его же будет дёргать по SSH
TeamCity-агент.
