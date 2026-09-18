param(
    [string]$Python = ""
)

# 在 Windows 下打包 UnifiedApp 为单文件 exe。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1
#   # 或指定自定义 Python：
#   powershell -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 -Python "C:\Python311\python.exe"
#
# 本脚本会自动完成：
#   1. 若未指定/未找到可用虚拟环境，则在 UnifiedApp\.venv 下自动创建一个；
#   2. 自动安装 requirements.txt 里的依赖（PySide6）以及 pyinstaller；
#   3. 执行 PyInstaller 打包。
# Windows 下 PySide6 的 wheel 自带所需运行库，不需要额外安装系统依赖。

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host "[build_windows] $Message" -ForegroundColor Cyan
}

$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

# ----------------------------------------------------------------------
# 1. 确定使用哪个 Python，如果没有可用虚拟环境就自动创建一个
# ----------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($Python)) {
    if (Test-Path -LiteralPath $VenvPython) {
        $Python = $VenvPython
        Write-Step "使用已存在的虚拟环境：$VenvDir"
    } else {
        $SystemPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $SystemPython) {
            $SystemPython = Get-Command py -ErrorAction SilentlyContinue
        }
        if (-not $SystemPython) {
            throw "未找到 python，请先安装 Python 3.9+（并确保加入 PATH）后再运行本脚本。"
        }
        Write-Step "未找到虚拟环境，正在创建：$VenvDir"
        & $SystemPython.Source -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            throw "创建虚拟环境失败，请确认 Python 安装完整（包含 venv 模块）。"
        }
        $Python = $VenvPython
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    # 允许传入 PATH 中的命令名而非绝对路径
    $Resolved = Get-Command $Python -ErrorAction SilentlyContinue
    if (-not $Resolved) {
        throw "指定的 Python 解释器不存在：$Python"
    }
    $Python = $Resolved.Source
}

Write-Step "使用 Python：$Python"
& $Python --version

# ----------------------------------------------------------------------
# 2. 自动安装 Python 依赖（requirements.txt + pyinstaller）
# ----------------------------------------------------------------------
Write-Step "升级 pip 并安装 Python 依赖（requirements.txt + pyinstaller）..."
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip 升级失败，退出码 $LASTEXITCODE" }

& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "安装 requirements.txt 依赖失败，退出码 $LASTEXITCODE" }

& $Python -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw "安装 pyinstaller 失败，退出码 $LASTEXITCODE" }

# ----------------------------------------------------------------------
# 3. 执行打包
# ----------------------------------------------------------------------
Write-Step "开始执行 PyInstaller 打包..."
& $Python -m PyInstaller --noconfirm --clean (Join-Path $ProjectRoot "packaging\UnifiedApp.spec")

$ExitCode = $LASTEXITCODE
if ($ExitCode -ne 0) {
    throw "PyInstaller failed with exit code $ExitCode."
}

$ExePath = Join-Path $ProjectRoot "dist\UnifiedMiningApp.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "PyInstaller finished but EXE was not found: $ExePath"
}

Write-Host ""
Write-Host "EXE generated:" -ForegroundColor Green
Write-Host $ExePath
Write-Host ""
Write-Host "首次运行时，若无法自动定位 J6B_UnifiedMining / MMT_UnifiedMining / CompareKPI_United，"
Write-Host "请在应用内“设置 -> 环境设置”中手动指定这三个工程所在的仓库根目录，以及各自使用的 Python 解释器。"
