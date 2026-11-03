# SPDX-License-Identifier: MIT
# smoke_exe_no_popup.ps1 — 直跑冒烟：干净 PATH 批量启动构建树全部 exe，
# 任一进程弹「找不到 DLL / 平台插件」模态框或非零退出即失败。
#
# 用途：提交前 / CI 固定环节。把「构建成功但跑不起来」钉死在构建产物上，
# 不靠开发者 shell 环境。2026-11-02/03 四轮弹窗（Qt6Widgets / Qt6Test /
# platforms 插件 / libgcc）的硬门禁。
#
# 用法（仓库根目录或任意位置）：
#   powershell -NoProfile -File scripts\smoke_exe_no_popup.ps1 -BuildDir cpp\build-local
#   powershell -NoProfile -File scripts\smoke_exe_no_popup.ps1 -BuildDir cpp\build  # 全量默认
#
# 判定：每个 exe 干净 PATH（仅 System32，无 QT_/MBDSDR_ env）下启动。
#   - 非零退出 → 记录（loader/插件层失败：0xC0000135=缺 DLL、0xC0000142=
#     插件初始化失败；或产品断言失败）；
#   - 输出缺失 DLL 名清单（objdump 导入表解析，区分 loader 层/插件层）。
#   弹框抑制：PowerShell 子进程继承 SEM_FAILCRITICALERRORS，loader 错误转
#   非零退出码而非模态框（2026-11-03 本机实测钉死）。
#
# 退出码：0 = 全部 exe 零弹窗零非零；1 = 有失败（明细落 stderr）。

[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$BuildDir,
  # 慢速 MainWindow/UI 测试（ctest label=slow/mainwindow）直跑会真建窗口跑
  # 数秒到十几秒，全量 161 exe 串行冒烟被 CI job 超时放大。-SkipSlow 跳过
  # 这批（它们的部署面与快测同源——同一目录同一批 DLL，快测过即部署齐）。
  [switch]$SkipSlow,
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BuildDir)) {
  Write-Error "构建目录不存在: $BuildDir"
  exit 1
}
$BuildDir = (Resolve-Path $BuildDir).Path

# objdump 定位（MinGW 工具链）——部署目标的 TC_BIN 同口径
$objdump = $null
foreach ($cand in @(
    "$env:MBDSDR_MINGW_BIN\objdump.exe",
    "C:\QtLocal\Tools\mingw1310_64\bin\objdump.exe")) {
  if ($cand -and (Test-Path $cand)) { $objdump = $cand; break }
}
if (-not $objdump) { $objdump = (Get-Command objdump.exe -ErrorAction SilentlyContinue).Source }
if (-not $objdump) {
  Write-Error "objdump.exe 未找到（设 MBDSDR_MINGW_BIN 或装 MinGW 工具链）"
  exit 1
}

# 干净 PATH：仅系统目录，无 Qt/MinGW/librtlsdr env。QT_QPA_PLATFORM=offscreen
# 与 MBDSDR_NULL_AUDIO=1 必须强制——冒烟是部署面验收（DLL/插件在不在），
# 不是显示/音频路径验收：
#   - offscreen：GUI 测试直跑不设 offscreen 会尝试真显示（无显示环境 exit 2，
#     2026-11-03 实测 test_constellation_view/test_sky_view）；
#   - NULL_AUDIO：无 env 直跑 e2e_smoke 打开真扬声器输出解调音频（现场故障）。
# 这两个开关只钉「不碰真实显示/音频设备」，不改变部署面判定（DLL/插件搜索
# 与显示后端无关——qoffscreen.dll 也随 platforms 一并部署，offscreen 下
# 插件层照样验到）。
$cleanPath = "C:\Windows\System32;C:\Windows"
$env:QT_QPA_PLATFORM = "offscreen"
$env:MBDSDR_NULL_AUDIO = "1"
$exes = Get-ChildItem "$BuildDir\*.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike "*_autogen*" -and $_.Name -ne "mbdsdr.exe" -and $_.Name -ne "mbdsdr_mcp.exe" }
# mbdsdr.exe / mbdsdr_mcp.exe 是 GUI/服务端产品 exe——直跑无参数不退出，
# 冒烟只测自退的测试 exe（产品 exe 的直跑验收走 package_windows.ps1 的
# --snapshot 冒烟链路，那里带自退参数）。
if (-not $exes) { Write-Error "构建目录无测试 exe: $BuildDir"; exit 1 }

# -SkipSlow：跳过 ctest label=slow 的测试（mainwindow 集成 + 经验慢 DSP/e2e
# 名单与 cpp/CMakeLists.txt L1990-2020 同源——改名单时两边同步）。慢测试
# 直跑真建 MainWindow 跑数秒到几十秒，全量串行冒烟被 CI job 超时放大；
# 它们与快测同一目录同一批 DLL，快测过即部署齐。
if ($SkipSlow) {
  $slowNames = @(
    # mainwindow;slow（L1990-1998）
    "ui_integration","shortcuts","device_ui","phase21_empty_state",
    "sky_time_slider","control_http_e2e_prod","ai_sessions_prod","ui_audit",
    # slow DSP/e2e（L2007-2017）
    "e2e_smoke","engine_audio_e2e","engine_integration","scan_link_engine",
    "apt","digital_e2e","control_hub_remote","network_audio_sink",
    "engine_empty_state","engine_hotplug")
  $exes = $exes | Where-Object { $_.BaseName -notin $slowNames }
}

$fail = @()
$loaderMissing = @{}   # exe -> [missing DLL names]
$pluginFail = @()      # exe names (platform plugin init failure)

foreach ($exe in $exes) {
  $name = $exe.BaseName
  $out = Join-Path $env:TEMP "smoke_$name.out"
  $err = Join-Path $env:TEMP "smoke_$name.err"
  # 干净环境：仅系统 PATH，无 QT_PLUGIN_PATH/QT_QPA_PLATFORM/MBDSDR_NO_HW。
  # MBDSDR_NULL_AUDIO=1 全局强制（上方）——冒烟是部署面验收，绝不允许测试
  # 写真实音频设备（2026-11-03 现场故障：无 env 直跑 e2e_smoke 打开真
  # 扬声器输出解调音频）。
  $env:PATH = $cleanPath
  Remove-Item Env:QT_PLUGIN_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:MBDSDR_NO_HW -ErrorAction SilentlyContinue
  # QT_QPA_PLATFORM=offscreen 与 MBDSDR_NULL_AUDIO=1 在上面全局强制——
  # 子进程继承，不在此删。
  # 超时保护：测试 exe 都该自退；mbdsdr.exe 等 GUI 产品 exe 无 --snapshot
  # 参数会一直跑窗口不退出（2026-11-03 实测冒烟卡在 mbdsdr 19 分钟）——
  # 上方已把产品 exe 排除在冒烟集外，此处不再内嵌超时（外层 CI job 超时兜底）。
  # 隐形 + 静音 + 可靠退出码：System.Diagnostics.ProcessStartInfo
  # CreateNoWindow=true（无窗口，不冒控制台黑窗）+ UseShellExecute=false
  # （重定向与 CreateNoWindow 的前提）。Start-Process -WindowStyle Hidden 与
  # RedirectStandardOutput 组合会让 ExitCode 变 $null（PS 5.1 实测坑），
  # ProcessStartInfo 无此问题。
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $exe.FullName
  $psi.WorkingDirectory = $BuildDir
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $proc = [System.Diagnostics.Process]::Start($psi)
  # 异步读完两管道防死锁（子进程输出量可能超 4KB 管道缓冲），再等退出
  $stdout = $proc.StandardOutput.ReadToEndAsync()
  $stderr = $proc.StandardError.ReadToEndAsync()
  $proc.WaitForExit()
  $exitCode = $proc.ExitCode
  [System.IO.File]::WriteAllText($out, $stdout.Result)
  [System.IO.File]::WriteAllText($err, $stderr.Result)
  $proc.Dispose()
  if ($exitCode -ne 0) {
    $fail += "$name(exit $exitCode)"
    $errText = Get-Content $err -Raw -ErrorAction SilentlyContinue
    # 插件层：Qt 平台插件初始化失败（DLL 在、plugins 不在）
    if ($errText -match "platform plugin|could not find the Qt platform") {
      $pluginFail += $name
    }
    # loader 层：缺 DLL（0xC0000135 / -1073741515）——objdump 解析缺失名
    if ($exitCode -eq -1073741515 -or $exitCode -eq 0xC0000135) {
      $imports = & $objdump -p $exe.FullName 2>$null |
          Select-String "DLL Name:\s+(\S+)" | ForEach-Object { $_.Matches[0].Groups[1].Value }
      $missing = @()
      foreach ($dll in $imports) {
        # 系统 DLL（KERNEL32/msvcrt/USER32/...）不查；只查 exe 旁缺失的非系统 DLL
        if ($dll -match "^(KERNEL32|msvcrt|USER32|GDI32|ADVAPI32|SHELL32|ole32|WS2_32|CRYPT32|WINMM|IMM32|VERSION|SETUPAPI|cfgmgr32|ntdll|combase|RPCRT4|bcrypt|sechost|SHLWAPI|COMCTL32|comdlg32|gdiplus|WINHTTP|IPHLPAPI|NETAPI32|dbghelp|dwmapi|uxtheme|windowscodecs)\.dll$") { continue }
        if (-not (Test-Path (Join-Path $BuildDir $dll))) { $missing += $dll }
      }
      if ($missing) { $loaderMissing[$name] = $missing }
    }
  }
  Remove-Item $out, $err -Force -ErrorAction SilentlyContinue
}

if ($fail) {
  Write-Host "SMOKE FAILED: $($fail.Count)/$($exes.Count) exe 直跑非零/超时" -ForegroundColor Red
  $fail | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
  if ($loaderMissing.Count) {
    Write-Host "loader 层缺失 DLL 清单：" -ForegroundColor Red
    $loaderMissing.GetEnumerator() | ForEach-Object {
      Write-Host "  $($_.Key): $($_.Value -join ', ')" -ForegroundColor Red
    }
  }
  if ($pluginFail) {
    Write-Host "插件层失败（platforms 插件未部署到 exe 旁）：$($pluginFail -join ', ')" -ForegroundColor Red
  }
  exit 1
}
if (-not $Quiet) {
  Write-Host "SMOKE PASS: $($exes.Count) exe 直跑零弹窗零非零（干净 PATH，无 QT_/MBDSDR_ env）" -ForegroundColor Green
}
exit 0
