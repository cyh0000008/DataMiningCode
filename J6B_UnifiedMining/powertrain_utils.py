from __future__ import annotations

from typing import TypeVar


POWERTRAIN_TYPES = ("PHEV/BEV", "ICE")
T = TypeVar("T")


def normalize_powertrain_type(value: object) -> str:
    powertrain_type = str(value).strip() if value is not None else ""
    if powertrain_type not in POWERTRAIN_TYPES:
        raise ValueError(
            "powertrain_type must be one of %s, got: %r"
            % (", ".join(POWERTRAIN_TYPES), value)
        )
    return powertrain_type


def value_for_powertrain(powertrain_type: str, phev_bev_value: T, ice_value: T) -> T:
    if powertrain_type == "PHEV/BEV":
        return phev_bev_value
    if powertrain_type == "ICE":
        return ice_value
    raise ValueError("Unsupported powertrain_type: %r" % powertrain_type)
