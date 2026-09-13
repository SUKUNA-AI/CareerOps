from __future__ import annotations

import logging

from .config import RerankerRuntimeConfig
from .server import serve


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return serve(RerankerRuntimeConfig.from_env())


if __name__ == "__main__":
    raise SystemExit(main())
