# 待决 patch：§5.8.1 十条「确认无用」公开符号

> 状态：**仅计划，未改任何代码**。依据 `docs/redundancy-register.md` §5.8（口径见该节）。
> 纪律：一批一文件、可单独 `git revert`；每批提交前跑该模块测试；删除后 `git grep -w <symbol>` 应为 0。

## A. 真可删（5 条）——依赖路径已被现有测试覆盖

| # | 符号 | 位置 | 删了谁可能受影响 | 删除判据 |
|---|---|---|---|---|
| A1 | `build_short_frame` | `mbdsdr_ai/adsb_lite.py:81` | 无调用方（全仓仅定义行）；短帧不在 `decode_baseband` 主链，长帧构造与 CRC-24 不受影响 | `tests/test_adsb_dump1090_golden.py`、`tests/test_adsb_cross_impl_parity.py`、`tests/test_digital_decode_ui.py` 全绿；`python -c "import mbdsdr_ai.adsb_lite"` 通过 |
| A2 | `grid_resolution_pix_per_deg` | `mbdsdr_ai/astronomy/moon_albedo.py:75` | 无调用方；一行委托 `albedo_meta`，月面几何走 `screen_to_disc`/`albedo_at_disc` | `tests/test_moon_albedo.py` 全绿；删除后 `git grep -w` = 0 |
| A3 | `audio_info_from_service` | `mbdsdr_ai/dab_plus_lite.py:482` | 无调用方；FIC→服务列表由既有解析链产出，删除只去掉未接线的音频信息构造入口 | `tests/dab_plus_test.py` 全绿 |
| A4 | `hard_tones_to_llr` | `mbdsdr_ai/ft8_decode.py:130` | 无调用方；FT8 解码主链用 `soft_tones_to_llr`，硬判决只在离线实验里用过 | `tests/ft8_real_roundtrip.py`、`tests/test_ft8_cross_impl_fixture.py`、`tests/test_ft8_wsjtx_golden.py` 全绿 |
| A5 | `convolutional_interleave` | `mbdsdr_ai/meteor_sat.py:352` | 无调用方；LRPT 主链不复用该交织器（Meteor 帧构造走 `lrpt_find_frames`） | `tests/apt_meteor_roundtrip.py`、`tests/test_decode_router_honesty.py` 全绿 |

## B. 需先补测试再删（3 条）——删除会静默改变/抹掉一条策略

| # | 符号 | 位置 | 删了谁可能受影响 | 删除判据 |
|---|---|---|---|---|
| B1 | `load_ground_station_config` | `desktop/rf_sky_view.py:1334` | 承载"环境变量优先 + 配置文件回退"的地面站读取策略。当前 `main_window.py` 有写入、无人读取——直接删会把"写了没人读"的集成缺口一起抹掉，以后无从发现 | 先补一条行为测试（未配置→`None`、非法 lat/lon→`None`、合法→GeoPoint），并核对写入侧口径；若产品确认不做"用户自设地面站"，再连同写入侧一起删 |
| B2 | `nav_bits_from_corr` | `mbdsdr_ai/gps_ca.py:319` | 导航比特提取（多参数、含门限策略）。`tests/test_gps_prn.py` 只钉 PRN 生成，删除无法被现有测试发现 | 先用已知 C/A 相关向量补一条对拍（或确认由 GNSS 解码线替代该功能），测试过了再删 |
| B3 | `ecef_state_to_keplerian` | `mbdsdr_ai/orbit_determination.py:796` | ECEF→开普勒根数换算，是对外数值 API 的候选；`test_orbit_determination.py`/`test_orbit_material_dataset.py` 未钉该换算 | 补一条已知向量测试（ISS 单点 ECEF → `(a,e,i,Ω,ω,ν)`）并过；或确认轨道线已有替代实现后删 |

## C. 入口角色不动（2 条）

| # | 符号 | 位置 | 不动理由 |
|---|---|---|---|
| C1 | `select_decoder` | `mbdsdr_ai/decoder_registry.py:138` | 注册表的"按模式选择"公开入口；零文本命中不代表外部/CLI 不会按名调用，且 `test_modularization_audit.py` 依赖注册表完整性 |
| C2 | `fy4_lrit_pipeline` | `mbdsdr_ai/fengyun_sat.py:669` | FY-4 LRIT 流水线入口；`test_fy4_iq_chain.py` 覆盖的是链路零件，不能用"零件有测试"推导"入口可删" |

## 执行顺序（建议）

1. A2 / A3（纯辅助、无策略）→ A1 / A4 / A5（帧/解调辅助）→ 每条独立提交；
2. B3 → B2（补测试后再删）→ B1（需产品口径确认，最后做或不做）；
3. C1 / C2 保持登记，不进入删除队列；若日后出现调用方，从 §5.8 移出。
