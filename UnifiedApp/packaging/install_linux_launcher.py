"""Install the current user's Ubuntu application icon and dist launcher."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil


def desktop_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def exec_value(value: str) -> str:
    if "=" in value or "\n" in value or "\r" in value:
        raise ValueError("应用路径不能包含等号或换行符")
    value = value.replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return desktop_value('"' + value + '"')


def install(executable: Path, data_home: Path) -> Path:
    executable = executable.resolve(strict=True)
    source = Path(__file__).resolve().parents[1] / "assets" / "unified-mining.svg"
    icon = data_home / "icons" / "hicolor" / "scalable" / "apps" / source.name
    entry = data_home / "applications" / "unified-mining.desktop"
    content = "\n".join([
        "[Desktop Entry]", "Type=Application", "Version=1.0",
        "Name=Unified Mining", "Name[zh_CN]=纵向数据挖掘助手",
        "Comment=J6B / MMT mining and KPI comparison",
        "Exec=" + exec_value(str(executable)),
        "Path=" + desktop_value(str(executable.parent)),
        "Icon=" + desktop_value(str(icon)),
        "Terminal=false", "Categories=Science;Development;",
        "StartupNotify=true", "StartupWMClass=UnifiedMiningApp", "",
    ])
    icon.parent.mkdir(parents=True, exist_ok=True)
    entry.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, icon)
    entry.write_text(content, encoding="utf-8")
    launcher = executable.parent / "unified-mining.desktop"
    launcher.write_text(content, encoding="utf-8")
    launcher.chmod(0o755)
    print(f"带图标的启动入口：{launcher}")
    print(f"应用菜单入口：{entry}")
    return launcher


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    install(args.executable, data_home)
