from __future__ import annotations

import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "code" / "acc_unreasonable_low_speed_count.py"
_SPEC = importlib.util.spec_from_file_location("_acc_unreasonable_low_speed_count_impl", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Cannot load metric implementation: {_MODULE_PATH}")

_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

for _name in dir(_MODULE):
    if not _name.startswith("__") or _name == "__run_haoran_data_run__":
        globals()[_name] = getattr(_MODULE, _name)
