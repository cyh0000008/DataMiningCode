#!/usr/bin/env bash
# Ubuntu: bash update_linux.sh
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' '用法：bash update_linux.sh' \
    '更新 origin/main、安装依赖并生成 UnifiedApp/dist/UnifiedMiningApp。' \
    '需要 git、python3、python3-venv、网络及首次安装系统依赖时的 sudo 权限。'
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo '未知参数，请运行 bash update_linux.sh --help' >&2
  exit 1
fi
if [[ "$(uname -s)" != "Linux" ]]; then
  echo '请在 Ubuntu/Linux 中运行此脚本。' >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"
for tool in git python3 flock; do
  command -v "${tool}" >/dev/null || { echo "缺少命令：${tool}" >&2; exit 1; }
done
exec 9>"$(git rev-parse --git-path update-linux.lock)"
flock -n 9 || { echo '已有更新/打包任务在运行。' >&2; exit 1; }

if [[ "$(git branch --show-current)" != "main" ]]; then
  echo '请先切换到 main 分支后再更新。' >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo '本地存在未提交改动，请先提交或暂存这些改动后再更新：' >&2
  git status --short
  exit 1
fi

echo '[1/3] 拉取 origin/main...'
git fetch origin main
if [[ "$(git rev-list --count origin/main..HEAD)" != "0" ]]; then
  echo '本地 main 存在云端没有的提交，请先处理这些提交后再更新。' >&2
  exit 1
fi
git merge --ff-only origin/main

echo '[2/3] 准备 Python 环境与挖掘依赖...'
VENV_DIR="${REPO_DIR}/UnifiedApp/.venv"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  python3 -m venv "${VENV_DIR}" || {
    echo '创建环境失败，请执行 sudo apt install python3-venv 后重试。' >&2
    exit 1
  }
fi
PYTHON_BIN="${VENV_DIR}/bin/python"
"${PYTHON_BIN}" -m pip install -r "${REPO_DIR}/UnifiedApp/packaging/requirements_mining.txt"

echo '[3/3] 生成 Linux 可执行文件...'
EXE_PATH="${REPO_DIR}/UnifiedApp/dist/UnifiedMiningApp"
BACKUP_PATH="${EXE_PATH}.previous"
if [[ -f "${EXE_PATH}" ]]; then
  cp -p -- "${EXE_PATH}" "${BACKUP_PATH}"
fi
if ! bash "${REPO_DIR}/UnifiedApp/packaging/build_linux.sh" "${PYTHON_BIN}"; then
  if [[ -f "${BACKUP_PATH}" ]]; then
    cp -p -- "${BACKUP_PATH}" "${EXE_PATH}"
    echo "构建失败，旧可执行文件已恢复：${EXE_PATH}" >&2
  fi
  echo '代码已更新，修复上述构建错误后可重新运行此脚本。' >&2
  exit 1
fi
printf '\n更新完成，代码版本：%s\n' "$(git rev-parse --short HEAD)"
printf '可执行文件：%s\n' "${EXE_PATH}"
printf '挖掘/对比 Python：%s\n' "${PYTHON_BIN}"
echo '在应用“设置 → 环境设置”中，可将三个工程的解释器设为上述 Python。'
echo '请关闭旧应用后启动新生成的可执行文件。'
