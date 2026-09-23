from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pyspark.sql import SparkSession


spark = SparkSession.builder.master("local[1]").appName("careerops-parquet-smoke").getOrCreate()
try:
    output = Path("/tmp") / f"careerops-parquet-smoke-{uuid4().hex}"
    spark.createDataFrame([("ok",)], ["value"]).write.mode("overwrite").parquet(str(output))
    parts = list(output.glob("part-*.parquet"))
    if not parts:
        raise RuntimeError("Spark did not produce a Parquet part file")
finally:
    spark.stop()
