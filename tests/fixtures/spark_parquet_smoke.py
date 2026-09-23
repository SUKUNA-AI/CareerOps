from __future__ import annotations

import tempfile
from pathlib import Path

from pyspark.sql import SparkSession


spark = SparkSession.builder.master("local[1]").appName("careerops-parquet-smoke").getOrCreate()
try:
    with tempfile.TemporaryDirectory(prefix="careerops-parquet-smoke-") as temp_dir:
        output = Path(temp_dir) / "out"
        spark.createDataFrame([("ok",)], ["value"]).write.mode("overwrite").parquet(
            str(output)
        )
        parts = list(output.glob("part-*.parquet"))
        if not parts:
            raise RuntimeError("Spark did not produce a Parquet part file")
finally:
    spark.stop()
