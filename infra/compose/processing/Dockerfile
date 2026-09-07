FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config/processing/target_policies ./config/processing/target_policies

RUN pip install --no-cache-dir . && \
    groupadd --system careerops && \
    useradd --system --gid careerops --home-dir /nonexistent --shell /usr/sbin/nologin careerops

USER careerops

ENTRYPOINT ["python", "-m", "careerops_processing"]
CMD ["serve"]
