FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.lock pyproject.toml ./
RUN python -m pip install --no-cache-dir --requirement requirements.lock

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY evals ./evals
COPY scripts ./scripts
RUN python -m pip install --no-cache-dir --no-deps .

RUN addgroup --system app && adduser --system --ingroup app app

USER app

EXPOSE 8000

CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
