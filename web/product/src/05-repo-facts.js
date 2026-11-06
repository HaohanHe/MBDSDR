/* 由 build/sync_from_repo.py 生成，勿手改。事实源：原仓库代码。 */
(function (global) {
  "use strict";
  const MB = global.MBDSDR = global.MBDSDR || {};
  MB.REPO = {
  "generatedAt": "2026-11-06T03:03:54+08:00",
  "repo": {
    "root": "D:\\mbdsdr\\MBDSDR",
    "head": "c438904c",
    "note": "只读抽取；工作树可能含其它会话在途改动，HEAD 仅标识基线"
  },
  "domains": [
    {
      "id": "sdr-vfo",
      "name": "SDR 调谐/VFO",
      "ja": "SDR 同調 / VFO",
      "en": "SDR Tuning / VFO",
      "icon": "≋",
      "group": "frozen",
      "summary": "接收机调谐、解调模式、带宽/静噪/降噪与多 VFO 及频谱读取参数控制。",
      "tools": [
        "add_vfo",
        "get_demod",
        "get_frequency",
        "learn_noise_floor",
        "list_vfos",
        "read_spectrum",
        "remove_vfo",
        "set_anr_enabled",
        "set_anr_strength",
        "set_bandwidth",
        "set_deemphasis",
        "set_demod",
        "set_frequency",
        "set_gain",
        "set_iq_invert",
        "set_primary_vfo",
        "set_sample_rate",
        "set_squelch",
        "start_analyze"
      ],
      "sample": [
        "add_vfo",
        "get_demod",
        "get_frequency",
        "learn_noise_floor",
        "list_vfos",
        "read_spectrum"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "subaudio",
      "name": "亚音数字解码",
      "ja": "サブトーン・デジタル復号",
      "en": "Subtone & Digital Decode",
      "icon": "⌁",
      "group": "frozen",
      "summary": "CTCSS/DCS 亚音与各类数字报文解码器的启停与报文读取。",
      "tools": [
        "get_decoder_messages",
        "start_decoder",
        "stop_decoder"
      ],
      "sample": [
        "get_decoder_messages",
        "start_decoder",
        "stop_decoder"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "vna-measure",
      "name": "VNA 测量",
      "ja": "VNA 測定",
      "en": "VNA Measurement",
      "icon": "∿",
      "group": "frozen",
      "summary": "VNA 扫频设置与 S 参数原始测量读取。",
      "tools": [],
      "sample": [],
      "empty": true,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "vna-analyze",
      "name": "VNA 分析",
      "ja": "VNA 解析",
      "en": "VNA Analysis",
      "icon": "◭",
      "group": "frozen",
      "summary": "对 VNA 测量数据做谐振/阻抗匹配等分析。",
      "tools": [
        "vna_analyze_sweep"
      ],
      "sample": [
        "vna_analyze_sweep"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "vna-calib",
      "name": "VNA 校准",
      "ja": "VNA 較正",
      "en": "VNA Calibration",
      "icon": "✛",
      "group": "frozen",
      "summary": "VNA 校准状态与校准流程控制。",
      "tools": [
        "vna_apply_calibration",
        "vna_solve_error_box"
      ],
      "sample": [
        "vna_apply_calibration",
        "vna_solve_error_box"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "export",
      "name": "导出演制",
      "ja": "録音・エクスポート",
      "en": "Recording & Export",
      "icon": "⇩",
      "group": "frozen",
      "summary": "IQ 录制、录制状态与音频/频谱数据导出。",
      "tools": [
        "export_peaks_csv",
        "export_spectrum_snapshot",
        "export_vna_report",
        "get_recording_status",
        "read_audio",
        "read_iq",
        "start_recording",
        "stop_recording"
      ],
      "sample": [
        "export_peaks_csv",
        "export_spectrum_snapshot",
        "export_vna_report",
        "get_recording_status",
        "read_audio",
        "read_iq"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "sat-scan",
      "name": "书签扫描卫星",
      "ja": "ブックマーク / 走査 / 衛星",
      "en": "Bookmarks, Scan & Satellites",
      "icon": "◉",
      "group": "frozen",
      "summary": "书签管理、步进扫描与卫星过境预报/调谐。",
      "tools": [
        "add_bookmark",
        "delete_bookmark",
        "find_nearest_bookmark",
        "get_satellite_pass",
        "get_scan_results",
        "list_bookmarks",
        "list_satellites",
        "set_ground_station",
        "start_scan",
        "stop_scan",
        "tune_satellite"
      ],
      "sample": [
        "add_bookmark",
        "delete_bookmark",
        "find_nearest_bookmark",
        "get_satellite_pass",
        "get_scan_results",
        "list_bookmarks"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "system",
      "name": "系统设备",
      "ja": "システム / デバイス",
      "en": "System & Devices",
      "icon": "⚙",
      "group": "frozen",
      "summary": "设备连接、音频/服务开关与整体状态。",
      "tools": [
        "connect",
        "disconnect",
        "get_service_status",
        "get_status",
        "is_connected",
        "list_audio_outputs",
        "list_devices",
        "set_audio_output",
        "set_mute",
        "set_volume",
        "shutdown",
        "start_audio",
        "start_remote_control",
        "start_web_server",
        "stop_audio",
        "stop_remote_control",
        "stop_web_server"
      ],
      "sample": [
        "connect",
        "disconnect",
        "get_service_status",
        "get_status",
        "is_connected",
        "list_audio_outputs"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "memory",
      "name": "记忆编排",
      "ja": "メモリ編成",
      "en": "Memory Orchestration",
      "icon": "▤",
      "group": "frozen",
      "summary": "本地持久记忆（SQLite FTS5）的写入、覆盖与全文检索，按 user/agent/session 作用域分层。",
      "tools": [],
      "sample": [],
      "empty": true,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "workbench",
      "name": "工作台编排",
      "ja": "ワークベンチ編成",
      "en": "Workbench Orchestration",
      "icon": "▦",
      "group": "frozen",
      "summary": "AIGUI 三栏工作台布局编排：按编译期白名单调整 splitter 尺寸/右侧 tab 可见性，未知 id 诚实占位，不碰 DSP/音频/硬件。",
      "tools": [
        "aigui_component_catalog",
        "aigui_render_preview",
        "aigui_validate_message"
      ],
      "sample": [
        "aigui_component_catalog",
        "aigui_render_preview",
        "aigui_validate_message"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "decoder-tail",
      "name": "解码器长尾",
      "ja": "デコーダ長尾",
      "en": "Decoder Long Tail",
      "icon": "⇶",
      "group": "python",
      "summary": "Python 侧独立实现、C++ 10 域尚未覆盖的长尾协议解码器与硬件参数查表（DAB+/NRSC-5/433.92MHz ISM/GK-2A LRIT/FST4/bladeRF 参数）。离线纯函数，真机验证多为 PENDING。",
      "tools": [
        "decoder_dab_fic_decode",
        "decoder_fst4_encode_text",
        "decoder_gk2a_parse_headers",
        "decoder_nrsc5_frame_parse",
        "decoder_rtl433_decode",
        "decoder_rtl433_list",
        "device_bladerf_params_query"
      ],
      "sample": [
        "decoder_dab_fic_decode",
        "decoder_fst4_encode_text",
        "decoder_gk2a_parse_headers",
        "decoder_nrsc5_frame_parse",
        "decoder_rtl433_decode",
        "decoder_rtl433_list"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "qa",
      "name": "人机问答",
      "ja": "対話質問",
      "en": "Human Q&A",
      "icon": "?",
      "group": "python",
      "summary": "结构化提问原语（ask_question/answer_question/question_status）：agent 在选项间请求人类决策，stdout 请求-响应式传输不阻塞——会话内存态，重启即丢 pending（如实文档化）。不走外部确认 token 门：问答是对话层动作，不碰 DSP/音频/硬件。",
      "tools": [
        "answer_question",
        "ask_question",
        "question_status"
      ],
      "sample": [
        "answer_question",
        "ask_question",
        "question_status"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "external-tools",
      "name": "外部工具",
      "ja": "外部ツール",
      "en": "External Tools",
      "icon": "⇄",
      "group": "python",
      "summary": "通用 MCP 客户端（mbdsdr_ai/mcp_client.py）：按 ~/.mbdsdr/mcp_servers.json注册并连接外部 MCP server（stdio / streamable_http），tools/list 与tools/call 代理，供 MBDSDR agent 侧消费第三方工具面。连接动作起子进程/发网络请求——connect/disconnect/call 为写工具走外部确认 token 门；外部 server的工具结果一律按不可信数据处理；needs_auth 为文档化 TODO（401 -> FAILED +错误信封，不伪造第五状态）。",
      "tools": [
        "mcp_client_call",
        "mcp_client_connect",
        "mcp_client_disconnect",
        "mcp_client_list"
      ],
      "sample": [
        "mcp_client_call",
        "mcp_client_connect",
        "mcp_client_disconnect",
        "mcp_client_list"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    },
    {
      "id": "sat-link",
      "name": "卫星链路",
      "ja": "衛星リンク",
      "en": "Satellite Links",
      "icon": "↗",
      "group": "python",
      "summary": "知名卫星下行链路目录 sat_links（data/sat_links.json，皮进配置）：星名枚举/条目查取/频率反查（频谱竖线是谁）/TLE 名收听建议。点星即听的数据底座；只读纯数据，无命中如实空态。",
      "tools": [
        "sat_link_get",
        "sat_link_identify",
        "sat_link_list",
        "sat_link_tuning"
      ],
      "sample": [
        "sat_link_get",
        "sat_link_identify",
        "sat_link_list",
        "sat_link_tuning"
      ],
      "empty": false,
      "source": "mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN"
    }
  ],
  "frozenCount": 10,
  "pythonExtensionCount": 4,
  "toolDomainCount": 82,
  "writePatterns": [
    "sdr_set_*",
    "sdr_connect*",
    "sdr_disconnect",
    "sdr_switch_*",
    "sdr_open_*",
    "sdr_record_*",
    "sdr_bookmark_add",
    "sdr_bookmark_goto",
    "sdr_play_*",
    "sdr_spill_save",
    "sdr_transmit_*",
    "sdr_aprs_send_*",
    "sdr_ai_sweep",
    "sdr_sweep_scan",
    "sdr_fm_scan",
    "sdr_fm_preset",
    "sdr_listen_fm",
    "sdr_watch_capture",
    "sdr_spectrum_screenshot",
    "sdr_wfm_stereo",
    "tc_uplink_transmit",
    "doppler_orbit_determine",
    "lro_track",
    "radio_connect",
    "radio_ptt",
    "radio_send_*",
    "radio_set_*",
    "gimbal_connect",
    "gimbal_move",
    "gimbal_point",
    "gimbal_stop",
    "gimbal_track_*",
    "gimbal_rssi_sweep",
    "vfo_add",
    "vfo_remove",
    "vfo_select",
    "astro_set_observer",
    "pose_update_*",
    "pnt_update_source",
    "amr_add_sample",
    "memory_write",
    "switch_model",
    "learning_record",
    "judge_evaluate",
    "subagent_create",
    "subagent_execute",
    "workflow_execute",
    "workflow_record_*",
    "workflow_template_create",
    "scheduler_add",
    "scheduler_enable",
    "scheduler_disable",
    "scheduler_tick",
    "baseband_save",
    "code_modify_*",
    "code_git_commit",
    "code_rollback",
    "code_hot_reload",
    "file_change_track",
    "file_change_revert",
    "guardian_rollback",
    "evolution_commit",
    "evolution_propose",
    "evolution_rollback",
    "git_clone_repo",
    "orbit_error_plot",
    "orbit_sky_plot",
    "sat_image_enhance",
    "satdump_compose_image",
    "satdump_process",
    "satdump_live",
    "bladerf_set_params",
    "hackrf_set_params",
    "limesdr_set_params",
    "rtlsdr_set_params",
    "set_ppm_correction",
    "doppler_tune_frequency",
    "ntrip_connect",
    "r2cloud_schedule",
    "sdrsharp_make_window",
    "sdrangel_remotecontrol",
    "cubicsdr_drag_tune"
  ],
  "gates": {
    "controlHub": {
      "owner": "C++ ControlHub 宿主写开关（不由 Web 前端签发）",
      "stateVar": "writeEnabled_",
      "guard": "row->write && !writeEnabled_.load()",
      "resultField": "gated",
      "errorContains": "write gate 关闭",
      "frontEndAction": "只透传 args；gated:true 如实回显，不自行放行",
      "source": "cpp/src/control/control_hub.cpp: gatedResult / execute"
    },
    "mcp": {
      "owner": "外部确认通道签发的一次性 token（Python 内核消费）",
      "tokenFields": [
        "confirm_token",
        "tx_token"
      ],
      "consumedGateFlags": [
        "arm_manual",
        "approve_session",
        "confirm_token",
        "tx_token"
      ],
      "selfReportFields": [
        "arm_manual"
      ],
      "errorCode": "manual_gate_required",
      "selfReportRejected": true,
      "hint": "由真人经外部确认通道签发一次性 confirm_token 后重试；token 有有效期且只能消费一次。",
      "frontEndAction": "只透传已签发的 token；不生成、不把 arm_manual 当放行",
      "source": "mbdsdr_ai/core/mcp_server.py · mbdsdr_ai/error_catalog.py"
    },
    "frontEndPolicy": {
      "mayNotIssueToken": true,
      "mustStripSelfReport": true,
      "gatedIsNotFailure": true,
      "note": "前端不得签发 token、不得因自报 arm_manual 放行；gated 与执行失败严格区分"
    }
  },
  "planned": [
    {
      "id": "vis6dof",
      "icon": "✦",
      "zh": "6DOF 视觉引导",
      "ja": "6DOF 視覚誘導",
      "en": "6DOF Visual Guidance",
      "why": "位姿融合 L0–L3 为规划；pose.py 的 IMU 修正算出来即丢弃，无串口/IMU 驱动",
      "source": "07-卫星指向与AR系统.md §9 · docs/audit_r2/27_gimbal_pose.md"
    },
    {
      "id": "calib",
      "icon": "✛",
      "zh": "视觉校准",
      "ja": "視覚キャリブレーション",
      "en": "Visual Calibration",
      "why": "相机-天线轴系标定为规划；gimbal.py 的 read_pose() 在 manual/board 模式恒返回 0°",
      "source": "docs/audit_r2/27_gimbal_pose.md"
    },
    {
      "id": "vrxr",
      "icon": "◌",
      "zh": "VR / XR",
      "ja": "VR / XR",
      "en": "VR / XR",
      "why": "OpenXR / 头显 6DOF 为 feature:xr-sky 规划，代码里不存在",
      "source": "07-卫星指向与AR系统.md §9"
    }
  ],
  "tokens": {
    "figma": {
      "bg": "#000000",
      "bg_alt": "#1f1f1f",
      "card": "#1f1f1f",
      "text": "#FFFFFF",
      "text_secondary": "#CFCFCF",
      "text_disabled": "#707070",
      "border": "#404040",
      "grid": "#2C2C2C",
      "primary": "#D9D9D9",
      "accent": "#D9D9D9",
      "accent_hover": "#E6E6E6",
      "success": "#6BA89A",
      "danger": "#B85C5C",
      "button": "#2C2C2C",
      "button_hover": "#404040",
      "button_pressed": "#4F4F4F",
      "button_disabled": "#1F1F1F",
      "primary_text": "#D9D9D9"
    },
    "radius": {
      "card_lg": 24,
      "card_md": 12,
      "card_sm": 8,
      "button": 18,
      "dock_icon": 13,
      "circle_sm": 42,
      "circle_lg": 84
    },
    "font": {
      "title_lg": "39px",
      "title": "24px",
      "body": "20px",
      "dock": "42px",
      "small": "14px"
    },
    "textAlpha": {
      "primary": 1.0,
      "secondary": 0.78,
      "tertiary2": 0.67,
      "tertiary": 0.5,
      "quaternary": 0.3,
      "faint": 0.23,
      "disabled": 0.2
    },
    "size": {
      "topbar_h": 56,
      "dock_h": 64,
      "splitter_handle": 4,
      "collapse_strip": 36,
      "min_dock_w": 300,
      "planetarium_strip_w": 152
    },
    "source": "desktop/tokens.py · THEME_COLORS['figma'] / RADIUS / FONT / TEXT_ALPHA / SIZE"
  },
  "timeRates": {
    "values": [
      {
        "label": "⏸",
        "rate": 0.0
      },
      {
        "label": "▶",
        "rate": 1.0
      },
      {
        "label": "×60",
        "rate": 60.0
      },
      {
        "label": "×300",
        "rate": 300.0
      },
      {
        "label": "×1800",
        "rate": 1800.0
      }
    ],
    "source": "desktop/rf_sky_view.py 底部倍率按钮"
  },
  "channels": {
    "controlHub": {
      "host": "127.0.0.1",
      "port": 50732,
      "envVar": "MBDSDR_CONTROL_HTTP_PORT",
      "settingsKey": "control/httpPort",
      "endpoints": [
        "/",
        "/status",
        "/command",
        "/pocsag_messages",
        "/m17_calls",
        "/vor_radial",
        "/acars_packets",
        "/navtex_messages"
      ],
      "postCommand": {
        "path": "/command",
        "body": {
          "tool": "<name>",
          "args": {}
        }
      },
      "writeGate": "writeEnabled_ (C++ host switch, not arm_manual)",
      "source": "cpp/src/core/tokens.h · cpp/src/control/control_http_server.cpp · docs/CHROMEOS_CROSTINI_SDR.md"
    },
    "mcp": {
      "serverName": "mbdsdr-core",
      "protocolVersion": "2024-11-05",
      "methods": [
        "initialize",
        "tools/list",
        "tools/call"
      ],
      "source": "mbdsdr_ai/core/mcp_server.py · docs/mcp_tools.md"
    },
    "headless": {
      "controller": "SDRController",
      "source": "docs/headless_api.md · mbdsdr_ai/core/"
    }
  },
  "site": {
    "default": {
      "lat": 0.0,
      "lon": 0.0,
      "alt": 0.0,
      "trusted": false
    },
    "guard": "mbdsdr_ai/station.py",
    "source": "AGENTS.md §1 · mbdsdr_ai/station.py",
    "note": "中性示例站点；不可信即拒绝锁定与过境预报"
  },
  "satellites": {
    "items": [
      {
        "name": "NOAA 15",
        "norad": "25338",
        "downlinkHz": 137620000.0,
        "tleEpoch": "2026-09-25",
        "tleLines": 2
      },
      {
        "name": "NOAA 18",
        "norad": "28654",
        "downlinkHz": 137912500.0,
        "tleEpoch": "2026-09-25",
        "tleLines": 2
      },
      {
        "name": "NOAA 19",
        "norad": "33591",
        "downlinkHz": 137100000.0,
        "tleEpoch": "2026-09-25",
        "tleLines": 2
      },
      {
        "name": "ISS (ZARYA)",
        "norad": "25544",
        "downlinkHz": 145800000.0,
        "tleEpoch": "2026-09-25",
        "tleLines": 2
      },
      {
        "name": "FUNBCUBE-1 (AO-73)",
        "norad": "39444",
        "downlinkHz": 145935000.0,
        "tleEpoch": "2026-09-25",
        "tleLines": 2
      },
      {
        "name": "RADFXSAT (FOX-1B)",
        "norad": "43017",
        "downlinkHz": 145960000.0,
        "tleEpoch": "2026-09-25",
        "tleLines": 2
      }
    ],
    "tleDate": "2026-09-25",
    "downlink": {
      "NOAA 15": 137620000.0,
      "NOAA 18": 137912500.0,
      "NOAA 19": 137100000.0,
      "ISS (ZARYA)": 145800000.0,
      "FUNBCUBE-1 (AO-73)": 145935000.0,
      "RADFXSAT (FOX-1B)": 145960000.0
    },
    "policy": {
      "decision": "keep_but_not_for_precise_passes",
      "summary": "内置合成/凑整 TLE 保留用于离线演示，但禁止作为精确过境/跟踪数据",
      "enforced_by": [
        "trust=synthetic_rounded → usable_for_tracking=False",
        "sdr_tools 输出附带「不可当精确过境时刻」告警",
        "orbit/decoders 取数不以内置合成值冒充实时（无缓存即诚实空态）"
      ],
      "precise_source": "Celestrak 实时 TLE（缓存 24h 门槛）或用户自备 .tle"
    },
    "source": "mbdsdr_ai/sat_tracker.py · mbdsdr_ai/builtin_tle_meta.py",
    "note": "产品 SGP4 轨迹用 sat_tracker.py::_BUILTIN_TLES（Celestrak 真实抓取，BUILTIN_TLE_DATE）；builtin_tle_meta.py 的合成/凑整策略针对 decoders.BUILTIN_TLE 历史占位值，不是同一份数据；静态快照不用于精确过境"
  },
  "redlines": {
    "noRealLocationDefault": true,
    "hardcodedCapabilityNumbers": false,
    "noMockFakeData": true,
    "writeToolsNeedExternalConfirmation": true,
    "frontEndMustNotSelfArm": true,
    "orbitDeterminationNotAFeature": true,
    "license": "MIT · AGPL 引擎只学机制不引代码",
    "source": "AGENTS.md §1–§3 · README.md · LICENSING.md · NOTICE.md"
  },
  "driftNotes": [
    "docs/mcp_tools.md 末尾标记区为自动生成且已滞后（无 sat_link_* 工具），不可手抄",
    "README.md 的域数描述与代码 ALL_DOMAIN_NAMES 不一致，以 mcp_server.py 为准",
    "07-卫星指向与AR系统.md 是历史设计稿：北京默认坐标、GPL-3.0 声明、/api/pointing/* 均未落地"
  ]
};
})(typeof window !== "undefined" ? window : globalThis);
