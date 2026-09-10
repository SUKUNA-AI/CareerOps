FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

WORKDIR /srv/careerops

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir . "transformers==4.57.3"

USER 10001:10001

CMD ["python", "-m", "careerops_reranker"]