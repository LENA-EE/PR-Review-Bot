FROM python:3.11-slim

WORKDIR /app

# Зависимости
RUN pip install --no-cache-dir fastapi uvicorn requests

# Код бота
COPY pr_review_bot.py .

# Стайлгайд — необязательный файл
# Положи styleguide.md на сервере рядом с docker-compose.yml
# Он смонтируется через volume (см. docker-compose.yml)

EXPOSE 9000

CMD ["python", "pr_review_bot.py"]
