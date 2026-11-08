FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY async_remote ./async_remote
COPY tests ./tests

RUN pip install --no-cache-dir ".[test]"

CMD ["python", "-m", "pytest", "-q"]
