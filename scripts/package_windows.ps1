# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
  MBDSDR Windows 便携包一键构建：configure + build + install + windeployqt + RTL DLL + 冒烟 + zip。

.DESCRIPTION
  缺口台账 P0「C++ 零安装/打包」的 Windows 落地。产物为解压即用的便携包：
  bin/mbdsdr.exe（桌面）+ bin/mbdsdr_mcp.exe（headless MCP）+ bin/i18n/*.qm
  + Qt/MinGW 运行库（windeployqt 收集）+ librtlsdr.dll（真 RTL-SDR 支持）。

  链路纪律（2026-11-02 弹窗根因收口后固定）：
  * 默认 BuildDir = cpp/build-local——本机唯一 RTLSDR=FOUND 的树；
    stub 树打出的包没有真 RTL-SDR 支持，RTLSDR_LIBRARY 校验会显式拒绝。
  * librtlsdr.dll 不走 install() 规则（它不是构建产物），由本脚本从
    CMakeCache 的 RTLSDR_LIBRARY 推导目录拷进包；推导失败直接 throw，
    绝不静默打出缺 DLL 的包（此前 dist 里手工拷的 DLL 不可复现，就是教训）。
  * 压 zip 前必须冒烟：干净 PATH（仅系统目录）下启动 bin/mbdsdr.exe
    --snapshot 走 startup_robustness 同款自检路径，PNG 落盘非空才算过。
  * 顺带收集 platforms/qoffscreen.dll（headless 冒烟/无桌面环境用）。

  诚实口径：本脚本只做"构建+收集+打包"，不做安装器（无注册表/快捷方式）。

.EXAMPLE
  .\scripts\package_windows.ps1                          # 默认本机 Qt/MinGW 前缀
  .\scripts\package_windows.ps1 -QtRoot D:\Qt\6.8.2\mingw_64
  .\scripts\package_windows.ps1 -SkipBuild               # 复用已有 build-local/，只重打 zip
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
# 默认 RTLSDR=FOUND 的树：便携包必须带真 RTL-SDR 支持；stub 树会被下方
# RTLSDR_LIBRARY 校验显式拒绝（而不是静默打出无 RTL 的包）。
if (-not $BuildDir) { $BuildDir = Join-Path $cpp "build-local" }
$windeployqt = Join-Path $QtRoot "bin\windeployqt.exe"

if (-not (Test-Path $windeployqt)) {
    throw "找不到 windeployqt：$windeployqt（用 -QtRoot 指定 Qt 前缀）"
}

# 构建工具入 PATH（MinGW make / g++），与本地 ctest 基线一致。
# 注意：这只影响构建步骤；冒烟步骤会用干净 PATH 另行起进程。
$buildPath = "$MinGWBin;$(Join-Path $QtRoot 'bin');$env:PATH"
$env:PATH = $buildPath

# ---- 版本号：唯一事实源 mbdsdr_ai/VERSION（CMake/pyproject/spec 同源派生） ----
$versionFile = Join-Path $repo "mbdsdr_ai\VERSION"
if (-not (Test-Path $versionFile)) { throw "缺少版本单一事实源：$versionFile" }
$ver = (Get-Content $versionFile -Raw).Trim()
if ($ver -notmatch '^\d+\.\d+\.\d+') {
    throw "mbdsdr_ai/VERSION 内容不是 x.y.z 开头：$ver"
}
Write-Host "== MBDSDR $ver Windows 便携包 =="

# ---- RTL-SDR 校验（打包前置）：必须来自 RTLSDR=FOUND 的构建树 ----
$cacheFile = Join-Path $BuildDir "CMakeCache.txt"
if (-not (Test-Path $cacheFile)) {
    throw "构建树缺 CMakeCache.txt：$BuildDir（先跑一次 configure）"
}
$rtlLine = Select-String -Path $cacheFile -Pattern '^RTLSDR_LIBRARY:FILEPATH=(.*)$' |
    Select-Object -First 1
$rtlLib = if ($rtlLine) { $rtlLine.Matches[0].Groups[1].Value.Trim() } else { "" }
if (-not $rtlLib -or $rtlLib -match '-NOTFOUND$') {
    $msg = "构建树未启用真 RTL-SDR（RTLSDR_LIBRARY={0}）。便携包必须来自 RTLSDR=FOUND 的树" -f
        $(if ($rtlLib) { $rtlLib } else { "<未配置>" })
    throw ($msg + "（默认 cpp/build-local）；stub 树打出的包没有真 RTL-SDR 支持，拒绝打包。")
}
$rtlDir = Split-Path -Parent $rtlLib
$rtlDll = Join-Path $rtlDir "librtlsdr.dll"
if (-not (Test-Path $rtlDll)) {
    throw "RTLSDR_LIBRARY=$rtlLib 已找到，但同目录缺 librtlsdr.dll：$rtlDir"
}

# ---- 构建 ----
if (-not $SkipBuild) {
    if (-not (Test-Path $cacheFile)) {
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

# ---- librtlsdr.dll（真 RTL-SDR 运行库；位置由 CMakeCache 推导，缺了即红）----
Copy-Item $rtlDll (Join-Path $stage "bin\librtlsdr.dll") -Force
Write-Host "RTL-SDR: librtlsdr.dll <- $rtlDll"

# ---- platforms/qoffscreen.dll（headless 冒烟/无桌面环境；qwindows 已由 windeployqt 收集）----
$offscreenSrc = Join-Path $QtRoot "plugins\platforms\qoffscreen.dll"
if (Test-Path $offscreenSrc) {
    $platDir = Join-Path $stage "bin\platforms"
    New-Item -ItemType Directory -Path $platDir -Force | Out-Null
    Copy-Item $offscreenSrc (Join-Path $platDir "qoffscreen.dll") -Force
    Write-Host "platforms: qoffscreen.dll <- $offscreenSrc"
} else {
    Write-Warning "Qt 插件目录缺 qoffscreen.dll（headless 冒烟将不可用）：$offscreenSrc"
}

# ---- 冒烟（压 zip 前；干净 PATH，模拟"换台机器双击"）----
# 单实例锁（QLockFile）：残留的 mbdsdr 进程会让冒烟静默变 no-op，先清场。
Get-Process -Name mbdsdr -ErrorAction SilentlyContinue |
    ForEach-Object { throw "残留 mbdsdr 进程 PID $($_.Id)，先结束它再打包" }
$snap = Join-Path $dist "smoke_snapshot.png"
if (Test-Path $snap) { Remove-Item $snap -Force }
$smokeSavedPath = $env:PATH
try {
    # 干净 PATH：仅系统目录——Qt/MinGW/RTL 一律不借力，全靠包内 DLL。
    $env:PATH = "C:\Windows\System32;C:\Windows"
    $proc = Start-Process -FilePath (Join-Path $stage "bin\mbdsdr.exe") `
        -ArgumentList "--snapshot", "`"$snap`"" `
        -WorkingDirectory (Join-Path $stage "bin") -PassThru
    if (-not $proc.WaitForExit(90000)) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        throw "冒烟超时（90 s）：mbdsdr.exe --snapshot 未退出"
    }
    if ($proc.ExitCode -ne 0) {
        throw "冒烟退出码 $($proc.ExitCode) 非 0"
    }
} finally {
    $env:PATH = $smokeSavedPath
}
if (-not (Test-Path $snap) -or (Get-Item $snap).Length -eq 0) {
    throw "冒烟失败：--snapshot 未落盘非空 PNG（$snap）"
}
Write-Host ("冒烟通过：--snapshot PNG {0} 字节（干净 PATH，包内运行库自洽）" -f (Get-Item $snap).Length)

# ---- 压 zip + SHA256 ----
$zip = Join-Path $dist "mbdsdr-$ver-win64-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$zip.sha256" -Value "$sha  $(Split-Path $zip -Leaf)"

Write-Host "产物: $zip"
Write-Host "SHA256: $sha"
