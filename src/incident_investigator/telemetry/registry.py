"""Maps the configured backend name to an adapter. Adding an adapter = one module + one entry."""

from collections.abc import Callable

from incident_investigator.config import Settings
from incident_investigator.telemetry.base import TelemetryBackend

BackendFactory = Callable[[Settings], TelemetryBackend]

BACKENDS: dict[str, BackendFactory] = {}


class UnknownBackendError(ValueError):
    pass


def get_backend(settings: Settings) -> TelemetryBackend:
    try:
        factory = BACKENDS[settings.backend]
    except KeyError:
        available = ", ".join(sorted(BACKENDS)) or "none"
        raise UnknownBackendError(
            f"unknown backend '{settings.backend}' (available: {available})"
        ) from None
    return factory(settings)
