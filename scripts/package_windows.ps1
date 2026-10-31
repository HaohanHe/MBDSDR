# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
  MBDSDR Windows 便携包一键构建：configure + build + install + windeployqt + zip。

.DESCRIPTION
  缺口台账 P0「C++ 零安装/打包」的 Windows 落地。产物为解压即用的便携包：
  bin/mbdsdr.exe（桌面）+ bin/mbdsdr_mcp.exe（headless MCP）+ bin/i18n/*.qm
  + Qt/MinGW 运行库（windeployqt 收集）+ 许可证文档。

  诚实口径：本脚本只做"构建+收集+打包"，不做安装器（无注册表/快捷方式）；
  运行库收集以 windeployqt 的覆盖为准，MinGW 系统 DLL 缺漏时按报错补。

.EXAMPLE
  .\scripts\package_windows.ps1                          # 默认本机 Qt/MinGW 前缀
  .\scripts\package_windows.ps1 -QtRoot D:\Qt\6.8.2\mingw_64
  .\scripts\package_windows.ps1 -SkipBuild               # 复用已有 build/，只重打 zip
#>
param(
    [string]$QtRoot = "C:\QtLocal\6.8.2\mingw_64",
    [string]$MinGWBin = "C:\QtLocal\Tools\mingw1310_64\bin",
    [string]$BuildDir = "",
    [switch]$SkipBuild
)
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot          # scripts/ 的父目录 = 仓库根
$cpp = Join-Path $repo "cpp"
if (-not $BuildDir) { $BuildDir = Join-Path $cpp "build" }
$windeployqt = Join-Path $QtRoot "bin\windeployqt.exe"

if (-not (Test-Path $windeployqt)) {
    throw "找不到 windeployqt：$windeployqt（用 -QtRoot 指定 Qt 前缀）"
}

# 构建工具入 PATH（MinGW make / g++），与本地 ctest 基线一致
$env:PATH = "$MinGWBin;$(Join-Path $QtRoot 'bin');$env:PATH"

# ---- 版本号：解析 cpp/CMakeLists.txt 的 project(mbdsdr VERSION x.y.z) ----
$cmakeLists = Get-Content (Join-Path $cpp "CMakeLists.txt") -Raw
if ($cmakeLists -notmatch 'project\(mbdsdr\s+VERSION\s+([0-9.]+)') {
    throw "无法从 CMakeLists.txt 解析版本号"
}
$ver = $Matches[1]
Write-Host "== MBDSDR $ver Windows 便携包 =="

# ---- 构建 ----
if (-not $SkipBuild) {
    if (-not (Test-Path (Join-Path $BuildDir "CMakeCache.txt"))) {
        # 显式 -G：Windows 上 cmake 不带 -G 默认落 Visual Studio 生成器，
        # 与 MinGW 工具链不符（"CMAKE_CXX_COMPILER not set" 实测踩坑）。
        cmake -S $cpp -B $BuildDir -G "MinGW Makefiles" -DCMAKE_PREFIX_PATH="$QtRoot"
        if ($LASTEXITCODE -ne 0) { throw "cmake configure 失败" }
    }
    cmake --build $BuildDir -j
    if ($LASTEXITCODE -ne 0) { throw "cmake build 失败" }
}

# ---- 安装树（只含交付物，测试目标不进树）----
$dist = Join-Path $BuildDir "dist"
$stage = Join-Path $dist "mbdsdr-$ver-win64-portable"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
cmake --install $BuildDir --prefix $stage
if ($LASTEXITCODE -ne 0) { throw "cmake --install 失败" }

# ---- Qt/MinGW 运行库 ----
& $windeployqt --release --no-translations (Join-Path $stage "bin\mbdsdr.exe")
if ($LASTEXITCODE -ne 0) { throw "windeployqt(mbdsdr) 失败" }
& $windeployqt --release --no-translations (Join-Path $stage "bin\mbdsdr_mcp.exe")
if ($LASTEXITCODE -ne 0) { throw "windeployqt(mbdsdr_mcp) 失败" }

# ---- 压 zip + SHA256 ----
$zip = Join-Path $dist "mbdsdr-$ver-win64-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$zip.sha256" -Value "$sha  $(Split-Path $zip -Leaf)"

Write-Host "产物: $zip"
Write-Host "SHA256: $sha"
