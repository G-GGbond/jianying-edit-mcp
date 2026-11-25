<#
.SYNOPSIS
    剪映 AI 剪辑 MCP · 一键安装

.DESCRIPTION
    自动完成：检查 Python → 安装 MCP 包 → 探测剪映与 jy-draftc →
    生成启动器 start-jianying-mcp.bat → 打印可直接粘贴的客户端配置。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -JyDraftc "D:\tools\jy-draftc.exe"
#>
param(
    [string]$JyDraftc,
    [string]$JianyingDir,
    [switch]$SkipPip
)

$ErrorActionPreference = 'Continue'
$Host.UI.RawUI.WindowTitle = '剪映 AI 剪辑 MCP · 安装'

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }
function Ok($msg)   { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [警告] $msg" -ForegroundColor Yellow }
function Bad($msg)  { Write-Host "  [失败] $msg" -ForegroundColor Red }

Say ''
Say '==============================================' Cyan
Say '   剪映 AI 剪辑 MCP · 一键安装' Cyan
Say '==============================================' Cyan
Say ''

$root = $PSScriptRoot

# ---------------------------------------------------------------- 1. Python
Say '[1/5] 检查 Python' White
$python = $null
foreach ($cand in @('python', 'python3', 'py')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        $ver = & $cand -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($ver -and [version]$ver -ge [version]'3.10') {
            $python = (Get-Command $cand).Source
            Ok "Python $ver  ($python)"
            break
        } elseif ($ver) {
            Warn "找到 $cand 但版本 $ver 太低（需要 3.10+）"
        }
    } catch { }
}
if (-not $python) {
    Bad '没有可用的 Python 3.10+'
    Say ''
    Say '请先安装 Python：https://www.python.org/downloads/' Yellow
    Say '安装时务必勾选 "Add python.exe to PATH"' Yellow
    Say ''
    Read-Host '按回车退出'
    exit 1
}

# ---------------------------------------------------------------- 2. 安装包
Say ''
Say '[2/5] 安装 MCP 包' White
if ($SkipPip) {
    Warn '跳过（-SkipPip）'
} else {
    $pkgSpec = $root
    if (Test-Path (Join-Path $root 'pyproject.toml')) {
        Say "  从本地目录安装：$root"
        & $python -m pip install --quiet --upgrade --no-cache-dir --retries 1 --timeout 30 $root 2>&1 | ForEach-Object { Say "    $_" DarkGray }
    } else {
        Say '  从 PyPI 安装：jianying-edit-mcp'
        & $python -m pip install --quiet --upgrade --no-cache-dir --retries 1 --timeout 30 jianying-edit-mcp 2>&1 | ForEach-Object { Say "    $_" DarkGray }
    }
    $check = & $python -c "import jianying_edit_mcp, sys; print(jianying_edit_mcp.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) { Ok "jianying_edit_mcp $check 已安装" }
    else { Bad "安装失败：$check"; Say '  试试手动：pip install jianying-edit-mcp' Yellow; Read-Host '按回车退出'; exit 1 }
}

# ---------------------------------------------------------------- 3. 找剪映
Say ''
Say '[3/5] 探测剪映专业版' White
if (-not $JianyingDir) {
    $apps = Join-Path $env:LOCALAPPDATA 'JianyingPro\Apps'
    if (Test-Path $apps) {
        $cands = Get-ChildItem $apps -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName 'videoeditor.dll') } |
            Sort-Object Name -Descending
        if ($cands) { $JianyingDir = $cands[0].FullName }
    }
}
if ($JianyingDir -and (Test-Path (Join-Path $JianyingDir 'videoeditor.dll'))) {
    $ver = Split-Path $JianyingDir -Leaf
    Ok "剪映 $ver"
    Say "         $JianyingDir" DarkGray
} else {
    Bad '没找到剪映（需要含 videoeditor.dll 的目录）'
    Say ''
    Say '  安装命令（版本要与你下载的 jy-draftc 匹配）：' Yellow
    Say '    winget install --id ByteDance.JianyingPro --exact --version 11.6.0.14552' Yellow
    Say ''
    $JianyingDir = $null
}

# ---------------------------------------------------------------- 4. 找 jy-draftc
Say ''
Say '[4/5] 探测 jy-draftc' White
if (-not $JyDraftc) {
    foreach ($guess in @(
        (Join-Path $root 'tools\jy-draftc.exe'),
        (Join-Path $root 'jy-draftc.exe'),
        (Join-Path $env:USERPROFILE '.jy-draftc\jy-draftc.exe')
    )) {
        if (Test-Path $guess) { $JyDraftc = $guess; break }
    }
    if (-not $JyDraftc) {
        $onPath = Get-Command 'jy-draftc' -ErrorAction SilentlyContinue
        if ($onPath) { $JyDraftc = $onPath.Source }
    }
}
if ($JyDraftc -and (Test-Path $JyDraftc)) {
    Ok $JyDraftc
} else {
    Bad '没找到 jy-draftc.exe'
    Say ''
    Say '  它是草稿加解密与无头导出的必需组件，按剪映版本下载：' Yellow
    Say '    https://github.com/wenshui330/jy-draftc/releases' Yellow
    Say '  下载后把 jy-draftc.exe 放到本目录的 tools\ 下，或重新运行：' Yellow
    Say '    powershell -ExecutionPolicy Bypass -File .\install.ps1 -JyDraftc "C:\path\jy-draftc.exe"' Yellow
    Say ''
    $JyDraftc = $null
}

# ---------------------------------------------------------------- 5. 生成启动器 + 打印配置
Say ''
Say '[5/5] 生成启动器与配置' White

$bat = Join-Path $root 'start-jianying-mcp.bat'
$batLines = @(
    '@echo off',
    'chcp 65001 >nul',
    'set PYTHONIOENCODING=utf-8'
)
if ($JyDraftc)    { $batLines += "set JYC_JY_DRAFTC=$JyDraftc" }
if ($JianyingDir) { $batLines += "set JYC_JIANYING_DIR=$JianyingDir" }
$batLines += "`"$python`" -m jianying_edit_mcp.mcp_server %*"
Set-Content -Path $bat -Value $batLines -Encoding OEM
Ok "启动器：$bat"

Say ''
Say '==============================================' Cyan
Say '   在客户端里这样填' Cyan
Say '==============================================' Cyan
Say ''
if ($JyDraftc -and $JianyingDir) {
    Say '【豆包工作 客户端】' White
    Say '  侧边栏 → 技能·连接器 → 新建自定义连接器 → 传输类型选 STDIO，然后填：' Gray
    Say ''
    Say "    服务器名称： jianying-edit"
    Say "    传输类型：   STDIO"
    Say "    命令：       $bat" Green
    Say "    参数：       （留空）"
    Say "    环境变量：   （留空，已写进启动器）"
    Say ''
    Say '【Claude Desktop / Trae / Cursor】把下面这段加到 MCP 配置里：' White
    Say ''
    $json = @{
        mcpServers = @{
            'jianying-edit' = @{
                command = $bat
                args    = @()
            }
        }
    } | ConvertTo-Json -Depth 6
    Say $json Cyan
} else {
    Warn '上面两项没探测全，先补齐再运行本脚本，或手动填环境变量：'
    Say '    JYC_JY_DRAFTC=jy-draftc.exe 的完整路径'
    Say '    JYC_JIANYING_DIR=含 videoeditor.dll 的剪映目录'
}

Say ''
Say '==============================================' Cyan
Say '   最后一步：验证' Cyan
Say '==============================================' Cyan
Say ''
Say '  在终端跑一次自检（应输出 ok: true）：' Gray
Say "    & `"$bat`" " DarkGray
Say '  （它会等 stdin，直接 Ctrl+C 退出即可；真正的自检见 docs）' DarkGray
Say ''
Say "  或者：python -m jianying_edit_mcp.engine doctor" Gray
Say ''
Read-Host '按回车关闭'
