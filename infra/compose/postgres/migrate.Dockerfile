FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic

RUN pip install --no-cache-dir . && \
    groupadd --system careerops && \
    useradd --system --gid careerops --home-dir /nonexistent --shell /usr/sbin/nologin careerops

USER careerops

ENTRYPOINT ["alembic"]
CMD ["upgrade", "head"]
