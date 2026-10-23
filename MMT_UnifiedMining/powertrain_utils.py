from typing import Any, Dict, List


POWERTRAIN_TYPES = {"PHEV/BEV", "ICE"}


def get_powertrain_type(frames: List[Dict[str, Any]]) -> str:
    if not frames:
        raise ValueError("Cannot determine powertrain_type from empty topic frames")
    powertrain_type = frames[0].get("_powertrain_type")
    if powertrain_type not in POWERTRAIN_TYPES:
        raise ValueError(f"Invalid or missing _powertrain_type in topic frames: {powertrain_type!r}")
    return powertrain_type


def value_for_powertrain(powertrain_type: str, phev_bev_value: Any, ice_value: Any) -> Any:
    if powertrain_type == "PHEV/BEV":
        return phev_bev_value
    if powertrain_type == "ICE":
        return ice_value
    raise ValueError(f"Unsupported powertrain_type: {powertrain_type!r}")
