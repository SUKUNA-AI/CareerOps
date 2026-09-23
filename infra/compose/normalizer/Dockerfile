FROM spark:4.2.0-scala2.13-java17-python3-ubuntu

USER root
RUN python3 -m pip install --no-cache-dir \
    boto3==1.40.61 \
    "psycopg[binary]==3.3.6"

COPY services/careerops-normalizer-spark/normalizer.py /opt/careerops/normalizer.py

USER spark
ENTRYPOINT ["/opt/spark/bin/spark-submit", "--master", "local[*]", "/opt/careerops/normalizer.py"]
