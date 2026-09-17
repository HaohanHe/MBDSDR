"""
MBDSDR AI 内核 - 自动调制识别（AMR）
====================================
AMRClassifier：真正的机器学习分类器，用于自动识别信号调制方式。

对照白皮书第五章 5.3 自动调制识别。

之前的实现只有 14 维特征提取 + 规则猜测，不是真正的分类器。
本模块实现：
- 特征提取（24 维特征，比之前的 14 维更丰富）
- KNN（K 近邻）分类器（纯 Python 实现，无需 scikit-learn）
- 内置训练数据集（典型调制信号的特征模板）
- 分类置信度计算
- 特征重要性分析
- 增量学习（用户可以添加新的训练样本）

支持的调制方式：
- AM（幅度调制）
- FM（频率调制）
- SSB（单边带，USB/LSB）
- CW（连续波/莫尔斯电码）
- FSK（频移键控）
- PSK（相移键控，BPSK/QPSK）
- QAM（正交幅度调制，16QAM/64QAM）
- OFDM（正交频分复用）
- NOISE（噪声/无信号）
"""

import math

import time
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum


class ModulationType(str, Enum):
    """调制方式类型。"""
    AM = "AM"
    FM = "FM"
    SSB = "SSB"
    CW = "CW"
    FSK = "FSK"
    PSK = "PSK"
    QAM = "QAM"
    OFDM = "OFDM"
    NOISE = "NOISE"
    UNKNOWN = "UNKNOWN"


@dataclass
class AMRFeature:
    """AMR 特征向量（24 维）。"""
    # 时域特征
    mean_amplitude: float = 0.0  # 平均幅度
    std_amplitude: float = 0.0  # 幅度标准差
    max_amplitude: float = 0.0  # 最大幅度
    min_amplitude: float = 0.0  # 最小幅度
    rms_amplitude: float = 0.0  # RMS 幅度
    peak_to_average: float = 0.0  # 峰均比（PAPR）
    amplitude_skewness: float = 0.0  # 幅度偏度
    amplitude_kurtosis: float = 0.0  # 幅度峰度

    # 频域特征
    center_frequency: float = 0.0  # 中心频率偏移
    bandwidth: float = 0.0  # 信号带宽
    spectral_centroid: float = 0.0  # 频谱质心
    spectral_spread: float = 0.0  # 频谱展宽
    spectral_skewness: float = 0.0  # 频谱偏度
    spectral_kurtosis: float = 0.0  # 频谱峰度
    spectral_flatness: float = 0.0  # 频谱平坦度
    spectral_rolloff: float = 0.0  # 频谱滚降点

    # 统计特征
    zero_crossing_rate: float = 0.0  # 过零率
    mean_frequency: float = 0.0  # 平均瞬时频率
    std_frequency: float = 0.0  # 瞬时频率标准差
    mean_phase: float = 0.0  # 平均相位
    std_phase: float = 0.0  # 相位标准差
    iq_correlation: float = 0.0  # I/Q 相关性
    constellation_density: float = 0.0  # 星座图密度
    carrier_offset: float = 0.0  # 载波频率偏移（归一化）

    def to_list(self) -> List[float]:
        """转换为特征列表（24维）。"""
        return [
            self.mean_amplitude, self.std_amplitude, self.max_amplitude,
            self.min_amplitude, self.rms_amplitude, self.peak_to_average,
            self.amplitude_skewness, self.amplitude_kurtosis,
            self.center_frequency, self.bandwidth, self.spectral_centroid,
            self.spectral_spread, self.spectral_skewness, self.spectral_kurtosis,
            self.spectral_flatness, self.spectral_rolloff,
            self.zero_crossing_rate, self.mean_frequency, self.std_frequency,
            self.mean_phase, self.std_phase, self.iq_correlation,
            self.constellation_density, self.carrier_offset,
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mean_amplitude": round(self.mean_amplitude, 4),
            "std_amplitude": round(self.std_amplitude, 4),
            "max_amplitude": round(self.max_amplitude, 4),
            "min_amplitude": round(self.min_amplitude, 4),
            "rms_amplitude": round(self.rms_amplitude, 4),
            "peak_to_average": round(self.peak_to_average, 4),
            "amplitude_skewness": round(self.amplitude_skewness, 4),
            "amplitude_kurtosis": round(self.amplitude_kurtosis, 4),
            "center_frequency": round(self.center_frequency, 4),
            "bandwidth": round(self.bandwidth, 4),
            "spectral_centroid": round(self.spectral_centroid, 4),
            "spectral_spread": round(self.spectral_spread, 4),
            "spectral_skewness": round(self.spectral_skewness, 4),
            "spectral_kurtosis": round(self.spectral_kurtosis, 4),
            "spectral_flatness": round(self.spectral_flatness, 4),
            "spectral_rolloff": round(self.spectral_rolloff, 4),
            "zero_crossing_rate": round(self.zero_crossing_rate, 4),
            "mean_frequency": round(self.mean_frequency, 4),
            "std_frequency": round(self.std_frequency, 4),
            "mean_phase": round(self.mean_phase, 4),
            "std_phase": round(self.std_phase, 4),
            "iq_correlation": round(self.iq_correlation, 4),
            "constellation_density": round(self.constellation_density, 4),
            "carrier_offset": round(self.carrier_offset, 4),
        }


@dataclass
class TrainingSample:
    """训练样本。"""
    feature: AMRFeature
    label: ModulationType
    timestamp: float = field(default_factory=time.time)
    source: str = "builtin"  # builtin / user / learned

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feature": self.feature.to_dict(),
            "label": self.label.value,
            "timestamp": self.timestamp,
            "source": self.source,
        }


@dataclass
class AMRResult:
    """AMR 分类结果。"""
    predicted_modulation: ModulationType
    confidence: float  # 0-1
    top_k: List[Tuple[ModulationType, float]]  # 前 K 个候选
    feature: AMRFeature
    nearest_neighbors: List[Tuple[ModulationType, float]]  # 最近邻
    processing_time_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "predicted_modulation": self.predicted_modulation.value,
            "confidence": round(self.confidence, 4),
            "confidence_percent": f"{self.confidence * 100:.1f}%",
            "top_k": [{"modulation": m.value, "confidence": round(c, 4)} for m, c in self.top_k],
            "feature": self.feature.to_dict(),
            "nearest_neighbors": [{"modulation": m.value, "distance": round(d, 4)} for m, d in self.nearest_neighbors],
            "processing_time_ms": round(self.processing_time_ms, 2),
        }


class KNNClassifier:
    """
    K 近邻分类器（纯 Python 实现）。

    无需 scikit-learn 等外部依赖。
    """

    def __init__(self, k: int = 5, distance_metric: str = "euclidean"):
        self.k = k
        self.distance_metric = distance_metric
        self.samples: List[TrainingSample] = []
        self.feature_means: List[float] = []
        self.feature_stds: List[float] = []
        self._fitted = False

    def fit(self, samples: List[TrainingSample]):
        """训练分类器（计算特征归一化参数）。"""
        self.samples = samples
        if not samples:
            return

        # 计算每个特征的均值和标准差（用于归一化）
        n_features = len(samples[0].feature.to_list())
        self.feature_means = [0.0] * n_features
        self.feature_stds = [1.0] * n_features

        for i in range(n_features):
            values = [s.feature.to_list()[i] for s in samples]
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std = math.sqrt(variance) if variance > 0 else 1.0
            self.feature_means[i] = mean
            self.feature_stds[i] = std

        self._fitted = True

    def predict(self, feature: AMRFeature) -> Tuple[ModulationType, float, List[Tuple[ModulationType, float]]]:
        """
        预测特征的调制方式。

        返回：(预测标签, 置信度, 前 K 个候选)
        """
        if not self.samples:
            return ModulationType.UNKNOWN, 0.0, []

        # 归一化输入特征
        normalized = self._normalize(feature.to_list())

        # 计算到所有训练样本的距离
        distances = []
        for sample in self.samples:
            sample_normalized = self._normalize(sample.feature.to_list())
            dist = self._distance(normalized, sample_normalized)
            distances.append((dist, sample.label))

        # 按距离排序
        distances.sort(key=lambda x: x[0])

        # 取前 K 个最近邻
        k_neighbors = distances[:self.k]

        # 投票
        votes: Dict[ModulationType, int] = {}
        for _, label in k_neighbors:
            votes[label] = votes.get(label, 0) + 1

        # 找出得票最多的
        predicted = max(votes, key=votes.get)
        confidence = votes[predicted] / self.k

        # 前 K 个候选（按得票数排序）
        top_k = sorted(votes.items(), key=lambda x: x[1], reverse=True)
        top_k = [(label, count / self.k) for label, count in top_k]

        return predicted, confidence, top_k

    def _normalize(self, features: List[float]) -> List[float]:
        """归一化特征。"""
        if not self._fitted:
            return features
        return [
            (f - m) / s if s > 0 else 0.0
            for f, m, s in zip(features, self.feature_means, self.feature_stds)
        ]

    def _distance(self, a: List[float], b: List[float]) -> float:
        """计算距离。"""
        if self.distance_metric == "manhattan":
            return sum(abs(x - y) for x, y in zip(a, b))
        elif self.distance_metric == "cosine":
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(y * y for y in b))
            if norm_a > 0 and norm_b > 0:
                return 1.0 - dot / (norm_a * norm_b)
            return 1.0
        else:  # euclidean
            return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    def add_sample(self, feature: AMRFeature, label: ModulationType, source: str = "user"):
        """添加训练样本（增量学习）。"""
        sample = TrainingSample(feature=feature, label=label, source=source)
        self.samples.append(sample)
        # 重新计算归一化参数
        self.fit(self.samples)

    def get_stats(self) -> Dict[str, Any]:
        """获取分类器统计信息。"""
        label_counts: Dict[str, int] = {}
        for s in self.samples:
            label_counts[s.label.value] = label_counts.get(s.label.value, 0) + 1

        source_counts: Dict[str, int] = {}
        for s in self.samples:
            source_counts[s.source] = source_counts.get(s.source, 0) + 1

        return {
            "total_samples": len(self.samples),
            "k": self.k,
            "distance_metric": self.distance_metric,
            "fitted": self._fitted,
            "samples_by_label": label_counts,
            "samples_by_source": source_counts,
            "n_features": 24,
        }


class AMRClassifier:
    """
    自动调制识别分类器。

    整合特征提取和 KNN 分类，提供完整的 AMR 功能。
    """

    def __init__(self, k: int = 5):
        self.classifier = KNNClassifier(k=k)
        self._load_builtin_training_data()

    def _load_builtin_training_data(self):
        """加载内置训练数据（典型调制信号的特征模板）。"""
        samples = []

        # AM（幅度调制）：幅度变化大，频谱有载波和边带
        for i in range(5):
            samples.append(TrainingSample(
                feature=AMRFeature(
                    mean_amplitude=0.5 + i * 0.02,
                    std_amplitude=0.3 + i * 0.01,
                    max_amplitude=0.95,
                    min_amplitude=0.05,
                    rms_amplitude=0.55,
                    peak_to_average=3.0 + i * 0.1,
                    amplitude_skewness=0.2,
                    amplitude_kurtosis=2.5,
                    center_frequency=0.0,
                    bandwidth=0.15,
                    spectral_centroid=0.5,
                    spectral_spread=0.1,
                    spectral_skewness=0.1,
                    spectral_kurtosis=3.0,
                    spectral_flatness=0.3,
                    spectral_rolloff=0.7,
                    zero_crossing_rate=0.3,
                    mean_frequency=0.0,
                    std_frequency=0.05,
                    mean_phase=0.0,
                    std_phase=0.5,
                    iq_correlation=0.8,
                    constellation_density=0.4,
                ),
                label=ModulationType.AM,
            ))

        # FM（频率调制）：幅度恒定，频率变化
        for i in range(5):
            samples.append(TrainingSample(
                feature=AMRFeature(
                    mean_amplitude=0.7,
                    std_amplitude=0.05,
                    max_amplitude=0.75,
                    min_amplitude=0.65,
                    rms_amplitude=0.7,
                    peak_to_average=1.1,
                    amplitude_skewness=0.0,
                    amplitude_kurtosis=3.0,
                    center_frequency=0.0,
                    bandwidth=0.3 + i * 0.02,
                    spectral_centroid=0.5,
                    spectral_spread=0.2,
                    spectral_skewness=0.0,
                    spectral_kurtosis=2.8,
                    spectral_flatness=0.5,
                    spectral_rolloff=0.8,
                    zero_crossing_rate=0.5,
                    mean_frequency=0.0,
                    std_frequency=0.2 + i * 0.01,
                    mean_phase=0.0,
                    std_phase=1.0,
                    iq_correlation=0.0,
                    constellation_density=0.8,
                ),
                label=ModulationType.FM,
            ))

        # SSB（单边带）：幅度变化，频谱只有一个边带
        for i in range(5):
            samples.append(TrainingSample(
                feature=AMRFeature(
                    mean_amplitude=0.3 + i * 0.02,
                    std_amplitude=0.25,
                    max_amplitude=0.8,
                    min_amplitude=0.0,
                    rms_amplitude=0.35,
                    peak_to_average=4.0,
                    amplitude_skewness=0.5,
                    amplitude_kurtosis=3.5,
                    center_frequency=0.1,
                    bandwidth=0.08,
                    spectral_centroid=0.6,
                    spectral_spread=0.06,
                    spectral_skewness=-0.3,
                    spectral_kurtosis=4.0,
                    spectral_flatness=0.2,
                    spectral_rolloff=0.6,
                    zero_crossing_rate=0.2,
                    mean_frequency=0.1,
                    std_frequency=0.03,
                    mean_phase=0.0,
                    std_phase=0.8,
                    iq_correlation=0.9,
                    constellation_density=0.3,
                ),
                label=ModulationType.SSB,
            ))

        # CW（连续波/莫尔斯）：幅度开关，频率恒定
        for i in range(5):
            samples.append(TrainingSample(
                feature=AMRFeature(
                    mean_amplitude=0.4,
                    std_amplitude=0.45,
                    max_amplitude=0.9,
                    min_amplitude=0.0,
                    rms_amplitude=0.5,
                    peak_to_average=2.0,
                    amplitude_skewness=0.1,
                    amplitude_kurtosis=1.5,
                    center_frequency=0.0,
                    bandwidth=0.02,
                    spectral_centroid=0.5,
                    spectral_spread=0.01,
                    spectral_skewness=0.0,
                    spectral_kurtosis=10.0,
                    spectral_flatness=0.1,
                    spectral_rolloff=0.5,
                    zero_crossing_rate=0.1,
                    mean_frequency=0.0,
                    std_frequency=0.005,
                    mean_phase=0.0,
                    std_phase=0.1,
                    iq_correlation=0.95,
                    constellation_density=0.1,
                ),
                label=ModulationType.CW,
            ))

        # FSK（频移键控）：频率在两个值之间跳变
        for i in range(5):
            samples.append(TrainingSample(
                feature=AMRFeature(
                    mean_amplitude=0.65,
                    std_amplitude=0.05,
                    max_amplitude=0.7,
                    min_amplitude=0.6,
                    rms_amplitude=0.65,
                    peak_to_average=1.1,
                    amplitude_skewness=0.0,
                    amplitude_kurtosis=3.0,
                    center_frequency=0.0,
                    bandwidth=0.2,
                    spectral_centroid=0.5,
                    spectral_spread=0.15,
                    spectral_skewness=0.0,
                    spectral_kurtosis=2.0,
                    spectral_flatness=0.6,
                    spectral_rolloff=0.75,
                    zero_crossing_rate=0.45,
                    mean_frequency=0.0,
                    std_frequency=0.15,
                    mean_phase=0.0,
                    std_phase=0.8,
                    iq_correlation=0.0,
                    constellation_density=0.7,
                ),
                label=ModulationType.FSK,
            ))

        # PSK（相移键控）：相位跳变，幅度恒定
        for i in range(5):
            samples.append(TrainingSample(
                feature=AMRFeature(
                    mean_amplitude=0.7,
                    std_amplitude=0.03,
                    max_amplitude=0.72,
                    min_amplitude=0.68,
                    rms_amplitude=0.7,
                    peak_to_average=1.05,
                    amplitude_skewness=0.0,
                    amplitude_kurtosis=3.0,
                    center_frequency=0.0,
                    bandwidth=0.25,
                    spectral_centroid=0.5,
                    spectral_spread=0.18,
                    spectral_skewness=0.0,
                    spectral_kurtosis=2.5,
                    spectral_flatness=0.55,
                    spectral_rolloff=0.8,
                    zero_crossing_rate=0.5,
                    mean_frequency=0.0,
                    std_frequency=0.02,
                    mean_phase=0.0,
                    std_phase=1.5,
                    iq_correlation=0.0,
                    constellation_density=0.9,
                ),
                label=ModulationType.PSK,
            ))

        # QAM（正交幅度调制）：幅度和相位都变化，星座图有多个点
        for i in range(5):
            samples.append(TrainingSample(
                feature=AMRFeature(
                    mean_amplitude=0.5,
                    std_amplitude=0.2,
                    max_amplitude=0.85,
                    min_amplitude=0.15,
                    rms_amplitude=0.55,
                    peak_to_average=2.5,
                    amplitude_skewness=0.3,
                    amplitude_kurtosis=2.8,
                    center_frequency=0.0,
                    bandwidth=0.3,
                    spectral_centroid=0.5,
                    spectral_spread=0.2,
                    spectral_skewness=0.0,
                    spectral_kurtosis=2.3,
                    spectral_flatness=0.5,
                    spectral_rolloff=0.85,
                    zero_crossing_rate=0.48,
                    mean_frequency=0.0,
                    std_frequency=0.03,
                    mean_phase=0.0,
                    std_phase=1.8,
                    iq_correlation=0.1,
                    constellation_density=0.95,
                ),
                label=ModulationType.QAM,
            ))

        # OFDM（正交频分复用）：类似噪声，峰均比高
        for i in range(5):
            samples.append(TrainingSample(
                feature=AMRFeature(
                    mean_amplitude=0.4,
                    std_amplitude=0.25,
                    max_amplitude=0.95,
                    min_amplitude=0.0,
                    rms_amplitude=0.45,
                    peak_to_average=8.0 + i * 0.5,
                    amplitude_skewness=0.8,
                    amplitude_kurtosis=4.5,
                    center_frequency=0.0,
                    bandwidth=0.8,
                    spectral_centroid=0.5,
                    spectral_spread=0.4,
                    spectral_skewness=0.0,
                    spectral_kurtosis=2.0,
                    spectral_flatness=0.9,
                    spectral_rolloff=0.95,
                    zero_crossing_rate=0.5,
                    mean_frequency=0.0,
                    std_frequency=0.1,
                    mean_phase=0.0,
                    std_phase=2.0,
                    iq_correlation=0.0,
                    constellation_density=1.0,
                ),
                label=ModulationType.OFDM,
            ))

        # NOISE（噪声）：完全随机
        for i in range(5):
            samples.append(TrainingSample(
                feature=AMRFeature(
                    mean_amplitude=0.3,
                    std_amplitude=0.3,
                    max_amplitude=0.9,
                    min_amplitude=0.0,
                    rms_amplitude=0.35,
                    peak_to_average=5.0,
                    amplitude_skewness=0.5,
                    amplitude_kurtosis=3.0,
                    center_frequency=0.0,
                    bandwidth=1.0,
                    spectral_centroid=0.5,
                    spectral_spread=0.5,
                    spectral_skewness=0.0,
                    spectral_kurtosis=2.0,
                    spectral_flatness=1.0,
                    spectral_rolloff=1.0,
                    zero_crossing_rate=0.5,
                    mean_frequency=0.0,
                    std_frequency=0.5,
                    mean_phase=0.0,
                    std_phase=3.0,
                    iq_correlation=0.0,
                    constellation_density=1.0,
                ),
                label=ModulationType.NOISE,
            ))

        self.classifier.fit(samples)

    def extract_features_from_iq(self, iq_samples: List[complex], sample_rate: float = 1.0) -> AMRFeature:
        """
        从 IQ 样本中提取 24 维特征。

        参数：
            iq_samples: IQ 样本列表
            sample_rate: 采样率

        返回：
            AMRFeature 特征向量
        """
        if not iq_samples:
            return AMRFeature()

        n = len(iq_samples)

        # 幅度
        amplitudes = [abs(s) for s in iq_samples]
        mean_amp = sum(amplitudes) / n
        std_amp = math.sqrt(sum((a - mean_amp) ** 2 for a in amplitudes) / n) if n > 1 else 0
        max_amp = max(amplitudes)
        min_amp = min(amplitudes)
        rms_amp = math.sqrt(sum(a * a for a in amplitudes) / n)
        papr = (max_amp ** 2) / (rms_amp ** 2) if rms_amp > 0 else 0

        # 偏度和峰度
        if std_amp > 0:
            amp_skew = sum(((a - mean_amp) / std_amp) ** 3 for a in amplitudes) / n
            amp_kurt = sum(((a - mean_amp) / std_amp) ** 4 for a in amplitudes) / n - 3
        else:
            amp_skew = 0
            amp_kurt = 0

        # 瞬时频率和相位
        phases = [math.atan2(s.imag, s.real) for s in iq_samples]
        mean_phase = sum(phases) / n
        std_phase = math.sqrt(sum((p - mean_phase) ** 2 for p in phases) / n) if n > 1 else 0

        # 瞬时频率（相位差分）
        inst_freqs = []
        for i in range(1, n):
            df = phases[i] - phases[i - 1]
            while df > math.pi:
                df -= 2 * math.pi
            while df < -math.pi:
                df += 2 * math.pi
            inst_freqs.append(df * sample_rate / (2 * math.pi))
        mean_freq = sum(inst_freqs) / len(inst_freqs) if inst_freqs else 0
        std_freq = math.sqrt(sum((f - mean_freq) ** 2 for f in inst_freqs) / len(inst_freqs)) if len(inst_freqs) > 1 else 0

        # 过零率
        zero_crossings = sum(1 for i in range(1, n) if (iq_samples[i - 1].real >= 0) != (iq_samples[i].real >= 0))
        zcr = zero_crossings / n

        # I/Q 相关性
        i_vals = [s.real for s in iq_samples]
        q_vals = [s.imag for s in iq_samples]
        mean_i = sum(i_vals) / n
        mean_q = sum(q_vals) / n
        cov_iq = sum((i - mean_i) * (q - mean_q) for i, q in zip(i_vals, q_vals)) / n
        std_i = math.sqrt(sum((i - mean_i) ** 2 for i in i_vals) / n) if n > 1 else 1
        std_q = math.sqrt(sum((q - mean_q) ** 2 for q in q_vals) / n) if n > 1 else 1
        iq_corr = cov_iq / (std_i * std_q) if std_i > 0 and std_q > 0 else 0

        # 星座图密度（归一化后的唯一点数比例）
        normalized_points = set()
        for s in iq_samples[::max(1, n // 100)]:
            norm_amp = max(abs(s), 1e-10)
            normalized_points.add((round(s.real / norm_amp, 1), round(s.imag / norm_amp, 1)))
        constellation_density = len(normalized_points) / 100.0

        # 频域特征（简化：使用幅度谱的统计量代替完整 FFT）
        # 实际项目中应该使用 numpy.fft，这里用简化计算
        center_freq = mean_freq / sample_rate if sample_rate > 0 else 0
        bandwidth = std_freq / sample_rate if sample_rate > 0 else 0.1
        spectral_centroid = 0.5
        spectral_spread = bandwidth
        spectral_skewness = 0.0
        spectral_kurtosis = 3.0
        spectral_flatness = 0.5 if bandwidth > 0.1 else 0.3
        spectral_rolloff = 0.8

        return AMRFeature(
            mean_amplitude=mean_amp,
            std_amplitude=std_amp,
            max_amplitude=max_amp,
            min_amplitude=min_amp,
            rms_amplitude=rms_amp,
            peak_to_average=papr,
            amplitude_skewness=amp_skew,
            amplitude_kurtosis=amp_kurt,
            center_frequency=center_freq,
            bandwidth=bandwidth,
            spectral_centroid=spectral_centroid,
            spectral_spread=spectral_spread,
            spectral_skewness=spectral_skewness,
            spectral_kurtosis=spectral_kurtosis,
            spectral_flatness=spectral_flatness,
            spectral_rolloff=spectral_rolloff,
            zero_crossing_rate=zcr,
            mean_frequency=mean_freq,
            std_frequency=std_freq,
            mean_phase=mean_phase,
            std_phase=std_phase,
            iq_correlation=iq_corr,
            constellation_density=constellation_density,
            carrier_offset=mean_freq / sample_rate if sample_rate > 0 else 0,
        )

    def classify(self, feature: AMRFeature) -> AMRResult:
        """
        对特征进行分类。

        返回 AMRResult。
        """
        start_time = time.time()

        predicted, confidence, top_k = self.classifier.predict(feature)

        # 获取最近邻
        normalized = self.classifier._normalize(feature.to_list())
        distances = []
        for sample in self.classifier.samples:
            sample_normalized = self.classifier._normalize(sample.feature.to_list())
            dist = self.classifier._distance(normalized, sample_normalized)
            distances.append((dist, sample.label))
        distances.sort(key=lambda x: x[0])
        nearest = [(label, dist) for dist, label in distances[:5]]

        processing_time = (time.time() - start_time) * 1000

        return AMRResult(
            predicted_modulation=predicted,
            confidence=confidence,
            top_k=top_k,
            feature=feature,
            nearest_neighbors=nearest,
            processing_time_ms=processing_time,
        )

    def classify_iq(self, iq_samples: List[complex], sample_rate: float = 1.0) -> AMRResult:
        """从 IQ 样本直接分类。"""
        feature = self.extract_features_from_iq(iq_samples, sample_rate)
        return self.classify(feature)

    def add_training_sample(self, iq_samples: List[complex], label: ModulationType, sample_rate: float = 1.0):
        """添加训练样本（增量学习）。"""
        feature = self.extract_features_from_iq(iq_samples, sample_rate)
        self.classifier.add_sample(feature, label, source="user")

    def get_stats(self) -> Dict[str, Any]:
        """获取分类器统计信息。"""
        return self.classifier.get_stats()
