# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 配置管理
==========================
管理 API key、模型、上下文长度、压缩阈值等配置。
支持 JSON 配置文件持久化，环境变量覆盖。
"""

import os
import json
import time
import shutil
import tempfile
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# 默认配置
DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "Qwen/Qwen3.6-35B-A3B",
    "max_context_tokens": 8192,
    "max_output_tokens": 2048,
    "compaction_threshold": 0.8,       # 上下文使用超过 80% 时触发压缩
    "compaction_target_ratio": 0.5,    # 压缩后目标使用率 50%
    "temperature": 0.7,
    "top_p": 0.9,
    "timeout": 60,
    # 工具输出预算：25 次过境的完整报告约 3.5k 字，TLE 缓存带降级标注时
    # 会到 4k+，旧值 4000 会稳定截断列表尾部（模型/用户看不到后半张列表）。
    # 提到 8000 保留整张列表；报告另有「全局状态去重」压缩，见 sdr_tools。
    "tool_output_max_chars": 8000,
    "enable_tool_calling": True,
    "enable_session_log": True,         # append-only 会话事件日志（~/.mbdsdr/sessions）
    "enable_self_evolution": True,      # 自进化默认开启（沙箱验证+快照回滚，不改真实文件）
    "sandbox_enabled": True,
    "auto_recovery": True,              # 一键恢复防幻觉变砖
    "language": "zh-CN",
    # 发射审计日志（mbdsdr_ai/tx_guard.py 落盘）。路径留空 = 自动选非 C 盘：
    # D:\mbdsdr\logs\tx_audit.jsonl（无 D 盘则程序所在盘/程序目录 logs）。
    "tx_audit_path": "",
    "tx_audit_max_mb": 50,              # 单文件上限（MB）
    "tx_audit_keep_files": 10,          # 总文件数（当前文件 + 轮转备份）
    "tx_audit_retention_days": 365,     # 备份最长保留天数（到点即删，不停止写入）
}


#: 配置文件历史键名 → 现行键名（一次性迁移表）。
#: 桌面端曾写 "api_base"，而 DEFAULT_CONFIG / main_window 读 "base_url"——
#: 统一为 base_url，读到旧键时兼容回退并迁移，消除同文件键名打架。
LEGACY_KEY_MAP = {
    "api_base": "base_url",
}


def normalize_config_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """配置键归一（纯函数）：旧键 api_base → base_url。

    - 已有 base_url 时旧键直接丢弃（新键优先，绝不覆盖）；
    - 幂等：对已归一的字典再跑一遍输出不变。
    """
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    for old, new in LEGACY_KEY_MAP.items():
        if old in out:
            if not out.get(new):
                out[new] = out[old]
            out.pop(old)
    return out


@dataclass
class AgentConfig:
    """Agent 配置数据类。"""
    api_key: str = ""
    base_url: str = "https://api.siliconflow.cn/v1"
    model: str = "Qwen/Qwen3.6-35B-A3B"

    # 上下文管理
    max_context_tokens: int = 8192
    max_output_tokens: int = 2048
    compaction_threshold: float = 0.8
    compaction_target_ratio: float = 0.5

    # 模型参数
    temperature: float = 0.7
    top_p: float = 0.9
    timeout: int = 60

    # 工具调用
    tool_output_max_chars: int = 8000
    enable_tool_calling: bool = True
    # 工具执行超时（秒）。<=0 = 不设超时（handler 内联执行，历史行为）。
    # 设正数后 registry 级生效，个别工具可用 register(timeout_s=) 覆盖。
    tool_timeout_s: float = 0.0
    # append-only 会话事件日志（"Model-visible ⟺ logged"）
    enable_session_log: bool = True

    # 自进化
    enable_self_evolution: bool = True
    sandbox_enabled: bool = True
    auto_recovery: bool = True

    # 其他
    language: str = "zh-CN"

    # 发射审计日志（tx_guard 落盘策略；path 留空 = 自动选非 C 盘路径）
    tx_audit_path: str = ""
    tx_audit_max_mb: int = 50
    tx_audit_keep_files: int = 10
    tx_audit_retention_days: int = 365

    # 运行时状态（不持久化）
    _config_path: Optional[str] = field(default=None, repr=False)

    def __post_init__(self):
        # 从环境变量覆盖
        env_key = os.environ.get("MBDSDR_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if env_key and not self.api_key:
            self.api_key = env_key

        # API Base 规范环境变量为 MBDSDR_BASE_URL；MBDSDR_API_BASE 是旧名，
        # 作兼容别名保留（不破坏已用旧名的用户）；OPENAI_BASE_URL 最后兜底。
        #
        # 优先级（2026-11-06 修）：**显式传入/配置文件里的值 > 环境变量 > 默认值**。
        # 旧式 `if env_url: self.base_url = env_url` 无条件覆盖，会让配置对话框
        # 里填的 base_url/model 不生效，而面板仍提示「已配置并保存: {用户填的}」
        # ——显示值与实际请求地址分叉，且用户查不出来。环境变量只在字段仍是
        # 默认（即调用方没有显式指定）时兜底，与上面 api_key 的既有规则一致。
        env_url = (os.environ.get("MBDSDR_BASE_URL")
                   or os.environ.get("MBDSDR_API_BASE")
                   or os.environ.get("OPENAI_BASE_URL"))
        if env_url and self.base_url == DEFAULT_CONFIG["base_url"]:
            self.base_url = env_url

        env_model = os.environ.get("MBDSDR_MODEL")
        if env_model and self.model == DEFAULT_CONFIG["model"]:
            self.model = env_model

        # 发射审计策略：环境变量优先（测试/受信任脚本可临时覆盖），
        # 其余从 Settings 面板写入的 JSON 键取值。路径为空表示自动选盘。
        env_audit_path = os.environ.get("MBDSDR_TX_AUDIT_PATH")
        if env_audit_path:
            self.tx_audit_path = env_audit_path
        for env_name, attr in (
                ("MBDSDR_TX_AUDIT_MAX_MB", "tx_audit_max_mb"),
                ("MBDSDR_TX_AUDIT_KEEP_FILES", "tx_audit_keep_files"),
                ("MBDSDR_TX_AUDIT_RETENTION_DAYS", "tx_audit_retention_days")):
            raw = os.environ.get(env_name)
            if raw:
                try:
                    setattr(self, attr, int(raw))
                except ValueError:
                    pass  # 坏值不覆盖，交给 tx_guard 侧夹到合法区间

    def to_dict(self) -> Dict[str, Any]:
        """转为字典（排除运行时状态）。"""
        d = asdict(self)
        d.pop("_config_path", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentConfig":
        """从字典创建（旧键 api_base 自动归一为 base_url）。"""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in normalize_config_keys(data).items()
                    if k in valid_fields}
        return cls(**filtered)

    def validate(self) -> list:
        """验证配置，返回错误列表。"""
        errors = []
        if not self.api_key:
            errors.append("api_key 为空，请设置 API key")
        if self.max_context_tokens < 1024:
            errors.append(f"max_context_tokens 过小: {self.max_context_tokens}，建议 >= 1024")
        if not (0 < self.compaction_threshold <= 1.0):
            errors.append(f"compaction_threshold 应在 (0, 1] 之间: {self.compaction_threshold}")
        if not (0 <= self.temperature <= 2):
            errors.append(f"temperature 应在 [0, 2] 之间: {self.temperature}")
        if self.tool_output_max_chars < 100:
            errors.append(f"tool_output_max_chars 过小: {self.tool_output_max_chars}")
        return errors


#: 最近一次 ``load_config`` / ``save_config`` 的人话失败原因；成功时为 None。
#: GUI 无控制台，``print`` 谁也看不见——上层必须读 ``last_error()`` 才能把
#: 「配置没读进来 / 没写下去」变成可见状态（tx_guard、设置面板等）。
_LAST_ERROR: Optional[str] = None


def last_error() -> Optional[str]:
    """返回最近一次配置 IO 的失败原因（成功或从未发生过则返回 None）。"""
    return _LAST_ERROR


def _set_error(message: Optional[str]) -> None:
    global _LAST_ERROR
    _LAST_ERROR = message
    if message:
        logger.warning("%s", message)
        print(f"警告: {message}")   # CLI/无日志配置时仍有一条可见通道


def _atomic_write_json(path: str, data: Dict[str, Any]) -> bool:
    """唯一临时文件 + fsync + ``os.replace`` 原子替换，绝不半写目标文件。

    旧式 ``open(path, "w")`` 在写盘中途失败（磁盘满/权限/进程被杀）会先把
    含 API key 的配置截断成半截 JSON，重启后解析失败；下一次任意保存再用
    默认值整份覆盖，用户配置与证据一起永久丢失。
    """
    directory = os.path.dirname(path) or "."
    tmp: Optional[str] = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".mbd-config-", suffix=".tmp",
                                   dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)       # 含 API key，仅所有者可读写
        except OSError:
            pass
        os.replace(tmp, path)
        tmp = None
        return True
    except (OSError, TypeError, ValueError) as exc:
        _set_error(f"配置写入失败（原文件未改动）：{type(exc).__name__}: {exc}")
        return False
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _backup_corrupt(path: str) -> Optional[str]:
    """把损坏配置复制成 ``*.corrupt-<时间戳>`` 留证，原文件保持不动（读操作
    不擅自删除用户文件）。返回备份路径；备份也失败时返回 None。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.corrupt-{stamp}"
    n = 0
    while os.path.exists(dst):
        n += 1
        dst = f"{path}.corrupt-{stamp}-{n}"
    try:
        shutil.copy2(path, dst)
        return dst
    except OSError:
        return None


def load_config(path: str = "~/.mbdsdr/config.json") -> AgentConfig:
    """从 JSON 文件加载配置。

    旧键 api_base 读后一次性迁移：内存里归一为 base_url，并把归一后的原形状
    字典**原子**回写磁盘（只改键名，不动其他字段）。

    损坏/不可读时：先复制一份 ``*.corrupt-*`` 留证，再用默认配置启动，并
    通过 :func:`last_error` 报出原因；``_config_path`` 仍然指向原路径，
    后续 :func:`save_config` 会先再备份一次才写，不会悄悄碾掉证据。
    """
    path = os.path.expanduser(path)

    def _defaults() -> AgentConfig:
        cfg = AgentConfig()
        cfg._config_path = path
        return cfg

    if not os.path.exists(path):
        _set_error(None)
        return _defaults()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("配置文件顶层不是 JSON 对象")
        normalized = normalize_config_keys(data)
        config = AgentConfig.from_dict(normalized)
        config._config_path = path
        # 配置文件里显式写过的键严格胜过环境变量（含"写的正好是默认值"这种
        # 边界），否则用户明明在文件里配了 base_url 却被机器上的 env 顶掉。
        for key in ("api_key", "base_url", "model"):
            if key in normalized:
                setattr(config, key, normalized[key])
        if normalized != data:
            # 迁移回写失败不阻断读取（旧键在内存里已归一）；错误单独留日志，
            # 不占用 last_error —— 本次"读"是成功的。
            if not _atomic_write_json(path, normalized):
                logger.warning("配置键迁移回写失败，旧键仍在磁盘上：%s",
                               _LAST_ERROR)
        _set_error(None)
        return config
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError) as exc:
        backup = _backup_corrupt(path)
        where = f"，已备份到 {os.path.basename(backup)}" if backup else \
                "（备份失败，请手动复制该文件）"
        _set_error(f"配置文件损坏或不可读（{type(exc).__name__}: {exc}）"
                   f"{where}，本次以默认配置启动；API key 等设置未生效")
        return _defaults()


def save_config(config: AgentConfig,
                path: Optional[str] = "~/.mbdsdr/config.json") -> bool:
    """保存配置到 JSON 文件；返回**是否真的写盘成功**。

    与 ``desktop/ai_panel.py`` 共用同一个 ``~/.mbdsdr/config.json``（两套写入
    者，见 docs/COORDINATION.md）。旧式 ``to_dict()`` 整份覆盖会把面板写入的
    额外键吃掉；这里改为按字段**合并**：保留文件里所有本模块不认识的键。
    写盘走原子替换，失败时原文件字节不变、``last_error()`` 给出原因。
    """
    target = os.path.expanduser(
        path or getattr(config, "_config_path", None) or "~/.mbdsdr/config.json")
    payload = config.to_dict()

    existing: Optional[Dict[str, Any]] = None
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as f:
                candidate = json.load(f)
            if isinstance(candidate, dict):
                existing = candidate
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            existing = None
        if existing is None:
            # 磁盘上已经是坏的：先留证再写，别把最后一份证据覆盖掉
            _backup_corrupt(target)
    merged = dict(existing or {})
    merged.update(payload)

    if not _atomic_write_json(target, merged):
        return False
    config._config_path = target
    _set_error(None)
    return True
