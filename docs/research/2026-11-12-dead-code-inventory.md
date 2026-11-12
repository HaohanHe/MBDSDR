# 死代码清单（只报不改，2026-11-12）

> 结论：`mbdsdr_ai/` + `desktop/` 共 6634 个模块级定义（类 / 模块级函数 / 顶层常量），
> 其中 **381 个在生产代码里零引用**：365 个「零引用且无反射迹象」、16 个「零引用但可能
> 反射」；另有 **406 个「仅测试/脚本引用」**。
>
> 起点核对：`frequency_manager_v2` 比设置线报的更彻底——**整个模块只被
> `tests/test_frequency_manager_v2.py` 导入**，`FrequencyManagerV2`
> （[91](../../mbdsdr_ai/frequency_manager_v2.py#L91)）零实例化；同文件的
> `BookmarkV2` / `FILE_VERSION` 虽有 10/5 次「生产引用」，但**全部是同文件自引用**，
> 不是外部消费者。所以它是「模块级未接线」，不是「类没接上」。
>
> 四个「整模块零生产引用」的模块（`anr_iq` / `gain_calibration` / `vfo_enhanced` /
> `vna_avg`）都是**功能实现完整、只有测试在用**——按「未完成的需求」处理，不是垃圾。
> 本清单**不删任何东西**。
>
> 比单条更值得看的结构性事实：**58 个模块在静态上没有任何生产导入**，其中 51 个是
> 功能模块（`background_decoder`、`channel_analyzer`、`device_watchdog`、`s_meter`、
> `recording_library`…）。但 `pkgutil` 自动发现让「没有 import 语句」不等于「运行时
> 没加载」，所以这也不是删除清单，是「没有 UI 接线点」的清单（§2.4）。
>
> 证据级别：E1（可复跑脚本 + 全量 JSON 明细）。静态分析的已知盲区见 §1.4，
> 「未找到引用」**不等于**「可以删」。

---

## 1. 口径

### 1.1 扫描范围

- 只扫 `mbdsdr_ai/` 与 `desktop/`（含 `mbdsdr_ai/tests/` 与 `desktop/tests/` 作为「测试」桶）。
- 定义类型：模块级 `class`、模块级 `def`（含 `async`）、顶层 `UPPER_CASE` 常量
  （`_PRIVATE` 也算，因为私有符号最可能是遗留）。
- 不计入：类内方法、函数内嵌套函数、小写模块级变量、`__dunder__`。

### 1.2 三档判定

| 档 | 判定 | 数量 |
| --- | --- | --- |
| 第一档 | `prod == 0` 且 **无**反射迹象（字符串 / 动态标记 / 数据文件命中） | 365 |
| 第二档 | `prod == 0` 且 **有**反射迹象（一律不判死） | 16 |
| 第三档 | `prod == 0`，但被 `tests/` 或 `scripts/` 引用 | 406 |

「生产引用」= 来自 `mbdsdr_ai/` 或 `desktop/` 的引用；
`tests/`（含 `mbdsdr_ai/tests/`、`desktop/tests/`）、`scripts/`、`docs/`、`experiments/`、
`tools/`、`web/` 一律**不算生产引用**。

### 1.3 反射迹象的判定（第二档的唯一来源）

名字命中以下任一项，即记「可能反射，需人工确认」，**不判死**：

- 字符串字面量里出现该名字（`getattr(x, "name")`、工具注册表 `name="..."`、日志/错误文案）；
- 出现在含 `getattr(`、`setattr(`、`hasattr(`、`eval(`、`exec(`、`importlib`、
  `__import__`、`QMetaObject`、`setProperty`、`invokeMethod`、`globals()`、`locals()`、
  `plugin`/`registry`/`register` 字样的行上；
- 名字出现在 `.ui` / `.json` / `.qss` / `.js` / `.html` 等数据文件中。

已经坐实的两个反射入口（所以相关定义不能判死）：

- [cli.py:264-269](../../mbdsdr_ai/core/cli.py#L264-L269)：`pkgutil.iter_modules` +
  `importlib.import_module` + `getattr(mod, "register_tool_registry")`，自动调用
  `mbdsdr_ai.*` 里每个模块的注册函数；
- [\_\_init\_\_.py:203-225](../../mbdsdr_ai/__init__.py#L203-L225)：PEP 562
  `__getattr__` 按 `_EXPORTS` 字符串表懒加载子模块符号（`"write_cs16": "dsp"` 这类）。
- 另有 `plugin_manager.py` 按 `plugin.json` 的 `"module:Class"` 字符串加载插件类。

### 1.4 静态分析的局限（必须读，否则会误用本清单）

1. **`obj.func()` 只能靠兜底**：调用点不是导入别名时（实例方法、`self.x.y()`、
   三层属性），只在「本文件导入过的模块」里找同名模块级定义。漏判方向是
   **把活代码误报成零引用**。
2. **反射/字符串表只能标记，不能证明**：第二档的 16 条都是「有迹象」，不是「确认被调」。
3. **UI 可达性不可靠**：`desktop/main.py` / `main_window.py` 出发的 import 图 BFS
   对 `importlib` 动态加载、包别名覆盖不足。本清单**不**用「UI 不可达」做删除依据，
   只报可直接人工核验的类（§6）。
4. **同名不同物**：字符串命中可能是巧合（如 `window="hann"` 命中了 `fir_taps.hann`），
   第二档逐条标了证据位置，需人工看一眼。
5. 变量表 / 字典键 / CSV 列名里的引用不计；跨进程（外部脚本、用户配置）的引用不计。

### 1.5 与上一版数字的差异（口径修正，不是结论反复）

上一版（v3.1）得到 371 / 35 / 450，v3.2 修正了两处系统性漏判后变为 365 / 16 / 406：

- `from . import preconnect` 这类**相对导入子模块**此前被整条丢弃，导致
  `preconnect.clear()` / `preconnect.describe()`（真的在
  [sdr_tools.py:3205](../../mbdsdr_ai/sdr_tools.py#L3205) 里被调用）被记成零引用；
- `from pkg import submodule` 的子模块仅被记成别名，未记「模块被导入」。

修正后共 **69 条**从候选里移出（第三档 −44、第二档 −19、第一档 −6），回到「生产在用」；
`register_tool_registry` 这一批（24 处）也全部由
[tool_registry.py](../../mbdsdr_ai/tool_registry.py) 逐个模块直接调用或经 pkgutil
自动发现，**不属于死代码**（只剩 `sync_tools.py:258` 是仅测试引用）。

复现：临时分析脚本不入库；明细 JSON 与三档纯文本保存在仓库外
（`d:\mbdsdr\w92_deadcode32.json`、`w92_T1v32.txt`、`w92_T2v32.txt`、`w92_T3grouped.txt`），
本清单所有「文件:行」均由 AST 定位，不是 grep 猜的。

---

## 2. 第一档：零引用且无反射迹象（365 条 / 99 个模块）

读法：同一模块的条目共用该行的判断（判断写在模块级）；**粗体**是私有符号，
私有 + 零引用是最强的「遗留」信号。模块路径相对仓库根。

### 2.1 `mbdsdr_ai/` 协议与格式常量表

| 模块 | 条目（定义:行） | 判断 |
| --- | --- | --- |
| `mbdsdr_ai/ax25.py` | `AX25_PID_IP:28` `AX25_CTRL_SABME:30` `AX25_CTRL_DISC:31` `AX25_CTRL_UA:32` `AX25_CTRL_DM:33` `KISS_CMD_DATA:53` `KISS_CMD_TXDELAY:54` `KISS_CMD_P:55` `KISS_CMD_SLOTTIME:56` `KISS_CMD_TXTAIL:57` `KISS_CMD_FULLDUPLEX:58` `KISS_CMD_SETHARDWARE:59` `KISS_CMD_RETURN:60` `APRS_POSITION:64` `APRS_POSITION_MSG:65` `APRS_POSITION_TIME:66` `APRS_POSITION_TIME_MSG:67` `APRS_STATUS:68` `APRS_MESSAGE:69` `APRS_WEATHER:70` `APRS_OBJECT:71` `APRS_ITEM:72` `APRS_TELEMETRY:73` `APRS_QUERY:74` `APRS_MIC_E:75` `APRS_USERDEF:76` `APRS_THIRDPARTY:77` `APRSWeather:800` | 模块被 8 个生产文件导入（aprs_panel / protocol_stack / dvbs2_datafield 等），但只用到帧构造与解析函数；这些是 AX.25/KISS/APRS 协议字典，取值在函数里**内联写字面量**，常量本身没人读。属「文档化的魔法数字」，删了不改变行为、但会让协议对照变难。 |
| `mbdsdr_ai/ysf_demod.py` | `YSF_FRAME_LENGTH_BYTES:42` `YSF_SYNC_LENGTH_BYTES:43` `YSF_FICH_LENGTH_BYTES:44` `YSF_FI_COMMUNICATIONS:52` `YSF_FI_TERMINATOR:53` `YSF_FI_TEST:54` `YSF_DT_VD_MODE1:57` `YSF_DT_VD_MODE2:59` `YSF_DT_VOICE_FR_MODE:60` `YSF_CM_GROUP2:64` `YSF_CM_INDIVIDUAL:65` `YSF_MR_NOT_BUSY:69` `YSF_MR_BUSY:70` `DCH_INTERLEAVE_TABLE_5_20:127` `bits_to_byte_be:165` | **整个模块只被 `tests/test_ysf_demod_roundtrip.py` 导入**，生产零引用。YSF 数字语音是完整实现（编解码 + IQ），属「实现完了没接进解码路由」，不是垃圾。同模块 `ysf_encode_iq` / `ysf_decode_iq` 落第三档。 |
| `mbdsdr_ai/kismet_adapter.py` | `DOT11_TYPE_UNKNOWN:34` `DOT11_SUB_ASSOC_REQ:41` `DOT11_SUB_ASSOC_RESP:42` `DOT11_SUB_REASSOC_REQ:43` `DOT11_SUB_REASSOC_RESP:44` `DOT11_SUB_ATIM:48` `DOT11_SUB_PSPOLL:55` `DOT11_SUB_DATA:61` `DOT11_SUB_DATA_NULL:62` `DOT11_SUB_QOS_DATA:63` `IE_TAG_COUNTRY:93` `IE_TAG_HT_CAP:97` | 802.11 子类型/IE 标签常量，解析器只处理其中少数几种；未被消费的子类型属「协议覆盖没做完」，跟着解析器一起留着更安全。 |
| `mbdsdr_ai/m17_adapter.py` | `M17_SYMBOLS_PER_FRAME:30` `M17_FRAME_MS:32` `M17_PAYLOAD_BITS:34` `M17_SYNC_STREAM:39` `M17_SYNC_LSF:40` `M17_SYNC_DATA:41` `M17_CONV_K:52` `M17_PUNCTURE_P1:57` `M17_PUNCTURE_P2:58` `golay24_encode:317` `m17_scramble:402` | M17 帧/卷积编码常量 + 两个编码原语；模块在用，但 Golay/加扰只在文档与测试里出现，属实现留档。 |
| `mbdsdr_ai/op25_adapter.py` | `P25_FRAME_SYNC_REV:30` `P25_FRAME_SYNC_MASK:32` `P25P2_FRAME_SYNC_REV:36` `P25P2_FRAME_SYNC_MASK:37` `P25_DUID_HDU:40` `P25_DUID_TDU:41` `P25_DUID_TSBK:43` `P25_DUID_LDU2:44` `P25_DUID_PDU:45` `P25_DUID_TDULC:46` `p25_imbe_pack_params:144` | P25 DUID/同步字常量，实际同步用内联字面量；`p25_imbe_pack_params` 零调用，属 P25 编解码半成品。 |
| `mbdsdr_ai/goes_lrit.py` | `VCDU_DATA_LEN:58` `H_IMAGE_NAVIGATION:258` `H_IMAGE_DATA_FUNC:259` `H_TIMESTAMP:261` `H_ANCILLARY_TEXT:262` `H_KEY:263` `H_NOAA_LRIT:265` `H_HEADER_STRUCTURE:266` `H_DCS_FILENAME:268` `RiceDecoder:624` | 头字段 key 常量与 Rice 解码器类均零引用；帧解析走的是硬编码偏移，这些 key 是「按规范命名但没用上」，属读代码时的对照表。 |
| `mbdsdr_ai/fengyun_sat.py` | `FY4A_SUB_LONGITUDE_DEG:82` `FY4B_SUB_LONGITUDE_DEG:84` `FY4_HRIT_FREQ_HZ:89` `FY4A_HRIT_SYMBOL_RATE:96` `FY4_RRC_ALPHA:98` `FY4_H_IMAGE_NAVIGATION:113` `FY4_H_KEY:114` `fy4_lrit_pipeline:669` `FY3D_CATNR:754` `FY3_ORBIT_ALT_KM:756` `DVBS2_SOF_VALUE:1093` `DVBS2_SOF_LEN:1094` `DVBS2_PLS_LEN:1095` `DVBS2_PLHEADER_LEN:1096` `DVBS2_FRAME_NORMAL_BITS:1097` `DVBS2_FRAME_SHORT_BITS:1098` | 风云卫星 HRIT/LRIT 参数与 DVBS2 帧长常量；`fy4_lrit_pipeline` 是整条流水线入口，**没有任何生产代码调用**（测试走的是分步函数）——要么是给上层预留的入口，要么是入口被别处内联实现取代，需人工确认后决定「接上」还是「删」。 |
| `mbdsdr_ai/dsdcc_lite.py` | `SYMBOL_RATE_2400:28` `DSD_SAMPLE_RATE:30` `SPS_2400:34` `RRC_ALPHA_DMR_FALLBACK:41` `DMR_SYNC_LEN_BITS:89` `DMR_CACH_LEN:90` `DMR_EMB_PART_LEN:92` `DMR_ES_LEN:93` `DMR_OFF_CACH:105` | DMR 帧结构与符号率常量，取值在解调函数里内联；模块在用，常量没用。 |
| `mbdsdr_ai/dmr_demod.py` | `DMR_FRAME_LENGTH_BITS:30` `DMR_FRAME_LENGTH_BYTES:31` `DMR_SYNC_LENGTH_BITS:32` `DT_VOICE_PI_HEADER:48` `DT_CSBK:51` `DT_DATA_HEADER:52` `DT_IDLE:53` `FLCO_USER_USER:57` | 同上：DMR 数据突发类型常量，解调器只覆盖语音路径。 |
| `mbdsdr_ai/rtcm3_decoder.py` | `PRUNIT_GPS:33` `PRUNIT_GLO:35` `P2_34:42` `P2_46:44` `P2_59:46` `P2_66:48` `P2_24:55` | RTCM3 缩放因子常量；解码器用位移/乘法内联，常量说明算法来源但未被读。 |
| `mbdsdr_ai/rtlsdr_params.py` | `TUNER_ENUM_TO_NAME:38` `SAMPLE_RATE_LOSS_WARN_HZ:136` `RTL_DIGITAL_AGC_REG:158` `RTL_DIGITAL_AGC_ON:159` `RTL_DIGITAL_AGC_OFF:160` `DIRECT_SAMPLING_OFF:168` `DIRECT_SAMPLING_MAX_HZ:171` | 调谐器枚举名映射与直采/AGC 寄存器常量；`TUNER_ENUM_TO_NAME` 零引用说明**调谐器名字显示没接上**（参数面有表、UI 没用），值得单独确认。 |
| `mbdsdr_ai/bladerf_params.py` | `BLADERF2_SAMPLERATE_STEP_HZ:101` `BLADERF2_TX_GAIN_STEP_DB:145` `BLADERF_CHANNEL_TX0:158` `BLADERF_ERR_UNEXPECTED:166` `BLADERF_ERR_RANGE:167` `BLADERF_ERR_INVAL:168` `BLADERF_ERR_TIMEOUT:169` `BLADERF_ERR_NODEV:170` | BladeRF2 步进与错误码；错误码只在测试里被断言，生产没把 libbladeRF 错误映射到人话，属**能力缺一块**的信号。 |
| `mbdsdr_ai/freedv_modem.py` | `OFDM_700D_TS:18` `OFDM_700D_TCP:20` `OFDM_700D_EDGE_PILOTS:25` `OFDM_700D_TXTBITS:27` `DBPSK_1600_SYMS_PER_FRAME:32` `FreeDVModem:323` | `tool_registry` 导入本模块，但 `FreeDVModem` 类从未实例化——模式注册了、实现没接。属未接线需求。 |
| `mbdsdr_ai/nrsc5_lite.py` | `PIDS_FRAME_LEN:30` `P1_FRAME_LEN_FM:31` `NRSC5_SAMPLE_RATE_CU8:34` `CONV_K7_N:39` `_COSTAS_LOOP_BW:43` `_COSTAS_DAMPING:44` | HD Radio 帧长/编码器常量，模块在用，常量没读。 |
| `mbdsdr_ai/rds_lite.py` | `RDS_TARGET_FS:29` `RDS_SPS:30` `RDS_DECIMATE:31` `RDS_DEVIATION_HZ:32` `RDS_GROUPS_PER_SEC:33` `RDS_POLY:41` | RDS 采样/多项式常量，实际用内联值；`RDS_POLY` 零引用说明 CRC 多项式另有实现。 |
| `mbdsdr_ai/ais_decoder.py` | `AIS_CHANNELS:34` `AIS_DEFAULT_BITS:36` `SOG_NOT_AVAILABLE:42` `COG_NOT_AVAILABLE:43` `HEADING_NOT_AVAILABLE:44` | AIS 双信道与「不可用」哨兵值；哨兵常量零引用说明解码后可能没按 AIS 规范处理「不可用」，值得核一下。 |
| `mbdsdr_ai/analog_demod.py` | `SDRPP_WFM_PILOT_BAND:61` `SDRPP_RDS_SUBCARRIER:63` `SDRPP_RDS_RESAMPLE_RATE:64` `SDRPP_AUDIO_SR:67` `_HangAGC:140` | 参数对齐 SDR++ 的常量 + 私有 AGC 类；`_HangAGC` 零引用是**遗留代码**（同类功能已换成别处 AGC）。 |
| `mbdsdr_ai/radio_control.py` | `text_to_cw_audio:90` `cw_decode_from_audio:152` `CIV_NAK:242` `CIV_CMD_SND_FREQ:245` `CIV_CMD_SND_MODE:246` `CIV_CMD_RD_MODE:248` `CIV_CMD_RD_TRXID:252` `CIV_SUB_RD_TX_FREQ:256` `CIV_RIG_ADDR:268` | CW 收发与 ICOM CI-V 命令集；CW 编解码零调用 = 「文本发报」功能注册了但没接 UI，属未完成需求。 |
| `mbdsdr_ai/ccsds_rx.py` | `CONV_RATE:48` `SAT_AFSK_2400_MARK:69` `SAT_AFSK_2400_SPACE:70` | 卷积码率与 AFSK 标记/空格频率，实际用内联。 |
| `mbdsdr_ai/dvbs2_pl.py` | `DUMMY_MODCOD:32` `RESERVED_MODCODS:33` | PLHEADER 的填充/保留 MODCOD 常量，编码器只走合法值，属规范留档。 |
| `mbdsdr_ai/codec2_lite.py` | `LSP_SCALAR_INDEXES:31` | Codec2 LSP 索引表，解码路径用别的方式取；私有性弱但确实无消费者。 |
| `mbdsdr_ai/navtex_decoder.py` | `NAVTEX_BITS_PER_CHAR:37` `CCIR476_FIGURES:59` | NAVTEX 字符位宽与 CCIR476 数字表；模块在用，数字表被内联。 |
| `mbdsdr_ai/lrpt_image.py` | `DATA_ZONE_LEN:65` | LRPT 数据区长度，渲染用硬编码。 |
| `mbdsdr_ai/lrpt_vcdu.py` | `LRPT_FRAME_SYNC:362` | 帧同步字，同步实现另有常量来源——**同名两处定义**，需确认哪处是权威。 |
| `mbdsdr_ai/ssdv_phy.py` | `DEFAULT_SYMRATE_HZ:31` `NOTCH_HALF_BINS:98` | SSDV 默认符号率与陷波半宽，调用方显式传参。 |
| `mbdsdr_ai/wspr_codec.py` | `CONSTRAINT_LENGTH:31` | WSPR 卷积约束长度，编码用字面量 32。 |
| `mbdsdr_ai/sigdigger_adapter.py` | `SU_CHANNEL_DETECTOR_ALPHA:38` `SU_CHANNEL_DETECTOR_BETA:39` `SU_CHANNEL_DETECTOR_GAMMA:40` `SU_PD_SIGNIF_DB:42` | Suscan 能量检测器参数，适配器只搬运 UI 值，不读这些常量。 |
| `mbdsdr_ai/cubicsdr_adapter.py` | `CUBIC_FREQ_DIGITS:38` `CUBIC_BW_DIGITS:39` `CUBIC_CENTER_DIGITS:40` | 显示位数常量，格式化在别处写死。 |
| `mbdsdr_ai/gqrx_receiver.py` | `GQRX_FILTER_PRESETS:47` `GQRX_FM_DEEMPH_TAU:55` | GQRX 滤波器预设与去加重时间常数，未接 UI。 |
| `mbdsdr_ai/acars_protocol.py` | `CENTER_FREQ_HZ:39` | ACARS 中心频率，调用方各自给频点。 |
| `mbdsdr_ai/solar_system.py` | `SPEED_OF_LIGHT_KM_S:45` `J2000_JD:46` | 常数副本；同仓 `new_spacetime.SPEED_OF_LIGHT`、`orbit_dynamics.C_LIGHT` 各有一份，属**常量多处复制**。 |
| `mbdsdr_ai/acquisition.py` | `GEO_RADIUS_M:43` | 地球半径，同仓多份副本之一。 |
| `mbdsdr_ai/gps_ca.py` | `CHIPS_PER_NAV_BIT:38` | GPS C/A 比特长度，相关器用 `20` 字面量。 |
| `mbdsdr_ai/blind_param_est.py` | `_N_CLASSES:34` | 私有常数无消费。 |
| `mbdsdr_ai/rtl433_decoder.py` | `PD_MIN_PULSES:40` | 脉冲检测最小脉宽，实现内联。 |
| `mbdsdr_ai/tle_fetch.py` | `SCHEDULED_INTERVAL_S:83` | 定时抓取间隔，调度方自带间隔。 |

### 2.2 `mbdsdr_ai/` 未被接线的类与函数

| 模块 | 条目（定义:行） | 判断 |
| --- | --- | --- |
| `mbdsdr_ai/astronomy.py` | `HOUR2RAD:30` `J2000_MJD:35` `EARTH_RADIUS_KM:38` `EARTH_FLATTENING:39` `FrameType:47` `jd_to_unix:191` `mjd_to_jd:201` `predict_satellite_pass:540` `get_observer_from_gps:673` | 模块在用，但 `FrameType` 枚举、`predict_satellite_pass`、`get_observer_from_gps` 零引用 → **过境预报另有一份实现**（`orbit.py` / `sat_passes.py`），此处是重复实现，删前需确认没有脚本在用。 |
| `mbdsdr_ai/dsp.py` | `QuadratureDemod:316` `RESAMPLER_KAISER_BETA:721` `RESAMPLER_FRACTIONAL_BW:722` `RESAMPLER_HALFBAND:723` `FFT_FILTER_FFTSIZE:728` `FFT_FILTER_NSAMPLES:729` `FFT_FILTER_TAILSIZE:730` `AGC2_GAIN_FLOOR:739` `make_agc2:742` | DSP 原语工具箱：模块主力在用，但这些原语没人调用 → 要么是给插件/外部留的 API，要么是搬自 GNU Radio 的未用件。 |
| `mbdsdr_ai/new_spacetime.py` | `SPEED_OF_LIGHT:24` `parse_nmea_sentence:648` `web_mercator_project:746` `format_dms:758` `SDRDataSource:1077` `GNSSDataSource:1104` | 模块被 17 个生产文件导入（时序/轨迹），但这两个「数据源」抽象类与 NMEA/投影工具零引用 → **抽象层没接上**，实现却完整。 |
| `mbdsdr_ai/osmosdr_source.py` | `BUILTIN_SOURCE_TYPES:151` `RTL_GAIN_NAMES_E4000:283` `osmosdr_list_backends:611` `SoapySDRSource:622` | SoapySDR 后端类与后端枚举零引用 → 只有 RTL 路径接了，Soapy 属未接线能力。 |
| `mbdsdr_ai/orbit_determination.py` | `GM_MOON:48` `ARTIFACT_DIR:53` `load_iq_file:197` `extract_doppler_observations:257` `_ecef_dynamics_aug:398` | 定轨工具箱，`load_iq_file` / `extract_doppler_observations` 是整条「用 IQ 反演轨道」的入口，零调用 → 功能没接进任何 UI/CLI。 |
| `mbdsdr_ai/rtklib_adapter.py` | `_norm:107` `_dot:112` `SatelliteOrbit:364` `klobuchar_ion_delay:432` `ntrip_manager_from_config:1033` | `_norm`/`_dot` 私有向量助手零引用（**死代码，可直接删**）；`ntrip_manager_from_config` 零引用说明 NTRIP 配置面没接上（`ntrip_connect` 在工具面可用，但管理入口没接）。 |
| `mbdsdr_ai/ardop_adapter.py` | `ARDOP_FRAME_MS:58` `_gray:75` `symbols_to_bits:130` `ARDOFFrame:155` | ARDOP 帧类与格雷映射零引用；适配器只暴露了工具面的一部分。 |
| `mbdsdr_ai/multimon_decoders.py` | `BCH_CODE_LEN:41` `POCSAG_BAUD:47` `PocsagWord:239` `pocsag_build_batch:267` | POCSAG 编码侧（构造码字/批）零引用 → 只做解码，无发射路径。 |
| `mbdsdr_ai/decode_pipeline.py` | `AIS_STAGE_NAMES:76` `ADSB_STAGE_NAMES:77` `APRS_STAGE_NAMES:78` | 阶段名表零引用 → 解码进度 UI 没接这些名字（直接写死文案）。 |
| `mbdsdr_ai/decoder_registry.py` | `select_decoder:138` `decode_audio:164` | **注册表的「选解码器」和「解音频」两个主入口零引用**。模块被 `desktop/capability_catalog.py` 导入，而该文件自己标注「含未接主调度的诚实标注」——即仓库已知这块没接完。属**未完成的功能**，不是垃圾。 |
| `mbdsdr_ai/fst4_ldpc.py` | `_N:13` `CRC24_POLY:30` `fst4_decode:126` | FST4 解码入口零引用，只有 LDPC 层被测试用。 |
| `mbdsdr_ai/fir_taps.py` | `highpass_taps:116` `bandpass_taps:131` | 滤波器设计函数零引用（低通在用），属工具箱留件。 |
| `mbdsdr_ai/ft8_lite.py` | `PERIOD_S:20` `BANDWIDTH_HZ:21` | 字面量重复定义。 |
| `mbdsdr_ai/ft8_encode.py` | `KK:16` | 常量零引用。 |
| `mbdsdr_ai/ft8_ldpc.py` | `get_colorder:27` | 列序助手零引用。 |
| `mbdsdr_ai/hooks.py` | `create_auto_record_hook:433` | 自动录制钩子零引用 → **「自动录制」功能定义了工厂但没挂上**。 |
| `mbdsdr_ai/js8call_adapter.py` | `tones_to_audio:190` | JS8 音频合成零引用（只做解码）。 |
| `mbdsdr_ai/openwebrx_adapter.py` | `owx_apply_waterfall:76` `owx_soapy_settings_parse:188` | OWRX 瀑布图应用/Soapy 设置解析零引用，适配器只做参数包装。 |
| `mbdsdr_ai/urh_adapter.py` | `grab_pulse_lens:249` `slice_bits_from_pulses:281` | URH 脉冲切片工具零引用。 |
| `mbdsdr_ai/serial_gnss.py` | `KNOWN_SENTENCE_TYPES:71` `NTRIPClient:895` | 语句类型表与 NTRIP 客户端类零引用 → NTRIP 客户端有**两套实现**（此处 + rtklib），未收敛。 |
| `mbdsdr_ai/sat_passes.py` | `_COARSE_STEP_S:119` `_FINE_STEP_S:120` `split_pass_window:132` `grid_manifest:156` | 过境窗口切分/网格清单零引用，私有步长也无消费。 |
| `mbdsdr_ai/openapi_integration.py` | `get_satellite_passes:177` | 对外 OpenAPI 包装零引用 → 接口定义了但没挂路由。 |
| `mbdsdr_ai/time_basis.py` | `reset_clock_guard:284` | 时钟守卫复位零引用 → 守卫只能进不能出，值得人工看一眼是不是缺陷。 |
| `mbdsdr_ai/sstv_decoder.py` | `decode_sstv_from_samples:1082` | 采样级 SSTV 入口零引用（生产走的是另一入口）。 |
| `mbdsdr_ai/sdr_tools.py` | `_decode_noaa_apt:4421` | 私有解码包装零引用，同文件另有工具面入口 → 遗留。 |
| `mbdsdr_ai/ssdv_decoder.py` | `_idct_8x8:924` | 私有 IDCT 零引用（用库实现替代）。 |
| `mbdsdr_ai/sat_image_processing.py` | `_is_uint8:54` | 私有类型判断零引用。 |
| `mbdsdr_ai/analysis/modulation_classifier.py` | `_kurtosis:57` | 私有统计量零引用。 |
| `mbdsdr_ai/fec.py` | `_poly_add:120` `_poly_mul:128` | 私有 GF(2) 多项式助手零引用 → 遗留。 |
| `mbdsdr_ai/crc.py` | `_to_bits:46` | 私有位转换零引用 → 遗留。 |
| `mbdsdr_ai/core/mcp_server.py` | `_noop:172` | 占位函数零引用 → 遗留。 |
| `mbdsdr_ai/lrpt_modem.py` | `_bytes_to_bits:108` | 私有位转换零引用。 |
| `mbdsdr_ai/orbit.py` | `WGS72_B:52` `refresh_tle_round:244` | 常量重复 + TLE 轮次刷新零引用。 |
| `mbdsdr_ai/module_graph.py` | `enumerate_sources:621` | 模块图的数据源枚举零引用。 |
| `mbdsdr_ai/usb_watchdog.py` | `UDEV_SUPPORTED:205` | 平台能力常量零引用。 |
| `mbdsdr_ai/digital_modes.py` | `demodulate_qpsk:229` | QPSK 解调入口零引用（viterbi 路径在用）。 |
| `mbdsdr_ai/dab_plus_lite.py` | `DABAudioInfo:473` | 音频信息结构体零引用。 |
| `mbdsdr_ai/device_capabilities.py` | `STATUS_LABELS:27` | 状态文案表零引用 → UI 自带文案，未复用。 |
| `mbdsdr_ai/adsb_map.py` | `WORLD_LAND_POLYGONS:175` | 世界陆地多边形数据零引用 → 地图底图换了实现，这是**大块遗留数据**。 |
| `mbdsdr_ai/hackrf_params.py` | `RX_ENDPOINT_ADDRESS:136` `TX_ENDPOINT_ADDRESS:137` `DEFAULT_BUFFER_SIZE:138` | USB 端点/缓冲常量，实际由 libhackrf 决定。 |
| `mbdsdr_ai/limesuite_params.py` | `LNA_GAIN_MIN_DB:116` `TIA_GAIN_MIN_DB:129` `TIA_GAIN_MAX_DB:130` | 增益范围常量；最大 LNA 与步进在测试里用，最小/ TIA 无人读。 |
| `mbdsdr_ai/satdump_adapter.py` | `LRPT_PLL_BW:53` `LRPT_CORR_SYNC:60` `HRPT_SCAN_ANGLE_DEG:78` | 解调参数常量，实现内联。 |
| `mbdsdr_ai/tx_guard.py` | `TX_AUDIT_MAX_BYTES:89` `TX_AUDIT_BACKUPS:92` `LIMIT_REG_UNCONFIGURED:879` | 审计容量常量与「限值未配置」状态位零引用 → 后者可能是**该报未报的分支**，值得确认。 |
| `mbdsdr_ai/astronomy/moon_albedo.py` | `MOON_RADIUS_KM:36` `REFERENCE_ALBEDO_SOURCE:43` | 半径常量与出处注释常量零引用。 |
| `mbdsdr_ai/gpredict_adapter.py` | `XJ2:41` `XJ4:43` `GE:51` `THDT:56` `jd_from_datetime_utc:139` | GPredict 时元常量与时间转换零引用 → 适配器只读不写时元。 |
| `mbdsdr_ai/tle_integrity.py` | `TRACKING_LEVELS:373` `TRACKING_OPERATIONS:374` `user_entries:594` | 跟踪等级/操作的映射表与用户条目读取零引用 → 完整性分级没接 UI。 |
| `mbdsdr_ai/nanovna_v2_proto.py` | `CMD_V2_INDICATE:34` `CMD_V2_INDICATE_ACK:35` | 命令码零引用，实现只用了子集。 |

### 2.3 `desktop/` 侧

| 模块 | 条目（定义:行） | 判断 |
| --- | --- | --- |
| `desktop/settings.py` | `_FLOAT_KEYS:58` `_INT_KEYS:60` `_BOOL_KEYS:62` | 三张「键 → 类型」表**只定义、从未被读**。settings 被 7 个生产文件导入，说明表被写完就搁置 → 类型校验没接上。属未完成（不是垃圾：它编码了键的类型契约）。 |
| `desktop/touch_helpers.py` | `TouchFriendlyScrollArea:220` `TouchFriendlyListWidget:242` | 模块被 6 个桌面文件导入（用 `enable_touch_events` / `apply_touch_target` / `PinchZoomMixin`），这两个控件类**零引用**。文件头把它们列为特性 → 触屏控件做了但没换上去，属未接线。 |
| `desktop/connect_request_store.py` | `unsubscribe:328` `recent_warnings:338` | 退订与「最近告警」读取零引用 → store 只进不出，告警无法回看。 |
| `desktop/panel_registry.py` | `AREA_LEFT:28` `AREA_FLOAT:31` | 区域常量；实际用字符串字面量 `"left"` / `"float"` → **常量与用法不一致**，属遗留。 |
| `desktop/config_schema.py` | `items_for_group:661` | 分组遍历零引用，设置面板自己遍历。 |
| `desktop/adsb_map_panel.py` | `_GRID:77` | 调色板常量零引用（同名 `_BG` 落第二档）。 |
| `desktop/adsb_panel.py` | `_GRID:62` | 同上，两个 ADS-B 面板各留一个未用颜色。 |
| `desktop/osm_map.py` | `ATTRIBUTION_CARTO:112` `ATTRIBUTION:139` | 两个署名常量零引用；**但署名本身没丢**——UI 画的是 `TileStyle.attribution`（`osm_map.py:1255` 绘制、`:959` 取值），所以只是常量重复的遗留，不是合规缺口。 |
| `desktop/rf_sky_view.py` | `_star_brush:382` | 私有画刷构造零引用。 |
| `desktop/main.py` | `_STARTUP_LOG_NAME:42` | 私有文件名常量零引用。 |

### 2.4 整模块没有任何生产导入的 58 个模块（本节最需要人工）

以 `import` 语句为准，`mbdsdr_ai/` + `desktop/` 里有 **58 个模块**没有任何生产代码导入。
其中 5 个是一次性构建脚本（`desktop/assets/generate_icon.py`、`desktop/i18n/build_qm.py`、
`mbdsdr_ai/astronomy/build_dso_catalog.py`、`mbdsdr_ai/skyengine/build_full_sky_catalog.py`、
`mbdsdr_ai/skyengine/build_xingguan_star_names.py`），`desktop/main.py` 是入口，
`mbdsdr_ai/aigui_demo.py` 是演示脚本——这 7 个不是死代码。**剩下 51 个是功能模块**：

| 主题 | 模块 |
| --- | --- |
| 设备 / 连接 / 守护（6） | `device_watchdog` `hal_enhanced` `reconnection` `remote_control_v2` `usb_watchdog` `position_service` |
| 录制 / 回放（5） | `background_decoder` `gated_recorder` `playback` `recorder_enhanced` `recording_library` |
| 解码 / 分析（9） | `channel_analyzer` `protocol_analyzer` `spectrum_accumulator` `sdrpp_decoders` `vor_decoder` `tc_cltu` `dvbs2_datafield` `ft8_modem` `ysf_demod` |
| 信号处理 / 流图（6） | `block_stream` `flowgraph_runtime` `polyphase_resampler` `marker_math` `mimo` `mimo_v2` |
| 卫星 / 测控（5） | `sat_calibration` `sat_pipeline_runner` `gnss_timestamps` `gps_tracking` `spacetime_predict` |
| 仪表 / 硬件协议（5） | `janvna2_proto` `nanovna_v2_proto` `vna_calio` `vna_avg` `s_meter` |
| 图像 / 渲染（2） | `image_enhance` `lrpt_render` |
| 服务 / 多用户（4） | `mobile_server` `web_bridge_host` `webspectra` `multi_user` |
| 框架 / 启动（5） | `module_system` `module_graph` `startup_sequence` `feature_manager` `optional_deps` |
| 频率 / 降噪 / 路由（4） | `frequency_manager_v2` `anr_iq` `gain_calibration` `vfo_enhanced` |

**这条数据不能当删除依据**，两个原因：

1. `mbdsdr_ai` 顶层模块会被 [cli.py:264](../../mbdsdr_ai/core/cli.py#L264) 的
   `pkgutil.iter_modules` + `importlib.import_module` **全量导入**（`--tool` 路径），
   所以「静态没有 import 语句」不等于「运行时没加载」；真正不会被加载的只有子包里的构建脚本。
2. 这些模块大多带 `register_*_tools` 之类注册函数，走工具面（MCP / Agent）而不是 UI，
   静态 import 图看不见这条路。

所以它们的正确标签是「**没有 UI 接线点**」，与 §6 同类：是未完成，不是垃圾。

其中在第一档逐条列出的代表条目：

| 模块 | 条目 | 判断 |
| --- | --- | --- |
| `mbdsdr_ai/ysf_demod.py` | 15 条（见 §2.1） | 只有 `tests/test_ysf_demod_roundtrip.py` 导入。 |
| `mbdsdr_ai/frequency_manager_v2.py` | `KNOWN_MODES:62` | 整模块只被 `tests/test_frequency_manager_v2.py` 导入；`KNOWN_MODES` 连测试都没用。**起点条目**。 |
| `mbdsdr_ai/ft8_modem.py` | `N_SYNC_SYMBOLS:69` `FRAME_LAYOUT:80` | 只有 FT8 相关测试导入；生产走的是 `ft8_encode` / `ft8_ldpc` 分步路径。 |
| `mbdsdr_ai/nanovna_v2_proto.py` | `CMD_V2_INDICATE:34` `CMD_V2_INDICATE_ACK:35` | 模块只被测试导入；协议实现完整但未接设备层。 |
| `mbdsdr_ai/module_graph.py` | `enumerate_sources:621` | 模块只被测试导入。 |
| `mbdsdr_ai/usb_watchdog.py` | `UDEV_SUPPORTED:205` | 模块只被测试导入。 |
| `desktop/main.py` | `_STARTUP_LOG_NAME:42` | 同名常量在 `startup_paths.py:22` 已有一份且被生产使用（3 处）→ main.py 这份是**重复定义**，属遗留。 |

---

## 3. 汇总

| 桶 | 定义数 | 说明 |
| --- | --- | --- |
| 全部模块级定义 | 6634 | `mbdsdr_ai/` + `desktop/`（不含两个 `tests/` 目录） |
| 生产零引用 | 381 | 第一档 365 + 第二档 16 |
| 仅测试/脚本引用 | 406 | 生产零引用，但测试当契约用 |
| 生产在用 | 5847 | 其余 |
| 整模块无生产导入 | 58 个模块 | 51 个功能模块 + 5 个构建脚本 + 入口 + demo（见 §2.4） |

「生产零引用」381 条只占 5.7%；其中真正「无反射、无测试、模块在用」的私有遗留
不到 20 条（见 §7 第 1 条）。剩余绝大多数是协议常量表与「编码侧/解码侧只接了一半」的实现。

---

## 4. 第二档：零引用但可能反射（16 条，逐条给证据）

这一档**一律不判死**。证据格式：`类型:位置`。

| 定义 | 证据 | 判断 |
| --- | --- | --- |
| `mbdsdr_ai/dsp.py:770` `write_cs16` | `字符串:mbdsdr_ai/__init__.py:104` | 在 `_EXPORTS` 字符串表里，`mbdsdr_ai.write_cs16` 可由 PEP 562 懒加载取出；仓内无调用。**公共 API 面**，不能判死。 |
| `mbdsdr_ai/dsp.py:817` `write_csv` | `字符串:mbdsdr_ai/__init__.py:106` | 同上。 |
| `mbdsdr_ai/dsp.py:833` `write_sidecar_json` | `字符串:mbdsdr_ai/__init__.py:107` | 同上。 |
| `mbdsdr_ai/hooks.py:410` `create_signal_alert_hook` | `字符串:mbdsdr_ai/__init__.py:124` | 同上；且是文档里的钩子工厂。 |
| `mbdsdr_ai/aigui_messages.py:139` `_node` | `动态:web/product/tests/verify_product.py:295` | 私有函数，动态命中来自前端校验脚本的字符串；**大概率遗留**，但属测试资产引用，需人工确认。 |
| `desktop/adsb_map_panel.py:74` `_BG` | `字符串:tests/test_ui_contrast.py:715,717` | 测试读的是 `location_panel.py` 的同名常量，**同名不同物**；本文件内零引用 → 遗留（低风险）。 |
| `mbdsdr_ai/vna_report.py:27` `_BG` | 同上 | 同上，另一处未用调色板常量。 |
| `mbdsdr_ai/fir_taps.py:51` `hann` | `字符串:audio_resampler.py:54` 等 3 处 | `scipy` 的 `window="hann"` 参数，**同名不同物**；函数零引用 → 大概率遗留。 |
| `mbdsdr_ai/fir_taps.py:56` `hamming` | `字符串:acquisition.py:808` 等 3 处 | 同上。 |
| `mbdsdr_ai/celestial_geometry.py:68` `AU` | `字符串:scripts/hw_acceptance/ring6_sky_mode.py:161` 等 | 验收脚本与测试文案里的「AU」字样；常量零引用 → 遗留。 |
| `mbdsdr_ai/orbit_dynamics.py:37` `C_LIGHT` | `字符串:vna_cal.py:37` | VNA 标定注释里的光速字样，非引用；常量零引用 → 与 `SPEED_OF_LIGHT` 重复。 |
| `mbdsdr_ai/ft8_encode.py:20` `NSPS` | `字符串:ft4_modem.py:49`、`ft8_modem.py:41` | 参数名 `NSPS` 出现在别处符号名里，非本模块常量引用。 |
| `mbdsdr_ai/ft8_modem.py:64` `SYMBOL_SEC` | `字符串:ft4_modem.py:50` | 同上。 |
| `mbdsdr_ai/meteor_sat.py:583` `qpsk_demodulate` | `字符串:satellite/meteor_lrpt.py:180` | 同名不同物（另一模块同名函数）；本函数零调用。 |
| `mbdsdr_ai/station.py:356` `pass_prediction_allowed` | `字符串+动态:scripts/hw_acceptance/ring6_sky_mode.py:214` | 验收脚本里有同名文本；若脚本真按名字调，则属**脚本专用 API**，不能判死。 |
| `mbdsdr_ai/rtl433_decoder.py:752` `decode_iq` | `动态:tests/rtl433_roundtrip.py:187` | 测试里按名字取；解码入口只在测试面存在。 |

补充说明：`register_tool_registry` 这一批（24 处）曾落此档，v3.2 修正后已确认为
生产引用（`tool_registry.py` 逐模块直接调用），不再是候选——列出来是为了避免下一轮
重复排查。`plugin_manager.PluginManager` 从未进过第二档（它本来就有测试引用）。

---

## 5. 第三档：仅测试/脚本引用（406 条 / 183 个模块）

含义：生产代码不引用，但测试把它当**契约**。删它等于删测试覆盖，
且多数是「回环测试的编码侧」——测试需要编码器来造激励，生产只需要解码器。
**这一档整体不建议删**，要删必须先改测试。

### 5.1 条目 ≥3 的模块（51 个）

读法：`定义:行` 均零生产引用；`+n` 表示该模块还有 n 条同类条目未展开。
「主要测试」是该模块被哪些测试文件引用（只列前两个）。

| 模块 | n | 定义 | 主要测试 |
| --- | --- | --- | --- |
| `mbdsdr_ai/fengyun_sat.py` | 15 | `FY4_LRIT_FREQ_HZ:87`, `FY4A_LRIT_SYMBOL_RATE:92`, `FY4B_LRIT_SYMBOL_RATE:94`, `FY3_HRPT_SYMBOL_RATE:101`, `build_fy4_image_information:179` +10 | test_dvbs2_datafield_vcdu.py, test_fengyun.py |
| `mbdsdr_ai/adsb.py` | 9 | `MODES_DEFAULT_FREQ_HZ:80`, `MODES_DEFAULT_SPS:82`, `MODES_DEFAULT_GAIN_DB:84`, `encode_cpr_airborne:330`, `build_identification_frame:363` +4 | adsb_real_roundtrip.py, test_benchmark_essence_defaults.py |
| `mbdsdr_ai/ais_decoder.py` | 8 | `build_type123:176`, `build_type5:206`, `build_type18:240`, `build_type24a:272`, `ais_frame_encode:411` +3 | test_ais_decoder.py, test_decode_router_honesty.py |
| `mbdsdr_ai/kismet_adapter.py` | 8 | `DOT11_TYPE_PHY:36`, `DOT11_TYPE_EXTENSION:38`, `DOT11_SUB_ACTION:52`, `DOT11_SUB_RTS:56`, `DOT11_SUB_CTS:57` +3 | kismet_test.py |
| `mbdsdr_ai/limesuite_params.py` | 7 | `SUPPORTED_OVERSAMPLE:81`, `LNA_GAIN_MAX_DB:117`, `PATH_NONE:154`, `PATH_AUTO:160`, `LMS_FMT_F32:179` +2 | limesuite_test.py |
| `mbdsdr_ai/rtl433_decoder.py` | 7 | `RTL433_DEFAULT_FREQUENCY_HZ:33`, `RTL433_DEFAULT_GAIN_DB:34`, `RTL433_MIN_SNR_DB:35`, `RTL433_MIN_LEVEL_DB:36`, `manchester_decode_bits:109` +2 | rtl433_roundtrip.py, test_benchmark_essence_defaults.py |
| `mbdsdr_ai/bladerf_params.py` | 6 | `BLADERF1_MIN_FREQ_HZ:81`, `BLADERF1_MAX_FREQ_HZ:82`, `BLADERF1_MIN_SAMPLERATE_HZ:83`, `BLADERF1_REC_MAX_SAMPLERATE_HZ:84`, `BLADERF1_MIN_BANDWIDTH_HZ:85` +1 | bladerf_test.py |
| `mbdsdr_ai/ft8_modem.py` | 6 | `WINDOW_SEC:70`, `gray_to_tone:89`, `tone_to_gray:96`, `iq_to_interleaved:103`, `Ft8Modulator:119` +1 | test_ft8_codec.py, test_ft8_modem.py |
| `mbdsdr_ai/gk2a_lrit.py` | 6 | `GK2A_LRIT_FREQ_HZ:54`, `GK2A_SYMBOL_RATE:60`, `build_gk2a_lrit_file:1108`, `file_to_vcdus:1166`, `vcdus_to_cadu:1230` +1 | test_gk2a_jp2.py, test_gk2a_lrit.py |
| `mbdsdr_ai/janvna2_proto.py` | 6 | `decode_packet:85`, `encode_datapoint:124`, `decode_datapoint:131`, `encode_sweep_settings:167`, `decode_sweep_settings:180` +1 | test_janvna2_proto.py |
| `mbdsdr_ai/nanovna_v2_proto.py` | 6 | `REG_V2:38`, `V2_USB_POINTS_MAX:53`, `build_read:61`, `build_write:68`, `encode_fifo_frame:108` +1 | test_nanovna_v2_proto.py |
| `mbdsdr_ai/acars_decoder.py` | 5 | `ACARS_CENTER_FREQ_HZ:45`, `ACARS_STANDARD_CHANNELS_MHZ:56`, `build_acars_frame:540`, `synthesize_msk_audio:572`, `frame_bits_from_bytes:592` | acars_test.py |
| `mbdsdr_ai/astronomy/moon_albedo.py` | 5 | `albedo_meta:70`, `selenographic_to_disc:125`, `screen_to_disc:181`, `albedo_at_disc:207`, `albedo_modulation:218` | test_moon_albedo.py |
| `mbdsdr_ai/crc.py` | 5 | `crc_update:127`, `crc16_m17:142`, `crc_residue_ok:174`, `_preset:197`, `check_crc14_ft8:253` | test_crc.py, test_crc_kernel.py |
| `mbdsdr_ai/dmr_demod.py` | 5 | `qr1676_encode:210`, `qr1676_decode:217`, `dmr_encode_voice_lc_header:601`, `dmr_encode_iq:678`, `dmr_decode_iq:991` | test_dmr_demod_roundtrip.py |
| `mbdsdr_ai/dsdcc_lite.py` | 5 | `SYMBOL_RATE_4800:26`, `DMR_VOCODER_FRAME_LEN:91`, `P25_IMBE_FRAME_BITS:113`, `DMR_RRC_COEFFS_ALPHA02:148`, `MBEParams:290` | dsdcc_test.py |
| `mbdsdr_ai/dvbs2_pl.py` | 5 | `fecframe_symbol_count:293`, `plframe_symbol_count:298`, `encode_qpsk_plframe:534`, `decode_qpsk_plframe:618`, `find_sof_candidates:656` | test_dvbs2_pl.py, test_dvbs2_sof_sync.py |
| `mbdsdr_ai/lrpt_render.py` | 5 | `APID_TO_CHANNEL:50`, `LrptVirtualChannel:76`, `generate_cloud_simulation:111`, `save_png:158`, `image_to_segments:185` | test_lrpt_render.py |
| `mbdsdr_ai/meteor_sat.py` | 5 | `dqpsk_bits_to_symbols:421`, `viterbi_encode:436`, `lrpc_build_frame:515`, `lrpt_find_frames:527`, `viterbi_decode_demo:602` | apt_meteor_roundtrip.py, test_compose_visible_image.py |
| `mbdsdr_ai/orbit_determination.py` | 5 | `ecef_to_eci:76`, `rangerate_to_fd:300`, `leosat_state_from_tle:534`, `position_error_curve:775`, `plot_residual_histogram:854` | test_orbit_determination.py, test_orbit_material_dataset.py |
| `mbdsdr_ai/rtlsdr_params.py` | 5 | `DIRECT_SAMPLING_I:169`, `DIRECT_SAMPLING_Q:170`, `ASYNC_DEFAULT_BUF_NUM:176`, `ASYNC_DEFAULT_BUF_LEN:177`, `get_gain_table_tenths:195` | rtlsdr_params_test.py |
| `mbdsdr_ai/wspr_codec.py` | 5 | `pack_message_bytes:180`, `deinterleave:240`, `sync_mismatch_count:267`, `decode_symbols:361`, `decode_message:367` | test_wspr_codec.py |
| `mbdsdr_ai/baseband_io.py` | 4 | `_write_rf64_wav:554`, `_read_rf64_wav:593`, `write_ziq:663`, `read_ziq:728` | test_baseband_rf64.py, test_baseband_ziq.py |
| `mbdsdr_ai/block_stream.py` | 4 | `VectorSource:297`, `VectorSink:324`, `MultiplyConst:337`, `FlowGraph:371` | test_block_stream.py |
| `mbdsdr_ai/decode_pipeline.py` | 4 | `decode_aprs_audio:837`, `benchmark_pipeline:901`, `behind_report:948`, `sweep_frequency_offset:1002` | test_decode_pipeline_e2e.py |
| `mbdsdr_ai/dvbs2_bch.py` | 4 | `bch_modes:272`, `bch_parameters:288`, `bch_syndrome:368`, `encode_dvbs2_fec:470` | test_dvbs2_bch.py, test_dvbs2_fec_chain.py |
| `mbdsdr_ai/flowgraph_runtime.py` | 4 | `VectorSource:192`, `VectorSink:219`, `MultiplyConst:231`, `Scheduler:374` | test_flowgraph_runtime.py |
| `mbdsdr_ai/lrpt_dct.py` | 4 | `dct_8x8:68`, `quantize:93`, `dequantize:103`, `zigzag_order:116` | test_lrpt_dct.py |
| `mbdsdr_ai/nrsc5_lite.py` | 4 | `PARTITION_DATA_CARRIERS:28`, `NRSC5_SAMPLE_RATE_NATIVE_FM:35`, `CONV_K7_GEN:40`, `HDCDecoder:408` | nrsc5_roundtrip.py |
| `mbdsdr_ai/vna_calio.py` | 4 | `save_error_box:48`, `load_error_box:70`, `save_cal_kit:90`, `load_cal_kit:107` | test_vna_calio.py |
| `desktop/command_palette.py` | 3 | `GROUPS:52`, `recent_error:81`, `reset_recent:199` | test_command_palette.py, test_command_palette_recent_corruption.py |
| `desktop/connect_request_store.py` | 3 | `session_values:208`, `is_occupied:213`, `reset:343` | test_connect_request_session.py |
| `desktop/error_remedy.py` | 3 | `no_entry_reason:102`, `mapping_gaps:123`, `mapping_overlaps:129` | test_error_remedy_links.py |
| `desktop/station_sync.py` | 3 | `last_error:50`, `read_gui_site:124`, `merge_sites:135` | test_station_sync.py |
| `mbdsdr_ai/atmosphere.py` | 3 | `atmospheric_refraction:134`, `airmass:170`, `is_night:234` | test_atmosphere.py |
| `mbdsdr_ai/digital_modes.py` | 3 | `WSJTInterface:93`, `get_qpsk_viterbi:224`, `decode_viterbi:236` | test_digital_modes_behavior.py |
| `mbdsdr_ai/dsp.py` | 3 | `write_cf32:760`, `write_wav:791`, `DSPChain:1325` | test_dsp_stream.py, test_full_integration.py |
| `mbdsdr_ai/dvbs2_ldpc.py` | 3 | `ldpc_modes:113`, `ldpc_parameters:128`, `ldpc_syndrome:178` | test_dvbs2_bch.py, test_dvbs2_ldpc.py |
| `mbdsdr_ai/i18n.py` | 3 | `set_language:43`, `tr:67`, `catalog_stats:85` | test_i18n.py |
| `mbdsdr_ai/image_enhance.py` | 3 | `stretch_black_white:104`, `enhance_apt:261`, `save_enhanced_png:307` | test_image_enhance.py |
| `mbdsdr_ai/lrpt_vcdu.py` | 3 | `reassemble_image_stream:95`, `fill_missing_lines:306`, `frames_to_meteor_segments:367` | test_fill_missing_lines.py, test_lrpt_huffman.py |
| `mbdsdr_ai/marker_math.py` | 3 | `snap_to_nearest:33`, `delta_reading:61`, `peak_search:73` | test_marker_math.py |
| `mbdsdr_ai/rds_lite.py` | 3 | `OFFSET_WORDS:43`, `build_4a_group:197`, `synthesize_rds_mpx:427` | rds_real_roundtrip.py |
| `mbdsdr_ai/sat_calibration.py` | 3 | `counts_to_reflectance:102`, `reflectance_to_brightness:112`, `CalibrationTable:124` | test_sat_calibration.py |
| `mbdsdr_ai/sat_links.py` | 3 | `refresh_machine_links:153`, `machine_status_text:194`, `machine_only_links:200` | test_sat_links_machine.py |
| `mbdsdr_ai/satdump_adapter.py` | 3 | `LRPT_RRC_ALPHA:52`, `LRPT_ENCODED_FRAME_SIZE:55`, `circular_orbit_ecef:544` | satdump_test.py |
| `mbdsdr_ai/sdrangel_adapter.py` | 3 | `MagAGC:276`, `NFMDemodSink:394`, `SSBDemodSink:434` | sdrangel_roundtrip.py |
| `mbdsdr_ai/skyengine/constellations.py` | 3 | `constellation_segments:240`, `constellation_label:294`, `constellation_anchor:305` | test_constellations_data.py, test_sky_constellations.py |
| `mbdsdr_ai/time_basis.py` | 3 | `utc_to_gps_seconds:93`, `gps_seconds_to_utc:100`, `local_to_utc:185` | test_time_basis.py |
| `mbdsdr_ai/vna_trace_math.py` | 3 | `group_delay:32`, `normalize_trace:46`, `gjb_conductance:61` | test_vna_trace_math.py |
| `mbdsdr_ai/ysf_demod.py` | 3 | `ysf_encode_data_frame:596`, `ysf_encode_iq:668`, `ysf_decode_iq:829` | test_ysf_demod_roundtrip.py |

### 5.2 条目 ≤2 的模块（132 个 / 172 条）

长尾，模式高度一致，不再逐条展开（完整逐条见生成物 `w92_T3grouped.txt`）：

- **同一功能的「另一半」**：`mbdsdr_ai/rds_lite.py` 的编码侧、`adsb.py` 的
  `encode_cpr_airborne`、`ais_decoder.py` 的 `build_typeNNN`、
  `dmr_demod.py` 的 `qr1676_encode`——生产只做解码，测试要编码器造激励。
- **硬件参数表**：`*_params.py` 的 `DEFAULT_PARAMS`、`SUPPORTED_*`、`BLADERF*`、
  `LIME*`——UI 只显示其中几项，测试校验整张表。
- **私有助手**：`_preset`、`_write_rf64_wav`、`_read_rf64_wav`、`zigzag_order`。
- **desktop 侧**：`command_palette`、`error_remedy`、`station_sync` 的纯函数被测试
  直接调，UI 走的是其他路径。

---

## 6. 特别标注：定义了但从来没有被任何 UI 触达

这一类**不是垃圾**：是需求没做完，删了等于删需求。判定只用可直接核验的证据
（类/函数零引用 + 模块被谁导入），不用不可靠的全局可达性推断。

### 6.1 整模块零生产导入、对外只有一个功能类（6 个代表）

| 模块 | 对外符号 | 实际含义 | 为什么不能删 |
| --- | --- | --- | --- |
| `mbdsdr_ai/anr_iq.py:37` | `ANR` | IQ 域谱减降噪；模块 docstring 写明「对应任务要求的 ANR 类」 | 需求原文在，实现有测试（6 处），缺的只是调用点 |
| `mbdsdr_ai/gain_calibration.py:32` | `GainCalibrationTable` | 增益标定表 + JSON 持久化 + `auto_calibrate` | 数据层完整（8 处测试），等设备标定流程接上 |
| `mbdsdr_ai/vfo_enhanced.py:27` | `VfoAudioRouter` | 主听/副听互斥路由 | 6 处测试覆盖互斥语义，UI 还没有第二听 |
| `mbdsdr_ai/vna_avg.py:32` | `SweepAverager` | 扫间平均 + 历史回溯 | 14 处测试，VNA 面板未接平均开关 |
| `mbdsdr_ai/frequency_manager_v2.py:91` | `FrequencyManagerV2` | 频率管理 v2（CRUD/最近/CSV/自动分类） | 设置线报告的起点；6 处测试，生产零导入 |
| `mbdsdr_ai/s_meter.py:97` | `SMeter` | S 表（信号强度显示） | 只有 `tests/test_s_meter.py`；**这是最典型的「UI 该有但没有」** |

### 6.2 活跃模块里的未接线类

| 位置 | 符号 | 证据 | 含义 |
| --- | --- | --- | --- |
| `desktop/touch_helpers.py:220` | `TouchFriendlyScrollArea` | 零引用；同模块 `enable_touch_events` 等被 6 个桌面文件用 | 触屏滚动容器没换上去 |
| `desktop/touch_helpers.py:242` | `TouchFriendlyListWidget` | 零引用 | 触屏列表没换上去 |
| `desktop/rf_sky_view.py:8399` | `RFSkyViewPanel` | 生产零引用、13 处测试引用 | 星图面板类被测试驱动，但主窗口没有用它当面板 |
| `mbdsdr_ai/code_editor.py:79` | `CodeEditor` | 生产只被 `agent.py` 用（生成代码后编辑），无 UI 入口 | 能力在，用户点不到 |
| `desktop/settings.py:58/60/62` | `_FLOAT_KEYS` `_INT_KEYS` `_BOOL_KEYS` | 只定义、无读取 | 设置项类型契约没接校验 |
| `desktop/osm_map.py:112/139` | `ATTRIBUTION*` | 两个常量零引用（署名实际走 `TileStyle.attribution`） | 常量重复的遗留，不是合规缺口 |

### 6.3 判定边界（写清楚，免得被当成「可删清单」）

- 「零引用」只说明**仓内静态找不到读取点**。外部脚本、用户配置、MCP 客户端、
  第三方插件都可能按名字用；第二档就是为此设的。
- 「UI 未触达」不等于「用户不需要」。本仓多份模块 docstring 直接写着
  「对应任务要求」，这类一律按未完成需求处理。

---

## 7. 结论与建议（只报不改）

1. **可直接删的候选只有一类**：私有符号 + 零引用 + 模块在用，且实现已被别处取代。
   共约 15 个，例如 `dsp.py` 的 `_HangAGC`、`rtklib_adapter.py` 的 `_norm`/`_dot`、
   `fec.py` 的 `_poly_add`/`_poly_mul`、`crc.py` 的 `_to_bits`、`core/mcp_server.py`
   的 `_noop`、`sdr_tools.py` 的 `_decode_noaa_apt`、`ssdv_decoder.py` 的 `_idct_8x8`。
   即便这一批，**也要先跑一遍全量测试**，因为 `_` 前缀不阻断外部脚本引用。
2. **协议常量表不建议删**：它们是魔法数字的注释，删掉提高的是误读概率，不是可维护性。
3. **整模块零生产引用的 6 个不是死代码，是未接线需求**。正确的动作是「接上」或
   「写一条显式的未接线说明」，不是删除。`decoder_registry` 已经示范了后者
   （`desktop/capability_catalog.py` 里标注「含未接主调度的诚实标注」）。
4. **真正需要人判断的只有 16 条第二档**；其中 4 条（`dsp.write_*`、`hooks.create_signal_alert_hook`）
   是 `_EXPORTS` 公共 API，实际不是候选。
5. 本次不删任何代码、不改任何产品文件；`themes.py` / `tokens.py` 与视觉相关入口
   一行未动。
