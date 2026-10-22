# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 配置管理
==========================
管理 API key、模型、上下文长度、压缩阈值等配置。
支持 JSON 配置文件持久化，环境变量覆盖。
"""

import os
import json
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any


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
    "tool_output_max_chars": 4000,     # 工具输出最大字符数，超过截断/写文件
    "enable_tool_calling": True,
    "enable_self_evolution": True,      # 自进化默认开启（沙箱验证+快照回滚，不改真实文件）
    "sandbox_enabled": True,
    "auto_recovery": True,              # 一键恢复防幻觉变砖
    "language": "zh-CN",
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
    tool_output_max_chars: int = 4000
    enable_tool_calling: bool = True

    # 自进化
    enable_self_evolution: bool = True
    sandbox_enabled: bool = True
    auto_recovery: bool = True

    # 其他
    language: str = "zh-CN"

    # 运行时状态（不持久化）
    _config_path: Optional[str] = field(default=None, repr=False)

    def __post_init__(self):
        # 从环境变量覆盖
        env_key = os.environ.get("MBDSDR_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if env_key and not self.api_key:
            self.api_key = env_key

        # API Base 规范环境变量为 MBDSDR_BASE_URL；MBDSDR_API_BASE 是旧名，
        # 作兼容别名保留（不破坏已用旧名的用户）；OPENAI_BASE_URL 最后兜底。
        env_url = (os.environ.get("MBDSDR_BASE_URL")
                   or os.environ.get("MBDSDR_API_BASE")
                   or os.environ.get("OPENAI_BASE_URL"))
        if env_url:
            self.base_url = env_url

        env_model = os.environ.get("MBDSDR_MODEL")
        if env_model:
            self.model = env_model

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


def load_config(path: str = "~/.mbdsdr/config.json") -> AgentConfig:
    """从 JSON 文件加载配置。

    旧键 api_base 读后一次性迁移：内存里归一为 base_url，并把归一后的原形状
    字典回写磁盘（只改键名，不动其他字段）。
    """
    path = os.path.expanduser(path)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            normalized = normalize_config_keys(data)
            config = AgentConfig.from_dict(normalized)
            config._config_path = path
            if normalized != data:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(normalized, f, indent=2, ensure_ascii=False)
                    os.chmod(path, 0o600)  # 含 API key，仅所有者可读写
                except OSError:
                    pass  # 回写失败不阻断读取；下次读到旧键仍可归一
            return config
        except (json.JSONDecodeError, IOError) as e:
            print(f"警告: 配置文件加载失败 ({e})，使用默认配置")
    return AgentConfig()


def save_config(config: AgentConfig, path: str = "~/.mbdsdr/config.json"):
    """保存配置到 JSON 文件。"""
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
    try:
        os.chmod(path, 0o600)  # 含 API key，仅所有者可读写
    except OSError:
        pass
    config._config_path = path
