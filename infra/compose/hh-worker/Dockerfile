FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

COPY pyproject.toml README.md letter.example.txt ./
COPY src ./src
COPY hh-applicant-tool ./hh-applicant-tool

RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir ./hh-applicant-tool

CMD ["python", "-m", "careerops_integrations.hh.batch_cli", "--help"]
