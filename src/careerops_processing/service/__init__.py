"""Runtime отдельного сервиса CareerOPS Processing v2"""

from .config import ProcessingRuntimeConfig
from .health import HealthServer

__all__ = ["HealthServer", "ProcessingRuntimeConfig"]
