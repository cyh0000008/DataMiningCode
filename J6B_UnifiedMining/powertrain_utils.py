from __future__ import annotations

from typing import TypeVar
import warnings


POWERTRAIN_TYPES = ("ICE", "PHEV", "BEV")
T = TypeVar("T")


def normalize_powertrain_type(value: object) -> str:
    powertrain_type = str(value).strip().upper() if value is not None else ""
    if powertrain_type == "PHEV/BEV":
        warnings.warn("Legacy PHEV/BEV migrated to PHEV; select BEV explicitly for a BEV vehicle.", UserWarning)
        powertrain_type = "PHEV"
    if powertrain_type not in POWERTRAIN_TYPES:
        raise ValueError(
            "powertrain_type must be one of %s, got: %r"
            % (", ".join(POWERTRAIN_TYPES), value)
        )
    return powertrain_type


def value_for_powertrain(powertrain_type: str, *, ice_value: T, phev_value: T, bev_value: T) -> T:
    if powertrain_type == "PHEV":
        return phev_value
    if powertrain_type == "BEV":
        return bev_value
    if powertrain_type == "ICE":
        return ice_value
    raise ValueError("Unsupported powertrain_type: %r" % powertrain_type)
