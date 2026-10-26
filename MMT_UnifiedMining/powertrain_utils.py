from typing import Any, Dict, List, TypeVar
import warnings


POWERTRAIN_TYPES = ("ICE", "PHEV", "BEV")
T = TypeVar("T")


def normalize_powertrain_type(value: object) -> str:
    powertrain_type = str(value).strip().upper() if value is not None else ""
    if powertrain_type == "PHEV/BEV":
        warnings.warn("Legacy PHEV/BEV migrated to PHEV; select BEV explicitly for a BEV vehicle.", UserWarning)
        powertrain_type = "PHEV"
    if powertrain_type not in POWERTRAIN_TYPES:
        raise ValueError(f"powertrain_type must be ICE, PHEV or BEV, got: {value!r}")
    return powertrain_type


def get_powertrain_type(frames: List[Dict[str, Any]]) -> str:
    if not frames:
        raise ValueError("Cannot determine powertrain_type from empty topic frames")
    powertrain_type = frames[0].get("_powertrain_type")
    if powertrain_type not in POWERTRAIN_TYPES:
        raise ValueError(f"Invalid or missing _powertrain_type in topic frames: {powertrain_type!r}")
    return powertrain_type


def value_for_powertrain(powertrain_type: str, *, ice_value: T, phev_value: T, bev_value: T) -> T:
    if powertrain_type == "PHEV":
        return phev_value
    if powertrain_type == "BEV":
        return bev_value
    if powertrain_type == "ICE":
        return ice_value
    raise ValueError(f"Unsupported powertrain_type: {powertrain_type!r}")
