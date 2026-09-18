FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY hh-applicant-tool /vendor/hh-applicant-tool

RUN pip install --no-cache-dir . /vendor/hh-applicant-tool && \
    groupadd --system careerops && \
    useradd --system --gid careerops --home-dir /nonexistent --shell /usr/sbin/nologin careerops

USER careerops

ENTRYPOINT ["python", "-m", "careerops_application"]
CMD ["serve"]
