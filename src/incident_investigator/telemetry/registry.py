"""Maps the configured backend name to an adapter. Adding an adapter = one module + one entry."""

from collections.abc import Callable

from incident_investigator.config import Settings
from incident_investigator.telemetry.base import TelemetryBackend

BackendFactory = Callable[[Settings], TelemetryBackend]


def _sqlite(settings: Settings) -> TelemetryBackend:
    from incident_investigator.telemetry.sqlite import SQLiteBackend

    return SQLiteBackend(settings.sqlite_path)


BACKENDS: dict[str, BackendFactory] = {
    "sqlite": _sqlite,
}


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
