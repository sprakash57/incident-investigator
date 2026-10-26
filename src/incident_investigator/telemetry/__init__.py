"""Telemetry backends: the interface, its models, and the adapter registry."""

from incident_investigator.telemetry.base import TelemetryBackend
from incident_investigator.telemetry.models import Capability, CapabilityNotAvailable
from incident_investigator.telemetry.registry import get_backend

__all__ = ["Capability", "CapabilityNotAvailable", "TelemetryBackend", "get_backend"]
