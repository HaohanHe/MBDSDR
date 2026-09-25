"""统一气象卫星 pipeline 参数表。

本模块把 SatDump 各卫星 pipeline JSON（resources/pipelines/*.json）里经过实测的
解调/解码参数，收敛为一份与算法解耦的静态参数表。运行时不再散落硬编码，调度器
（sat_pipeline_runner）只按本表描述的 stage 顺序串联，不重新实现任何解调/解码算法。

参数值来源（as of SatDump 仓库当前版本）：
  - NOAA APT            : resources/pipelines/NOAA.json        -> noaa_apt
  - Meteor-M LRPT 72k   : resources/pipelines/Meteor-M.json    -> meteor_m2_lrpt
  - Meteor-M2-x LRPT 80k: resources/pipelines/Meteor-M.json    -> meteor_m2-x_lrpt_80k
  - GK-2A LRIT          : resources/pipelines/GK2A.json        -> gk2a_lrit
  - GOES-R HRIT         : resources/pipelines/GOES.json         -> goes_hrit
  - FY-3 A/B AHRPT      : resources/pipelines/FengYun-3.json   -> fengyun3_ab_ahrpt

stage.level 与 SatDump pipeline 的 level 命名一致：
    baseband -> soft -> cadu -> frames -> products
其中 baseband 永远是输入层（空 module，不做处理）。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


# pipeline stage 处理层级（顺序即处理链顺序）。
# 与 SatDump src-core/pipeline/pipeline_run.cpp 的 level 串联一致。
STAGE_LEVELS: Tuple[str, ...] = ("baseband", "soft", "cadu", "frames", "products")

_LEVEL_ORDER_MAP: Dict[str, int] = {lvl: i for i, lvl in enumerate(STAGE_LEVELS)}


def level_index(level: str) -> int:
    """返回某 stage.level 在处理链中的序号（baseband=0 ... products=4）。"""
    return _LEVEL_ORDER_MAP[level]


@dataclass
class PipelineStage:
    """pipeline 中的一个处理阶段。

    Attributes:
        level: 该 stage 输出到的层级，取值见 STAGE_LEVELS。
        module: 处理函数名，对应 sat_pipeline_runner 中注册的 stage 处理函数；
                baseband 输入层为空串 ""。
        params: 该 stage 的解调/解码参数，照抄 SatDump JSON 实测值。
    """

    level: str
    module: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SatellitePipeline:
    """一颗卫星（或同一颗星的一种下行链路）的完整 pipeline 描述。

    Attributes:
        pipeline_id: 表内唯一标识。
        name: 展示名。
        frequencies: [(变体名, 频率 Hz)]，照抄 SatDump frequencies 数组。
        recommended_samplerate: SatDump 推荐的基带采样率 Hz。
        stages: 从 baseband 到 products 的有序处理阶段。
        user_params: 用户可调参数，{参数名: {"type":..., "value":..., "description":...}}。
    """

    pipeline_id: str
    name: str
    frequencies: List[Tuple[str, float]]
    recommended_samplerate: float
    stages: List[PipelineStage]
    user_params: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _baseband() -> PipelineStage:
    """baseband 输入层：空 module，仅代表数据起点。"""
    return PipelineStage(level="baseband", module="", params={})


# ---------------------------------------------------------------------------
# 卫星 pipeline 注册表。参数值均照抄 SatDump JSON，不做经验编造。
# ---------------------------------------------------------------------------
SATELLITE_PIPELINES: Dict[str, SatellitePipeline] = {

    # NOAA APT —— NOAA.json:197-270
    #   frequencies: NOAA-18 137.9125e6 / NOAA-19 137.1e6 / NOAA-15 137.62e6
    #   samplerate(基带) = 1e6；audio_wav 解调 symbolrate=50e3；products audio_samplerate=50e3
    "noaa_apt": SatellitePipeline(
        pipeline_id="noaa_apt",
        name="NOAA APT",
        frequencies=[
            ("NOAA-18", 137.9125e6),
            ("NOAA-19", 137.1e6),
            ("NOAA-15", 137.62e6),
        ],
        recommended_samplerate=1e6,
        stages=[
            _baseband(),
            # baseband IQ -> APT 音频（50kHz 调频解调）。当前未包装到调度器，
            # 由调度器按"未实现 stage"优雅返回，不抛异常。
            PipelineStage(level="soft", module="noaa_apt_demod",
                          params={"symbolrate": 50e3, "save_wav": False}),
            # 音频 -> A/B 双通道云图（挂接 noaa_apt_lite.decode_apt）。
            PipelineStage(level="products", module="noaa_apt_decode",
                          params={"audio_samplerate": 50e3,
                                  "max_crop_stddev": 3500,
                                  "save_unsynced": True,
                                  "align_timestamps": True}),
        ],
        user_params={
            "satellite_number": {"type": "options", "value": "19",
                                 "options": ["15", "18", "19"],
                                 "description": "APT 投影/叠加所需卫星编号"},
            "autocrop_wedges": {"type": "bool", "value": False,
                                "description": "自动裁剪 pass 顶部/底部静态噪声"},
            "sdrpp_noise_reduction": {"type": "bool", "value": True,
                                      "description": "SDR++ 风格 APT 降噪"},
        },
    ),

    # Meteor-M LRPT 72k (QPSK) —— Meteor-M.json:44-110
    #   Primary 137.1e6 / Backup 137.9e6；samplerate=1e6
    #   soft: psk_demod qpsk symbolrate=72e3 rrc_taps=31 rrc_alpha=0.5 pll_bw=0.002
    "meteor_m2_lrpt": SatellitePipeline(
        pipeline_id="meteor_m2_lrpt",
        name="Meteor-M LRPT 72k (QPSK)",
        frequencies=[
            ("Primary", 137.1e6),
            ("Backup", 137.9e6),
        ],
        recommended_samplerate=1e6,
        stages=[
            _baseband(),
            # IQ -> 软符号/CADU（挂接 meteor_sat.demodulate_lrpt，内部含 QPSK 解调
            # + 卷积去交织 + Viterbi + 解扰 + CADU 提取）。
            PipelineStage(level="soft", module="meteor_lrpt_demod",
                          params={"constellation": "qpsk",
                                  "symbolrate": 72e3,
                                  "rrc_taps": 31,
                                  "rrc_alpha": 0.5,
                                  "pll_bw": 0.002}),
            PipelineStage(level="cadu", module="meteor_lrpt_decoder",
                          params={"diff_decode": False}),
            PipelineStage(level="products", module="meteor_msumr_lrpt",
                          params={"m2x_mode": False, "max_fill_lines": 50}),
        ],
        user_params={
            "satellite_number": {"type": "options", "value": "Auto",
                                 "options": ["Auto", "M2", "M2-2", "M2-3", "M2-4"],
                                 "description": "覆盖下行卫星 ID"},
            "fill_missing": {"type": "bool", "value": False,
                             "description": "补线，纠正干扰/信号丢失造成的黑条"},
        },
    ),

    # Meteor-M2-x LRPT 80k (OQPSK) —— Meteor-M.json:195-263
    #   Primary 137.9e6 / Backup 137.1e6；samplerate=1e6
    #   soft: psk_demod oqpsk symbolrate=80e3 rrc_alpha=0.5 pll_bw=0.002
    #   cadu: meteor_lrpt_decoder viterbi_ber_threshold=0.300
    "meteor_m2x_lrpt_80k": SatellitePipeline(
        pipeline_id="meteor_m2x_lrpt_80k",
        name="Meteor-M2-x LRPT 80k (OQPSK)",
        frequencies=[
            ("Primary", 137.9e6),
            ("Backup", 137.1e6),
        ],
        recommended_samplerate=1e6,
        stages=[
            _baseband(),
            PipelineStage(level="soft", module="meteor_lrpt_demod",
                          params={"constellation": "oqpsk",
                                  "symbolrate": 80e3,
                                  "rrc_alpha": 0.5,
                                  "pll_bw": 0.002}),
            PipelineStage(level="cadu", module="meteor_lrpt_decoder",
                          params={"m2x_mode": True,
                                  "interleaved": True,
                                  "diff_decode": True,
                                  "viterbi_ber_threshold": 0.300,
                                  "viterbi_outsync_after": 20}),
            PipelineStage(level="products", module="meteor_msumr_lrpt",
                          params={"m2x_mode": True, "max_fill_lines": 50}),
        ],
        user_params={
            "satellite_number": {"type": "options", "value": "Auto",
                                 "options": ["Auto", "M2", "M2-2", "M2-3", "M2-4"],
                                 "description": "覆盖下行卫星 ID"},
            "fill_missing": {"type": "bool", "value": False,
                             "description": "补线，纠正干扰/信号丢失造成的黑条"},
        },
    ),

    # GK-2A LRIT (GEO 128.2E) —— GK2A.json:1-60
    #   LRIT 1692.14e6；samplerate=1e6；pkt_size=10240
    #   soft: psk_demod bpsk symbolrate=128e3 rrc_alpha=0.5 pll_bw=0.02 max_sps=3
    #   cadu: ccsds_conv_concat_decoder cadu_size=8192 viterbi_ber_threshold=0.300
    "gk2a_lrit": SatellitePipeline(
        pipeline_id="gk2a_lrit",
        name="GK-2A LRIT",
        frequencies=[("LRIT", 1692.14e6)],
        recommended_samplerate=1e6,
        stages=[
            _baseband(),
            PipelineStage(level="soft", module="gk2a_lrit_demod",
                          params={"constellation": "bpsk",
                                  "symbolrate": 128e3,
                                  "rrc_alpha": 0.5,
                                  "pll_bw": 0.02,
                                  "max_sps": 3}),
            PipelineStage(level="cadu", module="gk2a_cadu_decoder",
                          params={"constellation": "bpsk",
                                  "cadu_size": 8192,
                                  "viterbi_ber_threshold": 0.300,
                                  "viterbi_outsync_after": 20,
                                  "derandomize": True,
                                  "rs_i": 4,
                                  "rs_type": "rs223",
                                  "rs_usecheck": True}),
            # IQ -> 云图（挂接 gk2a_lrit.decode_iq_to_image，内部自含
            # BPSK 解调 + Viterbi + 帧同步 + RS + 重组装）。
            PipelineStage(level="products", module="gk2a_lrit_decode",
                          params={"write_images": True,
                                  "write_additional": True,
                                  "write_unknown": True}),
        ],
        user_params={
            "pkt_size": {"type": "int", "value": 10240,
                         "description": "LRIT 包长（1024*10）"},
        },
    ),

    # GOES-R HRIT —— GOES.json:47-145
    #   HRIT 1694.1e6；samplerate=6e6；pkt_size=10240
    #   soft: psk_demod bpsk symbolrate=927e3 rrc_alpha=0.5 pll_bw=0.02
    "goes_hrit": SatellitePipeline(
        pipeline_id="goes_hrit",
        name="GOES-R HRIT",
        frequencies=[("HRIT", 1694.1e6)],
        recommended_samplerate=6e6,
        stages=[
            _baseband(),
            PipelineStage(level="soft", module="psk_demod",
                          params={"constellation": "bpsk",
                                  "symbolrate": 927e3,
                                  "rrc_alpha": 0.5,
                                  "pll_bw": 0.02,
                                  "max_sps": 3}),
            PipelineStage(level="cadu", module="ccsds_conv_concat_decoder",
                          params={"constellation": "bpsk",
                                  "cadu_size": 8192,
                                  "viterbi_ber_threshold": 0.300,
                                  "viterbi_outsync_after": 20,
                                  "derandomize": True,
                                  "nrzm": True,
                                  "rs_i": 4,
                                  "rs_type": "rs223",
                                  "rs_usecheck": True}),
            PipelineStage(level="products", module="goes_lrit_data_decoder",
                          params={"max_fill_lines": 50,
                                  "write_images": True,
                                  "write_emwin": True,
                                  "write_messages": True,
                                  "write_dcs": False,
                                  "write_unknown": False}),
        ],
        user_params={
            "pkt_size": {"type": "int", "value": 10240,
                         "description": "HRIT 包长（1024*10）"},
            "fill_missing": {"type": "bool", "value": False,
                             "description": "补线，纠正干扰造成的黑条"},
            "write_emwin_nws": {"type": "bool", "value": True,
                                "description": "保存 EMWIN NWS 云图/天气图"},
        },
    ),

    # FY-3 A/B AHRPT —— FengYun-3.json:2-51
    #   Main 1704.5e6；samplerate=6e6
    #   soft: psk_demod qpsk symbolrate=2.8e6 rrc_alpha=0.5 pll_bw=0.003
    #   cadu: fengyun_ahrpt_decoder viterbi_ber_threshold=0.26
    "fy3_ahrpt": SatellitePipeline(
        pipeline_id="fy3_ahrpt",
        name="FY-3 A/B AHRPT",
        frequencies=[("Main", 1704.5e6)],
        recommended_samplerate=6e6,
        stages=[
            _baseband(),
            PipelineStage(level="soft", module="psk_demod",
                          params={"constellation": "qpsk",
                                  "symbolrate": 2.8e6,
                                  "rrc_alpha": 0.5,
                                  "pll_bw": 0.003}),
            PipelineStage(level="cadu", module="fengyun_ahrpt_decoder",
                          params={"viterbi_outsync_after": 20,
                                  "viterbi_ber_threshold": 0.26,
                                  "invert_second_viterbi": False}),
            PipelineStage(level="products", module="fy3_instruments",
                          params={"satellite": "fy3ab"}),
        ],
        user_params={
            "write_c10": {"type": "bool", "value": False,
                          "description": "生成 .C10 供 HRPT Reader 后处理"},
        },
    ),
}


def get_pipeline(pipeline_id: str) -> SatellitePipeline:
    """按 pipeline_id 取 pipeline；不存在则抛 KeyError。"""
    if pipeline_id not in SATELLITE_PIPELINES:
        raise KeyError(f"未知 pipeline_id: {pipeline_id}; "
                       f"可选: {sorted(SATELLITE_PIPELINES)}")
    return SATELLITE_PIPELINES[pipeline_id]


def list_pipelines() -> List[SatellitePipeline]:
    """返回全部已注册 pipeline（按注册顺序）。"""
    return list(SATELLITE_PIPELINES.values())
