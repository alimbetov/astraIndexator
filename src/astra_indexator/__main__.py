from __future__ import annotations

import logging

from astra_indexator.runtime.composition import build_runtime_from_env
from astra_indexator.runtime.config import RuntimeConfigError
from astra_indexator.runtime.db import DatabaseValidationError

LOGGER = logging.getLogger("astra_indexator.runtime")


def main() -> int:
    try:
        runtime = build_runtime_from_env()
    except (RuntimeConfigError, DatabaseValidationError) as exc:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s %(message)s")
        LOGGER.error("AstraIndexator startup failed: %s", exc)
        return 2
    return runtime.run()


if __name__ == "__main__":
    raise SystemExit(main())
