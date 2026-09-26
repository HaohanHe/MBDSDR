"""
MBDSDR 模块系统（module_system）
==================================

移植自 SDR++ 的模块管理架构（见 docs/learn/sdrpp_modules.md 第 1 节）：

  SDR++ C++                              本模块 Python
  ------------------------------------   ------------------------------------
  ModuleManager::Instance (module.h:43)   Module（ABC 基类）
  ModuleInfo_t (module.h:33-41)          Module.metadata / __init_subclass__
  ModuleManager::loadModule (module.cpp:5)  ModuleRegistry.register（动态 import 替代 dlopen）
  ModuleManager::createInstance (:86)     ModuleRegistry.create
  modules/instances map (:100-101)       ModuleRegistry._classes / _instances
  dsp::stream<T> 双缓冲 (stream.h:24)    SignalGraph 同步 pull/push（numpy ndarray 块）
  SourceHandler/SinkHandler (source.h)    ModuleType.source/sink/demod/tool + ports

设计要点：
  * 模块"类"与"实例"分离：register 注册类，create 出实例（对齐 module.cpp:86-106）。
  * 模块不直接持有彼此指针，只通过 (module, port_name) 端点连接；connect 时校验端口。
  * source 只有输出口，sink 只有输入口，demod/tool 两端都有（对齐 SDR++ 目录划分）。
  * 数据以 numpy ndarray 流式传递（默认 complex64 IQ 块）。
  * start/stop 沿图拓扑启停（先 source 后下游；停反向）。

MBDSDR 增强（比 SDR++ 多的点）：
  * ModuleRegistry.recommend_chain(features)：根据信号特征（带宽/占空比/峰值 SNR/
    频谱形状）建议解调模块链——SDR++ 没有这种自动推荐。
  * SignalGraph 支持多输入合并（mixer 节点）与多输出扇出。

红线：
  * 不伪造 IQ 数据；source 无设备时返回空块或抛错，绝不 mock。
  * 本文件只新建，不改任何现有共享 DSP 文件。
"""
from __future__ import annotations

import abc
import importlib
import inspect
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


# ----------------------------------------------------------------------
# 模块类型（对应 SDR++ 的 source_modules/ decoder_modules/ sink_modules/ 目录）
# ----------------------------------------------------------------------
class ModuleType:
    SOURCE = "source"     # 只出不进：硬件/文件 IQ
    SINK = "sink"         # 只进不出：显示/录制/解码输出
    DEMOD = "demod"       # 进+出：解调/信道化
    TOOL = "tool"         # 进+出：工具（FFT/分析/降噪）


# ----------------------------------------------------------------------
# 端口描述
# ----------------------------------------------------------------------
@dataclass
class Port:
    """一个数据流端口。dtype 期望的 numpy dtype；默认 complex64 IQ。"""
    name: str
    dtype: Any = np.complex64
    description: str = ""


# ----------------------------------------------------------------------
# 模块基类（对应 ModuleManager::Instance，module.h:43-50）
# ----------------------------------------------------------------------
class Module(abc.ABC):
    """所有 MBDSDR 模块的基类。

    子类需声明：
      MODULE_NAME    : str   —— 类名（注册用），对应 SDRPP_MOD_INFO.name
      MODULE_TYPE    : str   —— ModuleType 之一
      MODULE_DESC    : str   —— 一句话描述
      MAX_INSTANCES  : int   —— <=0 不限（对应 module.h:40 maxInstances）
    并实现 process()/start()/stop()。
    """

    MODULE_NAME: str = ""
    MODULE_TYPE: str = ""
    MODULE_DESC: str = ""
    MAX_INSTANCES: int = 1

    def __init__(self, instance_name: str, config: Optional[dict] = None):
        if not self.MODULE_NAME:
            raise ValueError(f"{type(self).__name__}.MODULE_NAME 未设置")
        self.instance_name = instance_name
        self.config: dict = dict(config or {})
        self._running = False
        # 端口默认值：子类可覆盖
        self.input_ports: List[Port] = []
        self.output_ports: List[Port] = []
        self._setup_ports()

    # -- 子类钩子 ---------------------------------------------------------
    def _setup_ports(self) -> None:
        """声明 input_ports / output_ports。默认按类型给一个 'in'/'out'。"""
        if self.MODULE_TYPE in (ModuleType.DEMOD, ModuleType.TOOL):
            self.input_ports = [Port("in")]
            self.output_ports = [Port("out")]
        elif self.MODULE_TYPE == ModuleType.SOURCE:
            self.input_ports = []
            self.output_ports = [Port("out")]
        elif self.MODULE_TYPE == ModuleType.SINK:
            self.input_ports = [Port("in")]
            self.output_ports = []

    def config_schema(self) -> Dict[str, dict]:
        """返回配置项 schema：{key: {"type":..., "default":..., "help":...}}。"""
        return {}

    @abc.abstractmethod
    def process(self, inputs: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        """同步处理一块数据。inputs={port_name: ndarray}，返回 {out_port: ndarray}。

        source 类型 inputs 为空 dict；sink 类型返回空 dict。
        """

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def post_init(self) -> None:
        """对应 SDR++ Instance::postInit()（module.h:46）——所有实例构造完后统一调用。"""

    @property
    def running(self) -> bool:
        return self._running

    # 便于调试
    def __repr__(self) -> str:
        return (f"<{type(self).__name__} '{self.instance_name}' "
                f"type={self.MODULE_TYPE}>")


# ----------------------------------------------------------------------
# 模块注册表（对应 ModuleManager，module.h:31-102）
# ----------------------------------------------------------------------
class ModuleRegistry:
    """模块类注册表 + 实例工厂。

    - register(cls)：注册一个 Module 子类（对应 loadModule 后入 modules map，module.cpp:82）。
    - create(instance_name, module_name)：实例化（对应 createInstance，module.cpp:86）。
    - dynamic_load(module_path)：从 'pkg.mod:Cls' 字符串动态 import（对应 dlopen，module.cpp:34）。
    - recommend_chain(features)：【MBDSDR 增强】按信号特征推荐模块链。
    """

    def __init__(self) -> None:
        self._classes: "OrderedDict[str, type]" = OrderedDict()
        self._instances: "OrderedDict[str, Module]" = OrderedDict()

    # -- 类注册 -----------------------------------------------------------
    def register(self, cls: type) -> None:
        if not isinstance(cls, type) or not issubclass(cls, Module):
            raise TypeError("register() 需要 Module 子类")
        name = cls.MODULE_NAME
        if not name:
            raise ValueError(f"{cls.__name__} 未设置 MODULE_NAME")
        if name in self._classes:
            raise ValueError(f"模块类 '{name}' 已注册（重名，对应 module.cpp:71）")
        self._classes[name] = cls

    def dynamic_load(self, module_path: str) -> type:
        """从 'pkg.module:ClassName' 动态加载并注册。对应 dlopen+dlsym(module.cpp:34-44)。"""
        if ":" not in module_path:
            raise ValueError("dynamic_load 需要 'pkg.module:ClassName' 形式")
        mod_path, cls_name = module_path.rsplit(":", 1)
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, cls_name)
        self.register(cls)
        return cls

    # -- 查询 -------------------------------------------------------------
    def get(self, module_name: str) -> type:
        if module_name not in self._classes:
            raise KeyError(f"模块类 '{module_name}' 未注册（已注册: {list(self._classes)}）")
        return self._classes[module_name]

    def list(self, type: Optional[str] = None) -> List[dict]:
        out = []
        for name, cls in self._classes.items():
            if type and cls.MODULE_TYPE != type:
                continue
            out.append({
                "name": name,
                "type": cls.MODULE_TYPE,
                "description": cls.MODULE_DESC,
                "max_instances": cls.MAX_INSTANCES,
            })
        return out

    # -- 实例工厂（对应 createInstance, module.cpp:86-106）-----------------
    def create(self, instance_name: str, module_name: str,
               config: Optional[dict] = None) -> Module:
        cls = self.get(module_name)
        if instance_name in self._instances:
            raise ValueError(f"实例 '{instance_name}' 已存在（对应 module.cpp:91）")
        if cls.MAX_INSTANCES > 0:
            count = sum(1 for m in self._instances.values()
                        if type(m).__name__ == cls.__name__)
            if count >= cls.MAX_INSTANCES:
                raise ValueError(
                    f"模块 '{module_name}' 实例数达上限 {cls.MAX_INSTANCES}"
                    f"（对应 module.cpp:96）")
        inst = cls(instance_name, config)
        self._instances[instance_name] = inst
        return inst

    def get_instance(self, instance_name: str) -> Module:
        return self._instances[instance_name]

    def delete_instance(self, instance_name: str) -> None:
        inst = self._instances.pop(instance_name, None)
        if inst is not None and inst.running:
            inst.stop()

    def instances(self) -> Dict[str, Module]:
        return dict(self._instances)

    def post_init_all(self) -> None:
        """对应 doPostInitAll（module.cpp:181-186）。"""
        for inst in self._instances.values():
            inst.post_init()

    # -- MBDSDR 增强：AI 推荐模块链 --------------------------------------
    def recommend_chain(self, features: Dict[str, Any]) -> List[str]:
        """根据信号特征建议解调/处理模块链。

        features 可含：
          bandwidth_hz : float   —— 信号带宽
          mode_hint    : str     —— 'FM'/'AM'/'CW'/'DIG'...
          peak_snr_db  : float   —— 峰值信噪比
          is_continuous: bool    —— 是否连续载波（vs 突发）
        返回建议的模块类名列表（不含 source）。无强规则时给空列表。
        纯规则实现，可被后续 LLM 替换。
        """
        rec: List[str] = []
        mode = (features.get("mode_hint") or "").upper()
        bw = float(features.get("bandwidth_hz", 0) or 0)

        if mode == "FM" or (bw >= 120e3 and bw <= 200e3):
            rec.append("wfm_demod")
        elif mode == "NFM" or (bw >= 8e3 and bw < 30e3):
            rec.append("nfm_demod")
        elif mode == "AM" or (bw > 0 and bw <= 10e3 and features.get("is_continuous")):
            rec.append("am_demod")
        elif mode == "CW" or bw < 200:
            rec.append("cw_demod")
        elif mode in ("DIG", "DATA"):
            rec.append("digital_demod")

        # 音频后处理：解调后接音量
        if rec and rec[-1] in ("wfm_demod", "nfm_demod", "am_demod", "cw_demod"):
            rec.append("audio_out")
        return rec


# ----------------------------------------------------------------------
# 信号图（简化版 GNU Radio / SDR++ stream 连接）
# ----------------------------------------------------------------------
@dataclass
class _Edge:
    src: str          # 源实例名
    src_port: str     # 源输出端口名
    dst: str          # 目的实例名
    dst_port: str     # 目的输入端口名


class SignalGraph:
    """模块间流式连接图。

    - connect(src_inst, src_port, dst_inst, dst_port)：连一条边。
    - run_one_block(source_inst, block)：从 source 推一块数据，沿拓扑传播到所有 sink。
      多输入模块在被多个上游连接时，按端口名收集上游输出。
    - start/stop：沿图拓扑启停（先 source，后下游）。
    """

    def __init__(self, registry: ModuleRegistry):
        self.reg = registry
        self._edges: List[_Edge] = []
        # dst -> list of (src, src_port, dst_port)
        self._inputs: Dict[str, List[_Edge]] = {}
        # src -> list of (src_port, dst, dst_port)
        self._outputs: Dict[str, List[_Edge]] = {}

    def connect(self, src: str, src_port: str, dst: str, dst_port: str) -> None:
        si = self.reg.get_instance(src)
        di = self.reg.get_instance(dst)
        if not any(p.name == src_port for p in si.output_ports):
            raise ValueError(f"源 '{src}' 没有输出端口 '{src_port}'，"
                             f"可用: {[p.name for p in si.output_ports]}")
        if not any(p.name == dst_port for p in di.input_ports):
            raise ValueError(f"目的 '{dst}' 没有输入端口 '{dst_port}'，"
                             f"可用: {[p.name for p in di.input_ports]}")
        edge = _Edge(src, src_port, dst, dst_port)
        self._edges.append(edge)
        self._inputs.setdefault(dst, []).append(edge)
        self._outputs.setdefault(src, []).append(edge)

    # -- 拓扑序 -----------------------------------------------------------
    def _topo_order(self) -> List[str]:
        """Kahn 算法：source 在前，sink 在后。"""
        indeg: Dict[str, int] = {n: 0 for n in self.reg.instances()}
        for e in self._edges:
            indeg[e.dst] = indeg.get(e.dst, 0) + 1
        queue = [n for n, d in indeg.items() if d == 0]
        order: List[str] = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for e in self._outputs.get(n, []):
                indeg[e.dst] -= 1
                if indeg[e.dst] == 0:
                    queue.append(e.dst)
        if len(order) != len(self.reg.instances()):
            raise RuntimeError("信号图存在环")
        return order

    # -- 启停 -------------------------------------------------------------
    def start(self) -> None:
        for name in self._topo_order():
            inst = self.reg.get_instance(name)
            if not inst.running:
                inst.start()

    def stop(self) -> None:
        for name in reversed(self._topo_order()):
            inst = self.reg.get_instance(name)
            if inst.running:
                inst.stop()

    # -- 数据驱动 ---------------------------------------------------------
    def run_one_block(self, source_name: str, block: np.ndarray) -> Dict[str, np.ndarray]:
        """把 block 喂给 source，沿图传播。返回各 sink 实例名 -> 其收到的数据。"""
        order = self._topo_order()
        # 每个实例的当前输出缓存 {inst_name: {port: ndarray}}
        cache: Dict[str, Dict[str, np.ndarray]] = {}
        sinks_out: Dict[str, np.ndarray] = {}

        for name in order:
            inst = self.reg.get_instance(name)
            if name == source_name:
                # source：把外部 block 作为它的输出（source.process 可覆写做变换）
                outs = inst.process({})
                # source 可选择忽略外部 block 并自己产数据；这里默认把外部 block 广播到所有输出口
                if not outs:
                    outs = {p.name: block for p in inst.output_ports}
                cache[name] = outs
            else:
                # 收集来自上游的输入
                inputs: Dict[str, np.ndarray] = {}
                for e in self._inputs.get(name, []):
                    src_out = cache.get(e.src, {})
                    if e.src_port in src_out:
                        inputs[e.dst_port] = src_out[e.src_port]
                outs = inst.process(inputs) or {}
                cache[name] = outs
                if inst.MODULE_TYPE == ModuleType.SINK:
                    # sink 把它收到的输入作为对外可见输出
                    for e in self._inputs.get(name, []):
                        sinks_out[name + ":" + e.dst_port] = inputs.get(e.dst_port, np.array([]))
        return sinks_out

    def edges(self) -> List[dict]:
        return [{"src": e.src, "src_port": e.src_port,
                 "dst": e.dst, "dst_port": e.dst_port} for e in self._edges]
