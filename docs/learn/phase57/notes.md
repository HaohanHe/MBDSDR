# Phase57 校准工具链 + 真机一键验收

HEAD=5e94140 接手。

## 块1：校准工具 `tools/calibration.py`（参数化参考，零硬编码）
- 频率：`measure_peak_hz(iq,fs)` FFT 谱线峰；`ppm_correction(measured, ref)`：
  `ppm = (measured-ref)/ref*1e6`，correction=-offset。ref 由调用方传入，ref<=0 拒绝。
- 电平：`measure_dbfs(iq)` RMS→dBFS；`level_offset_db(measured, ref)` → 增益修正。
- JSON 输出 `--cal-json`（路径参数化）。空 IQ/读失败诚实 FAIL+下一步。
- **注入实测**：750Hz 单音 FFT 测峰=750Hz；幅值 0.5→-6.02 dBFS（与理论 -6.0206 差<0.01）。

## 块2：一键验收（扩展既有 acceptance_run.sh --event，不另起脚本）
- 新增步骤 ③ 校准（在 diag 后、onboard 前），参数 `--cal-ref-hz / --cal-ref-dbfs / --cal-json`。
- 云内空态实测：`overall=NO_HARDWARE device_present=False PASS=2 FAIL=1 SKIP=4`，
  校准步诚实 FAIL（无设备不伪造校准），脚本不崩、退出正常。
- --json 汇总走既有 `--out` 检查表（acceptance_lib.assemble/validate）。

## 块3：回归
核心 `mbdsdr_ai/tests + onboarding` = **191 passed/7 skipped 不回归**；
含 tools（校准+验收）全量 = 239 passed/7 skipped。未动 mobile/cpp。

## 未解决
- 自动校准需参考 IQ 录制件（真机在场先录），云内只能跑空态 FAIL；真机过境未跑。
