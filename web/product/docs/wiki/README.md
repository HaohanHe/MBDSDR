# 能力百科 · 索引

> **能力而非流程**。本页的条目不是向导步骤，是模型可调用的能力集合。
> 工具域来自原仓库 `mcp_server.py` 的代码事实（由 `build/sync_from_repo.py` 抽取），
> 规划能力来自设计稿与审计结论并明确标注未验证。

## 一、已落地的工具域

| id | 域名（三语） | 组 | 代表工具 | 词条 |
|---|---|---|---|---|
| `sdr-vfo` | SDR 调谐/VFO / SDR 同調 / VFO / SDR Tuning / VFO | 冻结 | `add_vfo`、`get_demod`、`get_frequency`、`learn_noise_floor`、`list_vfos`、`read_spectrum` | [SDR 调谐/VFO](capabilities/sdr-vfo.md) |
| `subaudio` | 亚音数字解码 / サブトーン・デジタル復号 / Subtone & Digital Decode | 冻结 | `get_decoder_messages`、`start_decoder`、`stop_decoder` | [亚音数字解码](capabilities/subaudio.md) |
| `vna-measure` | VNA 测量 / VNA 測定 / VNA Measurement | 冻结 | 诚实空态 · 该面无工具 | [VNA 测量](capabilities/vna-measure.md) |
| `vna-analyze` | VNA 分析 / VNA 解析 / VNA Analysis | 冻结 | `vna_analyze_sweep` | [VNA 分析](capabilities/vna-analyze.md) |
| `vna-calib` | VNA 校准 / VNA 較正 / VNA Calibration | 冻结 | `vna_apply_calibration`、`vna_solve_error_box` | [VNA 校准](capabilities/vna-calib.md) |
| `export` | 导出演制 / 録音・エクスポート / Recording & Export | 冻结 | `export_peaks_csv`、`export_spectrum_snapshot`、`export_vna_report`、`get_recording_status`、`read_audio`、`read_iq` | [导出演制](capabilities/export.md) |
| `sat-scan` | 书签扫描卫星 / ブックマーク / 走査 / 衛星 / Bookmarks, Scan & Satellites | 冻结 | `add_bookmark`、`delete_bookmark`、`find_nearest_bookmark`、`get_satellite_pass`、`get_scan_results`、`list_bookmarks` | [书签扫描卫星](capabilities/sat-scan.md) |
| `system` | 系统设备 / システム / デバイス / System & Devices | 冻结 | `connect`、`disconnect`、`get_service_status`、`get_status`、`is_connected`、`list_audio_outputs` | [系统设备](capabilities/system.md) |
| `memory` | 记忆编排 / メモリ編成 / Memory Orchestration | 冻结 | 诚实空态 · 该面无工具 | [记忆编排](capabilities/memory.md) |
| `workbench` | 工作台编排 / ワークベンチ編成 / Workbench Orchestration | 冻结 | `aigui_component_catalog`、`aigui_render_preview`、`aigui_validate_message` | [工作台编排](capabilities/workbench.md) |
| `decoder-tail` | 解码器长尾 / デコーダ長尾 / Decoder Long Tail | Python 扩展 | `decoder_dab_fic_decode`、`decoder_fst4_encode_text`、`decoder_gk2a_parse_headers`、`decoder_nrsc5_frame_parse`、`decoder_rtl433_decode`、`decoder_rtl433_list` | [解码器长尾](capabilities/decoder-tail.md) |
| `qa` | 人机问答 / 対話質問 / Human Q&A | Python 扩展 | `answer_question`、`ask_question`、`question_status` | [人机问答](capabilities/qa.md) |
| `external-tools` | 外部工具 / 外部ツール / External Tools | Python 扩展 | `mcp_client_call`、`mcp_client_connect`、`mcp_client_disconnect`、`mcp_client_list` | [外部工具](capabilities/external-tools.md) |
| `sat-link` | 卫星链路 / 衛星リンク / Satellite Links | Python 扩展 | `sat_link_get`、`sat_link_identify`、`sat_link_list`、`sat_link_tuning` | [卫星链路](capabilities/sat-link.md) |

## 二、规划 / 未验证（not_verified）

| id | 名称（三语） | 状态 | 词条 |
|---|---|---|---|
| `vis6dof` | 6DOF 视觉引导 / 6DOF 視覚誘導 / 6DOF Visual Guidance | 规划 · 未验证 | [6DOF 视觉引导](capabilities/vis6dof.md) |
| `calib` | 视觉校准 / 視覚キャリブレーション / Visual Calibration | 规划 · 未验证 | [视觉校准](capabilities/calib.md) |
| `vrxr` | VR / XR / VR / XR / VR / XR | 规划 · 未验证 | [VR / XR](capabilities/vrxr.md) |

## 一致性保证

- 本页 id 集合与 `build/repo-facts.json` 的 `domains + planned` 一一对应；
  `tests/verify_product.py` 会核对 UI、词典、词条与构建产物，漂移即失败。
- 域名的中文是仓库事实，日/英译名与图标是产品侧本地化层
  （`build/sync_from_repo.py` 的 `DOMAIN_I18N` / `PLANNED`）。
- 工具名来自 `_TOOL_DOMAIN`，**不手抄** `docs/mcp_tools.md` 的自动生成标记区（已滞后）。

## 其它

- [术语表（三语）](glossary.md) · [常见问题](faq.md)
- [原仓库对齐说明](../09-repo-alignment.md)
- [产品规格铁律](../01-product-spec.md) · [场景页](../scenarios/)
