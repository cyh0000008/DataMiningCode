#!/usr/bin/env bash
# 在 Ubuntu / Linux 下打包 UnifiedApp 为单文件可执行程序。
#
# 用法：
#   ./packaging/build_linux.sh [python_executable]
#
# 本脚本会自动完成：
#   1. 若未指定/未找到可用的虚拟环境，则在 UnifiedApp/.venv 下创建一个；
#   2. 自动安装 requirements.txt 里的依赖（PySide6）以及 pyinstaller；
#   3. 尽量自动安装 PySide6/Qt 运行所需的系统共享库（仅当检测到 apt-get 且
#      有 root/sudo 权限时才会执行，失败不会中断打包，只会给出提示）；
#   4. 执行 PyInstaller 打包。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${1:-}"

log() { echo "[build_linux] $*"; }

# ----------------------------------------------------------------------
# 1. 确定使用哪个 Python，如果没有可用虚拟环境就自动创建一个
# ----------------------------------------------------------------------
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    PYTHON_BIN="${VENV_DIR}/bin/python"
    log "使用已存在的虚拟环境：${VENV_DIR}"
  else
    SYSTEM_PYTHON="$(command -v python3 || command -v python || true)"
    if [[ -z "${SYSTEM_PYTHON}" ]]; then
      echo "未找到 python3，请先安装 Python 3.9+ 再运行本脚本。" >&2
      exit 1
    fi
    log "未找到虚拟环境，正在创建：${VENV_DIR}"
    if ! "${SYSTEM_PYTHON}" -m venv "${VENV_DIR}"; then
      echo "创建虚拟环境失败，请确认已安装 python3-venv（例如：sudo apt install python3-venv）。" >&2
      exit 1
    fi
    PYTHON_BIN="${VENV_DIR}/bin/python"
  fi
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "指定的 Python 解释器不存在或不可执行：${PYTHON_BIN}" >&2
  exit 1
fi

log "使用 Python：${PYTHON_BIN}（$(${PYTHON_BIN} --version 2>&1)）"

# ----------------------------------------------------------------------
# 2. 尽量自动安装 PySide6/Qt 运行所需的系统共享库
#    （PySide6 的 manylinux wheel 自带大部分 Qt 库，但部分发行版仍缺少
#    libEGL / libxkbcommon / libxcb 等基础共享库，缺失时窗口无法启动）
# ----------------------------------------------------------------------
install_system_deps() {
  if ! command -v apt-get >/dev/null 2>&1; then
    log "未检测到 apt-get，跳过系统依赖自动安装（如为非 Debian/Ubuntu 系统请自行安装 Qt 运行库）。"
    return 0
  fi

  local sudo_cmd=""
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo_cmd="sudo"
    else
      log "当前非 root 且未找到 sudo，跳过系统依赖自动安装。"
      return 0
    fi
  fi

  local packages=(
    libgl1
    libegl1
    libxkbcommon0
    libxkbcommon-x11-0
    libxcb-cursor0
    libxcb-icccm4
    libxcb-image0
    libxcb-keysyms1
    libxcb-randr0
    libxcb-render-util0
    libxcb-shape0
    libxcb-xinerama0
    libdbus-1-3
    libfontconfig1
  )

  log "尝试自动安装 Qt 运行所需的系统共享库（需要网络与包管理权限，失败不影响后续打包）："
  if ${sudo_cmd} apt-get update -y >/dev/null 2>&1; then
    if ${sudo_cmd} apt-get install -y "${packages[@]}" >/dev/null 2>&1; then
      log "系统依赖安装完成。"
    else
      log "警告：部分系统依赖安装失败，如运行时报 Qt 相关错误，请手动执行："
      log "  sudo apt-get install -y ${packages[*]}"
    fi
  else
    log "警告：apt-get update 失败（可能无网络或无权限），跳过系统依赖自动安装。"
  fi
}

install_system_deps

# ----------------------------------------------------------------------
# 3. 自动安装 Python 依赖（requirements.txt + pyinstaller）
# ----------------------------------------------------------------------
log "升级 pip 并安装 Python 依赖（requirements.txt + pyinstaller）..."
"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install -r "${PROJECT_ROOT}/requirements.txt"
"${PYTHON_BIN}" -m pip install pyinstaller

# ----------------------------------------------------------------------
# 4. 执行打包
# ----------------------------------------------------------------------
log "开始执行 PyInstaller 打包..."
"${PYTHON_BIN}" -m PyInstaller --noconfirm --clean "${PROJECT_ROOT}/packaging/UnifiedApp.spec"

EXE_PATH="${PROJECT_ROOT}/dist/UnifiedMiningApp"
if [[ ! -f "${EXE_PATH}" ]]; then
  echo "PyInstaller finished but binary was not found: ${EXE_PATH}" >&2
  exit 1
fi
chmod +x "${EXE_PATH}"

echo ""
echo "可执行文件已生成："
echo "${EXE_PATH}"
echo ""
echo "首次运行时，若无法自动定位 J6B_UnifiedMining / MMT_UnifiedMining / CompareKPI_United，"
echo "请在应用内“设置 -> 环境设置”中手动指定这三个工程所在的仓库根目录，以及各自使用的 Python 解释器。"
