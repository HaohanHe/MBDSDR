# Tool-Use Cognitive Engine for AI-Defined Radio: Architecture, Heterogeneous Front-End Orchestration, and Safety-Constrained Self-Evolution

**Draft v0.1 — Submission target: IEEE Transactions on Cognitive Communications and Networking (TCCN)**
*Mode: DRAFT — Experimental results pending RTL-SDR and ai-sdr Mini hardware validation. All quantitative metrics in Section VI are expected targets or framework descriptions, not measured results.*

---

## Abstract

Software-defined radio (SDR) has matured into a flexible platform for spectrum sensing, signal demodulation, and protocol experimentation, yet its operation remains predominantly manual: users must explicitly configure center frequency, sample rate, demodulation mode, and decoding pipelines. Cognitive radio (CR) research introduced the observe–orient–decide–act (OODA) loop, but practical CR systems have been limited to narrow, preprogrammed policies rather than general-purpose reasoning. In this paper, we propose **AI-Defined Radio (ADR)**, a paradigm in which a large language model (LLM) serves as a general cognitive engine that perceives spectrum state, reasons about signal characteristics, and invokes SDR primitives as executable tools. We present the **Tool-Use Cognitive Engine (TUCE)**, an architecture that formalizes the cognition cycle as a tool-use problem: spectrum observation produces structured state, the LLM selects from a verifiable action space (Model Context Protocol tools), and each action is executed against a real SDR backend with deterministic feedback. We further describe **heterogeneous RF front-end orchestration**, a scheduling layer that coordinates complementary receivers (a broadcast-band SI4732 and a wideband IQ RTL2832U) under a unified abstraction, and **safety-constrained self-evolution**, a mechanism by which the LLM may propose and apply runtime modifications to its own processing pipeline while preserving a one-click rollback invariant that prevents model hallucination from bricking the device. We implement ADR as an open-source, cross-platform system (Linux, Windows, macOS, Android, HarmonyOS) with dual-device (phone–desktop) collaboration, and outline an experimental framework covering spectrum sensing, automatic modulation classification, frequency-offset correction, interference source localization, and digital-mode decoding (FT8, SSTV, ADS-B). This work contributes (i) a formal definition of AI-Defined Radio that distinguishes it from SDR and classical CR, (ii) a tool-use cognitive engine architecture with verifiable action semantics, (iii) a heterogeneous front-end orchestration layer, and (iv) a safety-constrained self-evolution mechanism with rollback guarantees.

**Index Terms**—AI-defined radio, cognitive radio, software-defined radio, large language models, tool use, model context protocol, heterogeneous RF front-end, self-evolution, spectrum sensing, safety constraints.

---

## I. Introduction

Software-defined radio (SDR) has transformed wireless experimentation by replacing fixed-function hardware with reconfigurable signal-processing pipelines [1], [2]. Modern SDR platforms such as GNU Radio [3], SDR++ [4], and SatDump [5] provide rich libraries of modulation, demodulation, filtering, and decoding blocks, enabling hobbyists and researchers to receive FM broadcast, aviation AM, ADS-B aircraft transponders, NOAA weather satellite imagery, and digital amateur modes such as FT8 and SSTV. Despite this flexibility, the operation of SDR systems remains fundamentally **manual and imperative**: a human operator must know which frequency to monitor, which demodulator to apply, what sample rate to select, and how to interpret the resulting spectrum. This manual burden limits SDR accessibility to trained operators and prevents autonomous, closed-loop adaptation to dynamic spectrum environments.

Cognitive radio (CR), introduced by Mitola and Maguire [6] and formalized by Haykin [7], proposed a solution: radios that can observe their electromagnetic environment, reason about spectrum occupancy, and adapt their transmission and reception parameters accordingly. The OODA loop—observe, orient, decide, act—became the canonical cognitive cycle. However, two decades of CR research have produced systems that are either (i) **narrow-policy**: preprogrammed decision rules for specific scenarios such as dynamic spectrum access in TV white spaces, or (ii) **machine-learning point solutions**: deep neural networks trained for a single task such as modulation classification [8] or spectrum sensing [9], with no general reasoning capability. Neither paradigm achieves the vision of a radio that can understand a user's high-level intent ("find the interference source," "decode whatever is on 14.074 MHz," "track this satellite") and autonomously configure the necessary SDR pipeline.

The emergence of large language models (LLMs) with tool-use capability [10]–[12] offers a new path. LLMs can perceive structured observations, reason about open-ended goals, and select from a set of executable actions (tools) to achieve those goals. Recent work on spectrum foundation models [13], [14] and AI-native physical layers [15] has demonstrated that deep learning can extract rich representations from RF signals. Yet no existing system integrates an LLM as a **general cognitive engine** that drives a full SDR stack—perceiving spectrum, reasoning about signals, invoking demodulators and decoders, and adapting runtime behavior—within a verifiable and safety-constrained framework.

This paper proposes **AI-Defined Radio (ADR)**, a paradigm that extends SDR and CR by placing an LLM-driven cognitive engine at the center of radio operation. In ADR, the human remains the authority (setting high-level goals and safety boundaries), but the LLM possesses autonomous regulation capability: it can detect frequency offsets and correct them, identify active frequencies and configure demodulators, recognize signal types and apply appropriate decoders, and even propose modifications to its own processing pipeline. Traditional SDR functionality is exposed as **tools** that the LLM invokes, rather than as fixed menu items that a human must navigate.

We make four contributions:

1. **Formal definition of AI-Defined Radio.** We distinguish ADR from SDR (reconfigurable but manually operated) and classical CR (preprogrammed or narrow-ML policies), and define ADR as a radio system in which an LLM serves as a general cognitive engine with a verifiable action space, human-in-the-loop authority, and autonomous regulation capability within safety boundaries.

2. **Tool-Use Cognitive Engine (TUCE) architecture.** We formalize the cognition cycle as a tool-use problem. Spectrum observation is converted to structured state (frequency–power histograms, demodulated audio transcripts, decoded packet fields), the LLM reasons over this state and selects actions from a Model Context Protocol (MCP) [16] tool registry, and each action is executed against a real SDR backend with deterministic, machine-readable feedback. This closes the OODA loop with general-purpose reasoning rather than preprogrammed rules.

3. **Heterogeneous RF front-end orchestration.** We describe a scheduling layer that coordinates complementary receivers with overlapping but non-identical capabilities—a broadcast-band SI4732 receiver (AM/FM/LW/SW with integrated demodulation) and a wideband IQ RTL2832U receiver (22 MHz–1.1 GHz, raw IQ samples)—under a unified abstraction. The orchestrator selects the optimal front-end for a given task based on frequency coverage, bandwidth, demodulation capability, and power consumption, and supports concurrent operation for dual-front-end diversity.

4. **Safety-constrained self-evolution with rollback.** We present a mechanism by which the LLM may propose and apply runtime modifications to its own processing pipeline—new decoding blocks, modified filter parameters, adaptive gain control—while preserving a **one-click rollback invariant**. Every self-modification is logged, sandboxed, and reversible; a deterministic recovery partition ensures that even catastrophic model hallucination cannot permanently brick the device. This addresses a critical concern in LLM-driven autonomous systems: the risk of self-modification leading to unrecoverable failure.

We implement ADR as **MBDSDR** (Model-Based Defined SDR), an open-source, cross-platform system supporting Linux (Ubuntu, Debian, Arch, Kali, Deepin, UOS), Windows (x86_64 and ARM64/WOA), macOS, Android, and HarmonyOS, with dual-device phone–desktop collaboration. The hardware reference design, **ai-sdr Mini**, integrates an ESP32-S3 microcontroller, SI4732 broadcast receiver, BMI260 IMU, TMAG5273 magnetometer, ATGM336H GNSS module, and a USB 2.0 hub for connecting external RTL-SDR dongles, in a compact, battery-powered form factor suitable for field operation.

The remainder of this paper is organized as follows. Section II reviews related work in SDR, cognitive radio, LLM tool use, and spectrum AI. Section III presents the formal definition and system model of ADR. Section IV describes the TUCE architecture in detail. Section V presents heterogeneous front-end orchestration and safety-constrained self-evolution. Section VI outlines the experimental framework and expected results (draft: pending hardware validation). Section VII discusses limitations, ethical considerations, and future work. Section VIII concludes.

---

## II. Related Work

### A. Software-Defined Radio

SDR replaces analog and digital hardware components with software running on general-purpose processors or FPGAs [1], [2]. The GNU Radio project [3] provides a block-based signal-processing framework with a rich library of modulation, demodulation, filtering, and coding blocks, and has become the de facto standard for SDR research and experimentation. SDR++ [4] offers a lightweight, high-performance SDR receiver with a modular plugin architecture, supporting a wide range of hardware front-ends. SatDump [5] specializes in satellite image processing, supporting NOAA APT, Meteor-M LRPT, and other satellite downlink standards. These systems provide powerful SDR functionality but require manual configuration by a knowledgeable operator.

Commercial SDR hardware ranges from low-cost RTL-SDR dongles (RTL2832U + R820T/FC0012 tuners, ~$10–20) [17] to high-performance USRP devices (Ettus/National Instruments, $1000+) [18]. The RTL-SDR ecosystem, while limited to 8-bit IQ, 2.4–3.2 MSps maximum sample rate, and receive-only operation, has democratized SDR experimentation and enabled a vast body of hobbyist and academic work. The SI4732 [19] is an integrated broadcast receiver supporting AM, FM, LW, and SW bands with built-in demodulation, offering a low-power, low-cost complement to wideband IQ SDRs.

### B. Cognitive Radio

Cognitive radio was proposed by Mitola and Maguire [6] as a radio that can model its environment and adjust its own behavior, with the ultimate goal of "making software radios more personal." Haykin [7] formalized cognitive radio as "brain-empowered wireless communications" and identified the OODA loop as the core cognitive cycle. Subsequent research explored dynamic spectrum access [20], spectrum sensing [21], and adaptive modulation and coding [22]. However, practical CR systems have been limited to preprogrammed decision rules or narrow machine-learning models. A survey by Zhang et al. [23] notes that "most existing CR architectures rely on hand-crafted policies that do not generalize across scenarios," and identifies "general-purpose reasoning" as an open challenge.

More recent work has applied deep learning to specific CR tasks: modulation classification [8], spectrum sensing [9], [24], and signal detection [25]. These approaches achieve high accuracy on their target tasks but lack the general reasoning capability to handle open-ended goals or to compose multiple SDR primitives into a coherent pipeline. The spectrum foundation model (SpectrumFM) [13], [14] represents a step toward general RF representation learning, using self-supervised pre-training on diverse spectrum data, but it does not integrate with an executable SDR action space.

### C. Large Language Models and Tool Use

LLMs have demonstrated remarkable capability in natural language understanding, reasoning, and code generation [26]. The introduction of function calling / tool use [10] enabled LLMs to invoke external APIs and executables, extending their capability beyond text generation. The ReAct framework [11] interleaves reasoning traces with task-specific actions, achieving strong performance on question-answering and decision-making tasks. Reflexion [12] introduces verbal reinforcement learning, where an agent reflects on its failures and improves its subsequent attempts. Voyager [27] demonstrates an open-ended embodied agent that writes, executes, and refines code in a Minecraft environment, with a skill library that accumulates over time.

The Model Context Protocol (MCP) [16] provides an open standard for connecting LLMs to external tools, data sources, and workflows, enabling interoperable tool-use across different LLM providers and applications. Hermes Agent [28] and OpenCode [29] are examples of self-evolving code agents that can modify their own source code and accumulate capabilities over time, though they operate in general software environments rather than SDR-specific domains.

Despite this rich ecosystem, no existing work applies LLM tool-use as a general cognitive engine for SDR. The closest efforts are LLM-based network management systems [30] and AI-assisted signal analysis tools [31], but these do not close the OODA loop with executable SDR actions, nor do they address the safety constraints required for runtime self-modification of radio hardware.

### D. Cross-Platform and Mobile SDR

Mobile SDR applications such as SDR Touch [32] and RF Analyzer [33] bring basic SDR functionality to Android devices via USB OTG, but they are limited to manual operation and do not support AI-driven cognition or cross-device collaboration. The Stellarium project [34] provides open-source planetarium software with satellite tracking capabilities, offering a model for sky-map visualization that can be integrated with SDR satellite reception. Cross-platform SDR frameworks that support both desktop and mobile operation, with unified UI and shared processing pipelines, remain an open engineering challenge.

---

## III. AI-Defined Radio: Formal Definition and System Model

### A. Definition

We define **AI-Defined Radio (ADR)** as a radio system with the following properties:

1. **LLM-driven cognitive engine.** An LLM serves as the central reasoning component, perceiving spectrum state, interpreting user intent, and selecting actions from a verifiable action space.

2. **Tool-based action space.** All SDR functionality—frequency tuning, gain control, demodulation, decoding, recording, spectrum analysis—is exposed as executable tools with formally specified input/output schemas, rather than as fixed menu items.

3. **Human-in-the-loop authority.** The human operator retains ultimate authority: high-level goals, safety boundaries, and resource limits are set by the human, and the LLM operates within these constraints. The LLM may propose actions that require human confirmation (e.g., transmission, high-power operation, self-modification), but routine perception and adaptation are autonomous.

4. **Autonomous regulation capability.** Within safety boundaries, the LLM can autonomously: detect and correct frequency offsets; identify active frequencies and configure appropriate demodulators; recognize signal types and apply decoders; track moving signal sources (e.g., satellites); and adapt processing parameters to changing conditions.

5. **Closed-loop cognition.** The system implements a complete observe–orient–decide–act cycle, where the consequences of each action are observed and fed back into subsequent reasoning, enabling iterative improvement without human intervention.

6. **Safety-constrained self-evolution.** The LLM may propose and apply runtime modifications to its own processing pipeline (new decoding blocks, modified parameters, adaptive algorithms), but every modification is logged, sandboxed, and reversible, with a deterministic rollback mechanism that prevents unrecoverable failure.

This definition distinguishes ADR from:
- **SDR**: reconfigurable hardware/software, but manually operated with no general reasoning capability.
- **Classical CR**: OODA loop with preprogrammed policies or narrow ML models, but no general-purpose LLM reasoning and no tool-based open action space.
- **AI-assisted SDR**: ML models for specific tasks (modulation classification, spectrum sensing), but no closed-loop cognition and no autonomous pipeline configuration.

### B. System Model

We model an ADR system as a tuple $\mathcal{A} = (\mathcal{S}, \mathcal{O}, \mathcal{T}, \mathcal{L}, \mathcal{E}, \mathcal{H}, \mathcal{C})$, where:

- $\mathcal{S}$ is the **spectrum state space**, representing all observable electromagnetic environment variables: frequency–power distributions, signal-to-noise ratios, demodulated audio streams, decoded packet fields, receiver metadata (temperature, gain, sample rate), and geospatial context (GPS position, IMU orientation, time).

- $\mathcal{O}$ is the **observation function** $\mathcal{O}: \mathcal{H} \to \mathcal{S}$, which maps raw hardware measurements to structured, LLM-readable state. The observation function includes preprocessing steps such as FFT computation, peak detection, demodulation, and transcription, producing structured outputs (JSON, tabular data, short text summaries) rather than raw IQ samples.

- $\mathcal{T}$ is the **tool registry**, a finite set of executable actions $\{t_1, t_2, \ldots, t_N\}$, each with a formally specified input schema (JSON Schema), output schema, side-effect profile (read-only vs. state-modifying vs. hardware-modifying), and safety level (autonomous vs. human-confirm). Tools are registered via the Model Context Protocol [16], enabling interoperability across LLM providers.

- $\mathcal{L}$ is the **LLM cognitive engine**, a function $\mathcal{L}: (\mathcal{S}, \mathcal{G}, \mathcal{H}_t) \to t^*$, where $\mathcal{G}$ is the current goal (human-provided or derived), $\mathcal{H}_t$ is the conversation/action history, and $t^* \in \mathcal{T} \cup \{\text{wait}, \text{ask\_human}\}$ is the selected action. The LLM may also produce natural language reasoning traces and explanations.

- $\mathcal{E}$ is the **execution environment**, which invokes the selected tool $t^*$ against the SDR backend, collects the result, and feeds it back as a new observation. The execution environment enforces safety constraints: tools with safety level "human-confirm" are queued for human approval; tools that modify hardware state are logged; all tool outputs are validated against their output schemas.

- $\mathcal{H}$ is the **hardware abstraction layer**, which provides a unified interface to heterogeneous RF front-ends (SI4732, RTL2832U, USRP, etc.), abstracting over differences in frequency coverage, bandwidth, sample format, and control interfaces.

- $\mathcal{C}$ is the **safety constraint set**, a set of invariants that must hold at all times: maximum transmit power (if transmission is supported), frequency band restrictions (regulatory compliance), hardware state rollback capability, and resource limits (CPU, memory, storage). The execution environment rejects any action that would violate $\mathcal{C}$.

The cognition cycle proceeds as follows:

1. **Observe**: $\mathcal{O}(\mathcal{H})$ produces structured spectrum state $s_t \in \mathcal{S}$.
2. **Orient**: The LLM $\mathcal{L}$ integrates $s_t$ with the current goal $\mathcal{G}$ and history $\mathcal{H}_t$, forming an understanding of the current situation.
3. **Decide**: $\mathcal{L}$ selects an action $t^* \in \mathcal{T} \cup \{\text{wait}, \text{ask\_human}\}$.
4. **Act**: $\mathcal{E}$ executes $t^*$ against $\mathcal{H}$, producing a result $r_t$.
5. **Feedback**: $r_t$ is incorporated into $\mathcal{H}_{t+1}$, and the cycle repeats.

This cycle continues until the goal $\mathcal{G}$ is achieved (as judged by the LLM and verified by the execution environment), the human intervenes, or a safety constraint is violated.

### C. Dual-Device Collaboration Model

ADR supports a **phone–desktop collaboration model** inspired by the dual-device navigation paradigm (phone + car head unit), where each device has complementary capabilities:

- **Phone (mobile device)**: serves as a portable spectrum sensor and pointing device. It connects to SDR hardware via USB OTG or Wi-Fi, provides real-time spectrum visualization, captures IMU/GNSS orientation for direction-finding, and can execute lightweight demodulation and decoding tasks. The phone also receives **guidance instructions** from the desktop LLM: e.g., "point the antenna at azimuth 135°," "switch to the Yagi antenna," "move 5 meters north."

- **Desktop (computer)**: serves as the heavy computation node, running the LLM cognitive engine, performing complex signal processing (wideband FFT, advanced demodulation, AI-based modulation classification), managing the tool registry and self-evolution pipeline, and providing a comprehensive UI for spectrum analysis and pipeline configuration.

The two devices communicate via a local network (Wi-Fi direct, Bluetooth, or USB tethering) using a lightweight protocol that transmits structured spectrum state, tool invocations, and guidance instructions. This collaboration model enables field operation where the phone provides portability and orientation sensing, while the desktop provides computational power and AI reasoning.

---

## IV. Tool-Use Cognitive Engine (TUCE) Architecture

### A. Overview

The Tool-Use Cognitive Engine (TUCE) is the core of the ADR system. It implements the cognition cycle described in Section III-B as a tool-use problem, where the LLM's action space is the set of registered SDR tools. TUCE is designed with three principles:

1. **Verifiable action semantics.** Every tool has a formally specified input/output schema, and every tool execution produces a structured, machine-readable result. This enables the LLM to reason about the consequences of its actions and the execution environment to validate correctness.

2. **Deterministic feedback.** Tool results are deterministic given the same input and hardware state (within the limits of RF signal variability). This allows the LLM to learn from experience and the system to detect anomalies (e.g., a tool returning an unexpected result may indicate hardware failure or interference).

3. **Safety stratification.** Tools are classified into three safety levels:
   - **Read-only (Level 0)**: tools that observe spectrum state without modifying hardware configuration (e.g., `get_spectrum`, `get_signal_info`). These may be invoked autonomously at any time.
   - **State-modifying (Level 1)**: tools that change receiver configuration (e.g., `set_frequency`, `set_gain`, `start_demodulation`). These may be invoked autonomously within safety constraints, but all invocations are logged.
   - **Hardware-modifying / self-evolution (Level 2)**: tools that modify persistent system state (e.g., `install_decoder`, `modify_pipeline`, `update_firmware`). These require human confirmation before execution, and every invocation creates a rollback checkpoint.

### B. Tool Registry and MCP Integration

The tool registry is implemented using the Model Context Protocol (MCP) [16], an open standard for connecting LLMs to external tools. Each SDR tool is registered as an MCP tool with:

- A unique name (e.g., `sdr.tune`, `sdr.demodulate.fm`, `sdr.decode.ft8`).
- A JSON Schema input specification (e.g., `{"frequency": {"type": "number", "unit": "Hz"}, "gain": {"type": "number", "unit": "dB"}}`).
- A JSON Schema output specification.
- A safety level (0, 1, or 2).
- A human-readable description that explains what the tool does, when to use it, and what results to expect.

The MCP server runs as a local process on the desktop (or on the phone for standalone operation), exposing the tool registry to any MCP-compatible LLM client. This enables interoperability: the same ADR system can be driven by different LLMs (open-source models via Ollama, cloud models via API, or embedded models on the phone), and the tool registry can be extended by third-party developers without modifying the core system.

The initial tool registry includes the following tool categories:

**Spectrum observation tools (Level 0):**
- `sdr.scan(start_freq, end_freq, resolution)`: Perform a frequency sweep and return a frequency–power histogram.
- `sdr.spectrum(center_freq, bandwidth, fft_size)`: Return a real-time FFT spectrum for the specified band.
- `sdr.peaks(threshold)`: Identify signal peaks above a threshold in the current spectrum.
- `sdr.audio(transcript)`: Return demodulated audio, optionally transcribed to text.
- `sdr.packets(protocol)`: Return decoded packets for the specified protocol.

**Receiver configuration tools (Level 1):**
- `sdr.tune(frequency)`: Set the center frequency.
- `sdr.gain(value)`: Set the receiver gain (auto or manual).
- `sdr.sample_rate(rate)`: Set the sample rate.
- `sdr.demodulate(mode, options)`: Start demodulation with the specified mode (FM, AM, SSB, CW, WFM, etc.).
- `sdr.decode(protocol, options)`: Start decoding with the specified protocol (FT8, SSTV, ADS-B, POCSAG, DMR, etc.).
- `sdr.record(format, duration, path)`: Record baseband data in the specified format (WAV, CSV, raw IQ, etc.).

**Analysis tools (Level 0/1):**
- `sdr.identify_signal(frequency)`: Attempt to identify the signal type at the specified frequency using AI-based modulation classification.
- `sdr.correct_frequency_offset()`: Detect and correct frequency offset using known reference signals (e.g., FM pilot tone, ADS-B preamble).
- `sdr.find_interference(band)`: Scan the specified band and identify potential interference sources.
- `sdr.track_satellite(norad_id)`: Track a satellite by NORAD ID, automatically adjusting frequency for Doppler shift and antenna pointing.

**Self-evolution tools (Level 2):**
- `sdr.install_decoder(name, source)`: Install a new decoder from a source repository (requires human confirmation).
- `sdr.modify_pipeline(patch)`: Apply a patch to the current processing pipeline (requires human confirmation, creates rollback checkpoint).
- `sdr.rollback(checkpoint_id)`: Roll back to a previous pipeline state.
- `sdr.list_checkpoints()`: List available rollback checkpoints.

**System tools (Level 0/1):**
- `system.status()`: Return system status (CPU, memory, storage, hardware connected, LLM model info).
- `system.list_models()`: List available LLM models and their capabilities.
- `system.context_length()`: Return the current context window usage and limit.
- `system.set_context_length(length)`: Set the context window size (for models that support it).
- `system.compress_context(strategy)`: Compress the conversation history using the specified strategy (summary, key-point extraction, sliding window).

### C. Observation Function and State Structuring

A critical design challenge is converting raw SDR data (IQ samples, audio streams, spectrum arrays) into a structured, LLM-readable format that fits within the context window. The observation function $\mathcal{O}$ implements several strategies:

1. **Spectrum compression.** Raw FFT spectra (e.g., 4096 bins) are compressed into a structured representation: a list of detected peaks (frequency, power, bandwidth, estimated modulation type), a coarse histogram (e.g., 64 bins), and statistical summaries (mean power, noise floor, occupancy ratio). This reduces a 4096-bin spectrum to a few hundred tokens.

2. **Audio transcription.** Demodulated audio is transcribed to text using a speech-to-text model (e.g., Whisper [35] or a lightweight on-device model), producing a compact text representation that the LLM can reason about directly. For non-speech audio (e.g., SSTV tones, FSK data), the observation function extracts features (tone frequencies, baud rate, modulation type) rather than transcribing.

3. **Packet decoding.** Digital signals are decoded to structured packet fields (e.g., ADS-B: aircraft ID, latitude, longitude, altitude, heading; FT8: callsign, grid square, signal report). These structured fields are compact and directly interpretable by the LLM.

4. **Multimodal observation.** For LLM providers that support image input (e.g., multimodal models), the observation function can render the spectrum waterfall as an image and pass it to the LLM alongside the structured data. This enables the LLM to perceive visual patterns (e.g., frequency-hopping signals, wideband interference) that may be difficult to capture in structured data. For text-only LLMs, the observation function relies entirely on structured data.

5. **Context management.** The conversation/action history can grow rapidly during long-running cognition cycles. The system implements several context management strategies inspired by Kilo Code [36], MIMO Code [37], and Hermes Agent [28]:
   - **Sliding window**: Keep only the most recent N turns.
   - **Summarization**: Periodically summarize older turns into a compact abstract.
   - **Key-point extraction**: Extract and retain only decision-relevant information (goal state, key observations, action outcomes).
   - **Configurable context length**: The user can set the maximum context window size, and the system reports current usage via `system.context_length()`.

### D. LLM Integration and Model Agnosticism

TUCE is designed to be **model-agnostic**: any LLM that supports tool-use (function calling) can serve as the cognitive engine. The system supports:

- **Cloud models** via API (e.g., OpenAI GPT series, Anthropic Claude, Google Gemini, SiliconFlow Qwen series).
- **Open-source local models** via Ollama or llama.cpp (e.g., Llama, Qwen, Mistral, Gemma).
- **Embedded models** on mobile devices (e.g., Qualcomm AI Engine, Apple Neural Engine, MediaTek APU) for offline operation.

The user can configure the model via `system.list_models()` and a model selection interface, and the system provides a **model fallback** mechanism: if the primary model is unavailable or produces invalid tool calls, the system automatically falls back to a secondary model. This is particularly important for low-cost or embedded models that may have weaker tool-use capability.

Following the user's design principle of "test with weak models first," the system is validated against small, low-cost models (e.g., Qwen3.6-35B-A3B, a 35B-parameter MoE model with 3B active parameters) to ensure that the tool-use system works even with models that have limited reasoning capability. If the system works reliably with a weak model, it will work even better with stronger models.

### E. Tool Call Validation and Error Recovery

A common failure mode in LLM tool-use is **invalid tool calls**: the LLM may produce malformed JSON, reference a non-existent tool, or pass invalid parameters. TUCE implements a robust validation and recovery pipeline:

1. **Schema validation.** Every tool call is validated against the tool's JSON Schema input specification before execution. Invalid calls are rejected with a structured error message that explains the validation failure and suggests corrections.

2. **Tool existence check.** The system verifies that the referenced tool exists in the registry. If not, it returns a list of available tools with descriptions, enabling the LLM to correct its selection.

3. **Parameter coercion.** For minor parameter errors (e.g., a string that can be parsed as a number, a frequency in MHz instead of Hz), the system attempts to coerce the parameter to the correct type, logging the coercion for transparency.

4. **Retry with feedback.** After a validation failure, the system returns the error to the LLM as a new observation, enabling it to correct its tool call. The system allows up to 3 retries per action before escalating to the human.

5. **Execution monitoring.** During tool execution, the system monitors for timeouts, exceptions, and unexpected results. If a tool fails to execute (e.g., hardware disconnected, buffer overflow), the system returns a structured error and the LLM can adapt (e.g., switch to a different front-end, reduce sample rate).

6. **Anomaly detection.** The system maintains a statistical model of expected tool results (e.g., typical spectrum occupancy, typical noise floor). If a tool returns a result that deviates significantly from expectations (e.g., a completely empty spectrum when a signal is known to be present, or a saturated spectrum indicating gain too high), the system flags it as an anomaly and suggests corrective actions (e.g., "check antenna connection," "reduce gain").

---

## V. Heterogeneous Front-End Orchestration and Safety-Constrained Self-Evolution

### A. Heterogeneous RF Front-End Orchestration

A key observation is that no single SDR front-end is optimal for all tasks. The RTL2832U [17] provides wideband IQ coverage (22 MHz–1.1 GHz) but has limited dynamic range (8-bit), no built-in demodulation, and relatively high power consumption. The SI4732 [19] provides excellent broadcast-band reception (AM/FM/LW/SW) with built-in demodulation and audio output, low power consumption, and high sensitivity, but it does not provide raw IQ samples and is limited to broadcast bands. By orchestrating these complementary front-ends under a unified abstraction, ADR can select the optimal receiver for each task and even operate both concurrently for diversity.

The **front-end orchestrator** is a middleware layer that sits between the tool registry and the hardware abstraction layer. It maintains a registry of available front-ends, each described by a capability profile:

```
FrontEnd {
  id: string,
  type: "iq_wideband" | "broadcast_integrated" | "usrp" | "hackrf" | ...,
  frequency_range: [min_hz, max_hz],
  max_bandwidth: hz,
  sample_formats: ["iq8", "iq16", "audio", "demodulated"],
  demodulation_modes: ["FM", "AM", "SSB", "CW", ...],
  decoding_protocols: ["FT8", "SSTV", "ADS-B", ...],
  power_consumption: mW,
  connection: "usb" | "spi" | "i2c" | "wifi",
  status: "available" | "busy" | "error"
}
```

When a tool requires RF front-end access (e.g., `sdr.tune`, `sdr.demodulate`, `sdr.spectrum`), the orchestrator selects the optimal front-end based on:

1. **Frequency coverage**: The front-end must support the target frequency.
2. **Bandwidth requirement**: Wideband spectrum scans require a wideband IQ front-end; narrowband demodulation can use an integrated broadcast receiver.
3. **Demodulation capability**: For FM/AM/SSB demodulation, the SI4732's built-in demodulator is more efficient and lower-power than software demodulation on the RTL-SDR.
4. **Sample format requirement**: AI-based modulation classification and advanced signal processing require raw IQ samples, which only the RTL-SDR provides.
5. **Power consumption**: On battery-powered mobile operation, the orchestrator prefers the lower-power SI4732 for tasks where it is sufficient.
6. **Availability**: If one front-end is busy (e.g., recording baseband), the orchestrator may use the other for a concurrent task.

The orchestrator supports **concurrent operation**: both front-ends can operate simultaneously, enabling use cases such as:
- Wideband spectrum monitoring on the RTL-SDR while demodulating a specific FM station on the SI4732.
- Cross-front-end verification: the SI4732 demodulates a signal while the RTL-SDR captures raw IQ for offline AI analysis.
- Frequency diversity: both front-ends monitor the same frequency with different antenna configurations, improving reception reliability.

The ai-sdr Mini hardware reference design integrates both front-ends: the SI4732 is connected via I2C to the ESP32-S3, and the RTL-SDR dongle connects via the on-board USB 2.0 hub (USB2514B [38]). The ESP32-S3 [39] serves as a bridge controller, managing the SI4732, reading the IMU (BMI260 [40]), magnetometer (TMAG5273 [41]), and GNSS (ATGM336H [42]), and providing Wi-Fi/Bluetooth connectivity for phone–desktop collaboration. The USB hub also enables connecting additional USB devices (e.g., a second RTL-SDR, a USB drive for recording, or a 4G modem for remote operation).

### B. Direction-Finding and Antenna Pointing

The integration of IMU (BMI260, 6-axis accelerometer + gyroscope), magnetometer (TMAG5273, 3-axis), and GNSS (ATGM336H, GPS + BeiDou + GLONASS) enables **direction-finding and antenna pointing** capabilities that are unique to ADR:

1. **Compass-based pointing**: The magnetometer provides absolute heading (azimuth), enabling the system to determine the direction the antenna is pointing. When combined with GNSS position, the system can calculate the bearing to a known signal source (e.g., a satellite, a transmitter at a known location) and guide the user to point the antenna correctly.

2. **IMU-aided tracking**: The accelerometer and gyroscope provide high-rate orientation tracking, enabling the system to compensate for antenna movement during signal reception (e.g., stabilizing the frequency tracking during satellite passes).

3. **Interference source localization**: By taking signal strength measurements at multiple positions (using GNSS for position and magnetometer for heading), the system can triangulate the location of an interference source. The LLM can guide the user through the measurement process ("move 10 meters north and measure again," "point the antenna at azimuth 90° and record signal strength").

4. **AR overlay**: The phone's camera can be used to overlay signal direction information on the real-world view (augmented reality), showing the user where signals are coming from. This is inspired by the sky-map visualization in Stellarium [34], but applied to RF signals rather than celestial objects.

The ai-sdr Mini is designed to be mountable on antennas (Yagi, dish, whip) using standard camera tripod mounts or cable ties, with the IMU/magnetometer providing orientation relative to the antenna boresight. This enables portable, field-deployable direction-finding without specialized equipment.

### C. Safety-Constrained Self-Evolution

A defining feature of ADR is the ability of the LLM to **propose and apply runtime modifications to its own processing pipeline**. This self-evolution capability enables the system to adapt to new signal types, optimize processing parameters, and accumulate capabilities over time—similar to how Voyager [27] builds a skill library in Minecraft, or how Hermes Agent [28] and OpenCode [29] modify their own source code. However, self-modification in a radio system carries unique risks: a bad modification could disable reception, corrupt recorded data, or even cause hardware damage (if transmission is supported). Therefore, ADR implements **safety-constrained self-evolution** with the following mechanisms:

1. **Rollback checkpointing.** Before any self-modification (Level 2 tool invocation), the system creates a **checkpoint**: a complete snapshot of the current processing pipeline configuration, installed decoders, model parameters, and system state. Checkpoints are stored in a dedicated recovery partition, and the system maintains a configurable number of historical checkpoints (default: 10).

2. **One-click rollback.** The `sdr.rollback(checkpoint_id)` tool restores the system to any previous checkpoint, completely undoing all modifications made after that checkpoint. Rollback is deterministic and atomic: either the entire checkpoint is restored, or the system remains in its current state (no partial rollback). The rollback mechanism is implemented at the filesystem level (using overlay filesystems or snapshotting), ensuring that even if a self-modification corrupts the main system files, the recovery partition remains intact.

3. **Sandboxed execution.** Self-modifications are first applied in a **sandbox environment**—an isolated copy of the processing pipeline that runs alongside the main system. The LLM can test its proposed modification in the sandbox, observe the results, and only then apply it to the main system (with human confirmation). If the sandbox test fails (e.g., the modified decoder crashes, the pipeline produces garbage output), the modification is discarded without affecting the main system.

4. **Human confirmation for Level 2 actions.** All self-modification tools require explicit human confirmation before execution. The LLM proposes a modification with a justification ("I propose installing a new DMR decoder because the current decoder cannot handle this signal type"), and the human can approve, reject, or request modifications. This ensures that the human retains ultimate authority over system changes.

5. **Modification logging and audit trail.** Every self-modification is logged with: timestamp, LLM model and version, proposed change, justification, sandbox test results, human decision (approve/reject), and execution outcome. This audit trail enables post-hoc analysis of the system's evolution and supports debugging if a modification causes problems.

6. **Contribution and community review.** Since ADR is open-source, self-modifications that prove useful can be contributed back to the main repository via pull requests. A community review process (inspired by the "creative workshop" / Steam Workshop model) ensures that contributed modifications are reviewed for quality, safety, and regulatory compliance before being merged. This creates a flywheel: users discover new signal types, the LLM proposes decoders, the community reviews and improves them, and the entire ecosystem benefits.

7. **Regulatory compliance guardrails.** The system enforces regulatory constraints on self-modification: it will not install or enable transmission capabilities without proper licensing verification, it will not modify processing to operate on restricted frequency bands, and it will log all transmission-related modifications for audit. This ensures that self-evolution does not lead to regulatory violations.

### D. Baseband Recording and Data Export

ADR supports comprehensive baseband recording capabilities, enabling users to capture RF signals for offline analysis, AI model training, or sharing with the community. The recording system supports:

- **Multiple formats**: WAV (audio), CSV (spectrum data), raw IQ (8-bit, 16-bit, 32-bit float), and compressed formats (FLAC for audio, ZSTD for raw IQ).
- **Configurable granularity**: users can select sample rate, bit depth, channel count, and recording duration.
- **Triggered recording**: the LLM can start recording automatically when a specific signal type is detected (e.g., "record when an FT8 signal is detected on 14.074 MHz").
- **Scheduled recording**: users can schedule recordings for specific time windows (e.g., "record the NOAA 19 satellite pass at 14:30 UTC").
- **Streaming export**: recorded data can be streamed to a remote server or cloud storage for backup or analysis.
- **Metadata tagging**: every recording is tagged with metadata: frequency, sample rate, gain, timestamp, GNSS position, IMU orientation, signal type (if identified), and LLM observations. This metadata is essential for AI model training and reproducible research.

The recording system also supports **VVVF (Variable-Voltage Variable-Frequency) inverter acoustic analysis**, a specialized application where an electromagnetic pickup coil (connected to the SDR's audio input or via a dedicated front-end) captures the acoustic emissions of a VVVF inverter (commonly used in electric trains), and the system analyzes the frequency spectrum to reverse-engineer the inverter's control program. This is an experimental feature that demonstrates the generality of the ADR tool-use framework: the same cognitive engine that decodes ADS-B can be applied to VVVF analysis by registering appropriate analysis tools.

---

## VI. Experimental Evaluation (Draft — Pending Hardware Validation)

*Note: This section describes the experimental framework and expected results. Actual measurements are pending RTL-SDR and ai-sdr Mini hardware validation. All quantitative values below are expected targets based on literature and preliminary analysis, not measured results.*

### A. Experimental Setup

The experimental evaluation uses the following hardware:

- **RTL-SDR dongle** (RTL2832U + FC0012 tuner): 22 MHz–1.1 GHz, 8-bit IQ, max 2.4 MSps, USB 2.0. Two units are used for dual-front-end experiments.
- **ai-sdr Mini** (reference design): ESP32-S3 + SI4732 + BMI260 + TMAG5273 + ATGM336H + USB2514B hub, battery-powered, mountable on antennas.
- **Desktop computer**: x86_64 Linux (Ubuntu 22.04), 16 GB RAM, for LLM inference and heavy signal processing.
- **Mobile phone**: Android 13, USB OTG, for portable spectrum sensing and direction-finding.
- **Signal generator**: laboratory-grade RF signal generator (school facility) for controlled experiments.
- **Spectrum analyzer**: laboratory-grade spectrum analyzer (school facility) for ground-truth measurements.

LLM models used for evaluation:
- **Primary**: Qwen3.6-35B-A3B (35B MoE, 3B active) via SiliconFlow API — a "weak" model used to validate that the tool-use system works with limited reasoning capability.
- **Secondary**: GPT-4o / Claude 3.5 Sonnet — stronger models for comparison.
- **Local**: Llama 3.1 8B via Ollama — for offline operation testing.

### B. Experiment 1: Spectrum Sensing and Automatic Frequency Identification

**Objective**: Evaluate the LLM's ability to scan a frequency band, identify active signals, and configure appropriate demodulators without human intervention.

**Method**:
1. The human provides the goal: "Scan 88–108 MHz and identify all active FM stations."
2. The LLM invokes `sdr.scan(88e6, 108e6, 100e3)` to perform a frequency sweep.
3. The LLM analyzes the returned frequency–power histogram, identifies peaks above the noise floor, and invokes `sdr.tune(frequency)` + `sdr.demodulate("FM")` for each peak.
4. The LLM invokes `sdr.audio(transcript=true)` to verify that each station is broadcasting audio (not noise or interference).
5. The LLM reports the list of identified stations with frequency, signal strength, and audio content summary.

**Metrics**:
- **Detection accuracy**: fraction of actual FM stations detected (compared to ground truth from spectrum analyzer).
- **False positive rate**: fraction of detected "stations" that are actually noise or interference.
- **Time to complete**: total time from goal provision to final report.
- **Tool call efficiency**: number of tool calls required (fewer = more efficient reasoning).

**Expected results**: Detection accuracy > 95% for strong stations (> −60 dBm), > 80% for weak stations (−60 to −80 dBm); false positive rate < 5%; time to complete < 60 seconds for a 20 MHz scan.

### C. Experiment 2: Automatic Modulation Classification

**Objective**: Evaluate the LLM's ability to identify the modulation type of unknown signals using AI-based classification tools.

**Method**:
1. The signal generator produces known signals (FM, AM, SSB, FSK, PSK, QPSK, QAM16) at various frequencies and SNR levels.
2. The human provides the goal: "Identify the modulation type of the signal at [frequency]."
3. The LLM invokes `sdr.identify_signal(frequency)`, which uses a pre-trained CNN (based on [8]) on raw IQ samples to classify the modulation.
4. The LLM may also invoke `sdr.spectrum()` and `sdr.audio()` to gather additional evidence.
5. The LLM reports the identified modulation type with confidence.

**Metrics**:
- **Classification accuracy**: fraction of signals correctly classified, by modulation type and SNR level.
- **Confidence calibration**: correlation between reported confidence and actual accuracy.
- **Comparison to baseline**: comparison with standalone CNN classification (without LLM reasoning) to evaluate whether LLM evidence integration improves accuracy.

**Expected results**: Classification accuracy > 90% at SNR > 10 dB for all modulation types; LLM-augmented classification improves accuracy by 5–10% over standalone CNN at low SNR (< 5 dB) due to evidence integration from spectrum and audio observations.

### D. Experiment 3: Frequency-Offset Correction

**Objective**: Evaluate the LLM's ability to detect and correct frequency offset in the RTL-SDR (which lacks a TCXO and may have tens of ppm offset).

**Method**:
1. The signal generator produces a known unmodulated carrier at a precise frequency (e.g., 100.000 MHz).
2. The LLM invokes `sdr.spectrum()` and observes that the peak is at 100.003 MHz (3 kHz offset due to 30 ppm crystal error).
3. The LLM invokes `sdr.correct_frequency_offset()`, which uses the known carrier frequency to estimate and apply a correction.
4. The LLM verifies the correction by observing that the peak is now at 100.000 MHz.
5. The LLM applies the correction to subsequent demodulation tasks.

**Metrics**:
- **Offset detection accuracy**: difference between estimated and actual offset.
- **Residual offset after correction**: remaining offset after correction.
- **Time to correct**: time from first observation to corrected demodulation.
- **Impact on digital decoding**: improvement in FT8/ADS-B decoding success rate after offset correction.

**Expected results**: Offset detection accuracy < 100 Hz; residual offset < 50 Hz; time to correct < 10 seconds; FT8 decoding success rate improves from < 50% (without correction) to > 90% (with correction) for signals with > 5 ppm offset.

### E. Experiment 4: Digital Mode Decoding (FT8, SSTV, ADS-B)

**Objective**: Evaluate the LLM's ability to recognize and decode common digital amateur and aviation modes.

**Method**:
1. **FT8**: The LLM is given the goal: "Decode FT8 signals on 14.074 MHz USB." The LLM configures the receiver (tune to 14.074 MHz, set USB demodulation, set 2.7 kHz bandwidth), invokes `sdr.decode("FT8")`, and reports decoded messages (callsigns, grid squares, signal reports).
2. **SSTV**: The LLM is given the goal: "Receive the SSTV transmission on 14.230 MHz." The LLM configures the receiver, invokes `sdr.decode("SSTV")`, and reports the received image.
3. **ADS-B**: The LLM is given the goal: "Track aircraft on 1090 MHz." The LLM configures the receiver (tune to 1090 MHz, set 2 MSps sample rate), invokes `sdr.decode("ADS-B")`, and reports aircraft positions, altitudes, and callsigns.

**Metrics**:
- **Decode success rate**: fraction of transmissions successfully decoded.
- **Time to first decode**: time from goal provision to first decoded message.
- **Pipeline configuration correctness**: whether the LLM configures the correct frequency, demodulation mode, bandwidth, and sample rate without human correction.
- **Comparison to manual**: comparison with a human operator configuring the same pipeline (time and correctness).

**Expected results**: FT8 decode success rate > 80% (comparable to WSJT-X [43]); SSTV image reception > 70% of transmitted images; ADS-B aircraft tracking > 95% of aircraft within range; LLM configures pipeline correctly on first attempt > 90% of the time; time to first decode < 30 seconds (compared to > 60 seconds for a human operator).

### F. Experiment 5: Interference Source Localization

**Objective**: Evaluate the LLM's ability to guide a user through interference source localization using direction-finding and triangulation.

**Method**:
1. A known interference source (signal generator) is placed at an unknown location.
2. The human provides the goal: "Find the interference source around 433 MHz."
3. The LLM invokes `sdr.scan()` to identify the interference frequency.
4. The LLM guides the user to take signal strength measurements at multiple positions: "Move to position A and measure," "Point the Yagi antenna at azimuth 0° and record signal strength," "Rotate 30° and measure again."
5. The LLM uses the measurements (with GNSS position and magnetometer heading) to triangulate the interference source location.
6. The LLM reports the estimated location and guides the user to verify.

**Metrics**:
- **Localization accuracy**: distance between estimated and actual source location.
- **Number of measurements required**: fewer = more efficient.
- **Guidance clarity**: whether the user can follow the LLM's instructions without confusion (subjective rating).
- **Time to localize**: total time from goal to estimated location.

**Expected results**: Localization accuracy < 5 meters in an open field with > 5 measurements; time to localize < 10 minutes; guidance clarity rating > 4/5.

### G. Experiment 6: Heterogeneous Front-End Orchestration

**Objective**: Evaluate the front-end orchestrator's ability to select the optimal front-end for each task and operate both concurrently.

**Method**:
1. **Single-task selection**: For each task type (FM demodulation, wideband spectrum scan, raw IQ capture, FT8 decoding), measure which front-end the orchestrator selects and compare performance (demodulation quality, power consumption, latency).
2. **Concurrent operation**: Run a wideband spectrum scan on the RTL-SDR while simultaneously demodulating an FM station on the SI4732. Measure whether both tasks complete without interference.
3. **Failover**: Disconnect the RTL-SDR mid-task and measure whether the orchestrator fails over to the SI4732 (for tasks it can handle) or reports the task as unavailable (for tasks requiring raw IQ).

**Metrics**:
- **Selection correctness**: fraction of tasks where the orchestrator selects the optimal front-end (as judged by a human expert).
- **Concurrent throughput**: performance of each task during concurrent operation vs. standalone.
- **Failover time**: time from hardware disconnection to system adaptation.
- **Power savings**: reduction in power consumption when using the SI4732 instead of the RTL-SDR for broadcast-band tasks.

**Expected results**: Selection correctness > 95%; concurrent operation with < 10% performance degradation for either task; failover time < 3 seconds; power savings > 50% for broadcast-band tasks using SI4732 vs. RTL-SDR.

### H. Experiment 7: Safety-Constrained Self-Evolution

**Objective**: Evaluate the self-evolution mechanism's ability to propose, test, and apply pipeline modifications while maintaining safety guarantees.

**Method**:
1. **Proposed modification**: The LLM encounters a signal type that the current decoder cannot handle (e.g., a novel digital mode). The LLM proposes installing a new decoder (simulated by a known decoder not yet installed).
2. **Sandbox test**: The system applies the modification in the sandbox and tests it against known signals.
3. **Human confirmation**: The human reviews the proposal and sandbox results and approves.
4. **Application**: The modification is applied to the main system.
5. **Rollback test**: After application, the system rolls back to the previous checkpoint and verifies that the system is restored to its pre-modification state.
6. **Failure injection**: A deliberately broken modification is proposed; the system should detect the failure in the sandbox and discard it without affecting the main system.

**Metrics**:
- **Modification success rate**: fraction of valid modifications successfully applied.
- **Sandbox detection rate**: fraction of broken modifications detected in the sandbox (not applied to main system).
- **Rollback correctness**: whether rollback fully restores the pre-modification state (verified by checksum comparison).
- **Rollback time**: time to complete rollback.
- **Human confirmation overhead**: time spent by the human reviewing and confirming modifications.

**Expected results**: Modification success rate > 95% for valid modifications; sandbox detection rate > 99% for broken modifications; rollback correctness = 100% (checksum match); rollback time < 5 seconds; human confirmation time < 30 seconds per modification.

### I. Experiment 8: Cross-Platform and Dual-Device Operation

**Objective**: Evaluate the system's operation across supported platforms and in phone–desktop collaboration mode.

**Method**:
1. **Platform compatibility**: Install and run the system on each supported platform (Ubuntu, Windows x86_64, Windows ARM64/WOA, macOS, Android, HarmonyOS, Deepin, Arch Linux, Kali). Verify that core functionality (spectrum sensing, demodulation, tool use) works on each platform.
2. **Dual-device collaboration**: Connect the phone (Android) and desktop (Linux) via Wi-Fi direct. Run a satellite tracking task where the phone provides IMU/GNSS orientation and the desktop runs the LLM and heavy signal processing. Measure latency and correctness.
3. **UI rendering**: Verify that the UI renders correctly on each platform, including fallback rendering when OpenGL is unavailable (e.g., Windows on ARM64 with limited GPU support).

**Metrics**:
- **Platform coverage**: fraction of supported platforms where core functionality works.
- **Collaboration latency**: time from phone sensor reading to desktop LLM action.
- **UI consistency**: subjective rating of UI consistency across platforms.
- **Fallback correctness**: whether the software renderer produces correct output when OpenGL is unavailable.

**Expected results**: Core functionality works on all listed platforms; collaboration latency < 500 ms on local Wi-Fi; UI consistency rating > 4/5; software renderer fallback produces correct output with < 30% frame rate reduction.

---

## VII. Discussion

### A. Limitations

1. **LLM reliability and hallucination.** LLMs can produce invalid tool calls, incorrect reasoning, or hallucinated facts. TUCE addresses this through schema validation, retry with feedback, sandboxed self-modification, and human confirmation for Level 2 actions. However, for autonomous operation (Level 0 and 1 tools), the LLM may still make suboptimal decisions (e.g., selecting a suboptimal demodulation mode, failing to detect a weak signal). Future work should explore confidence-calibrated tool use, where the LLM reports its confidence in each action and the system escalates to the human when confidence is low.

2. **Context window limitations.** Long-running cognition cycles can exceed the LLM's context window, leading to forgotten goals or repeated actions. The system implements context management strategies (sliding window, summarization, key-point extraction), but these may lose important information. The `system.context_length()` tool and configurable context length help the user monitor and manage this, but optimal context management remains an open research problem. The system is designed to work with models of varying context sizes (from 4K to 128K+ tokens), and the observation function's compression strategies are calibrated to the available context.

3. **Hardware limitations of low-cost SDR.** The RTL-SDR and SI4732 are low-cost, low-performance devices. The 8-bit ADC of the RTL-SDR limits dynamic range (~40–50 dB), the lack of TCXO causes frequency offset, and the maximum sample rate (2.4 MSps) limits bandwidth. These limitations constrain the types of experiments that can be performed (e.g., high-bandwidth signals like 5G NR cannot be received). However, the front-end orchestrator's abstraction enables upgrading to higher-performance SDRs (USRP, HackRF, BladeRF) without changing the cognitive engine. The ai-sdr Mini is designed as a reference platform for experimentation, not as a high-performance measurement instrument.

4. **Regulatory and legal considerations.** SDR systems can potentially be used to intercept communications or transmit on restricted frequencies, which may be illegal in many jurisdictions. ADR includes regulatory guardrails (no transmission without licensing verification, frequency band restrictions, audit logging), but these are software-enforced and can be bypassed by a determined user. The open-source nature of the project means that users can modify the guardrails. The project includes clear documentation of legal responsibilities and encourages users to comply with local regulations. This is a social and legal issue that cannot be fully solved by technology alone.

5. **Power consumption on mobile devices.** Running an LLM (even a small one) and SDR processing on a mobile phone can drain the battery quickly. The dual-device collaboration model addresses this by offloading heavy computation to the desktop, but standalone phone operation is limited to short sessions. Future work should explore on-device AI accelerators (Qualcomm AI Engine, Apple Neural Engine) and model quantization to reduce power consumption.

### B. Ethical Considerations

1. **Privacy.** SDR systems can receive a wide range of radio signals, including private communications (mobile phones, walkie-talkies, cordless phones). ADR's recording and decoding capabilities could be used for surveillance. The project includes privacy guardrails (no recording of encrypted communications, metadata tagging for accountability, user education about legal restrictions), but the open-source nature means these can be bypassed. The project community should establish clear ethical guidelines and potentially implement technical measures (e.g., watermarking recorded data, restricting certain decoders to licensed users).

2. **Accessibility and democratization.** A primary goal of ADR is to democratize SDR by making it accessible to non-experts through natural language interaction. This has the potential to expand the SDR community beyond trained radio operators and engineers, enabling new applications in education, citizen science, and emergency communications. However, it also lowers the barrier to potentially harmful uses (interference, surveillance). The project should invest in user education and community governance to maximize benefits while minimizing harms.

3. **Open-source and community governance.** ADR is fully open-source, which enables transparency, community contribution, and independent audit of safety mechanisms. The self-evolution contribution model (community review of proposed modifications) creates a distributed governance structure. However, open-source projects can be vulnerable to malicious contributions (e.g., a decoder that contains hidden surveillance functionality). The project should implement code review, security auditing, and a trusted maintainer model to mitigate this risk.

### C. Future Work

1. **Transmission capability.** The current ADR system is receive-only. Adding transmission capability (e.g., via a HackRF or PlutoSDR) would enable closed-loop cognitive transmission: the LLM could sense the spectrum, decide on transmission parameters (frequency, power, modulation), and transmit, then observe the result. This would bring the system closer to the original cognitive radio vision [6], [7]. Transmission requires careful regulatory compliance and safety constraints (power limits, frequency restrictions, emergency channel protection).

2. **Distributed ADR networks.** Multiple ADR devices could form a distributed spectrum sensing network, collaborating to monitor wide geographic areas, localize interference sources through multilateration, and share spectrum intelligence. This could be applied to citizen science (monitoring spectrum usage), emergency communications (detecting and locating distress signals), and regulatory enforcement (identifying unauthorized transmitters).

3. **On-device foundation models.** Current ADR relies on cloud or desktop LLMs for reasoning. Future work could explore fine-tuning small, specialized models (1–7B parameters) for SDR-specific tasks, enabling fully offline operation on mobile devices. These models could be trained on the spectrum datasets collected by the ADR community, creating a flywheel where more users generate more data, which improves the model, which attracts more users.

4. **Integration with satellite communication.** The direction-finding and satellite tracking capabilities could be extended to support satellite communication (e.g., QO-100 geostationary amateur radio satellite, Iridium, Starlink). The LLM could automatically track satellites, adjust for Doppler shift, configure appropriate modems, and even schedule recording sessions during satellite passes. The Stellarium-inspired sky-map visualization [34] could be integrated to show satellite positions and antenna pointing directions in real time.

5. **VVVF inverter analysis and industrial applications.** The VVVF acoustic analysis feature (Section V-D) demonstrates the generality of the ADR framework. Future work could explore other industrial and scientific applications: power line communication analysis, electromagnetic compatibility (EMC) testing, radio astronomy (with appropriate front-ends), and biomedical signal analysis (with appropriate sensors). The tool-use architecture means that any application that can be expressed as "observe spectrum → reason → invoke tools" can be supported by ADR.

6. **Formal verification of safety constraints.** The current safety constraints are enforced at the software level (input validation, checkpointing, sandboxing). Future work could explore formal verification of the safety constraint set, using theorem provers or model checkers to mathematically prove that the system cannot enter an unsafe state (e.g., cannot transmit above regulatory power limits, cannot corrupt the recovery partition). This would provide stronger safety guarantees for critical applications.

---

## VIII. Conclusion

This paper proposed AI-Defined Radio (ADR), a paradigm that extends software-defined radio and cognitive radio by placing an LLM-driven cognitive engine at the center of radio operation. We presented the Tool-Use Cognitive Engine (TUCE), which formalizes the cognition cycle as a tool-use problem with verifiable action semantics, deterministic feedback, and safety-stratified tools. We described heterogeneous RF front-end orchestration, which coordinates complementary receivers (SI4732 and RTL2832U) under a unified abstraction, and safety-constrained self-evolution, which enables the LLM to propose and apply runtime modifications while preserving a one-click rollback invariant. We implemented ADR as MBDSDR, an open-source, cross-platform system with dual-device phone–desktop collaboration, and outlined an experimental framework covering spectrum sensing, modulation classification, frequency-offset correction, digital mode decoding, interference localization, front-end orchestration, self-evolution safety, and cross-platform operation.

ADR represents a step toward the original cognitive radio vision of "brain-empowered wireless communications" [7], but with a fundamentally different approach: rather than preprogrammed policies or narrow machine-learning models, ADR uses general-purpose LLM reasoning with a verifiable tool-use action space. This enables open-ended adaptation to new signal types, new frequency bands, and new applications, without requiring explicit reprogramming for each scenario. The open-source nature of the project, combined with the community contribution model for self-evolution, creates a flywheel for continuous improvement: as more users deploy ADR and contribute modifications, the system's capabilities grow, the spectrum dataset expands, and the cognitive engine becomes more capable.

We believe that AI-Defined Radio has the potential to democratize radio technology, making sophisticated spectrum analysis and signal processing accessible to non-experts through natural language interaction, while maintaining the safety, reliability, and regulatory compliance required for real-world deployment. The reference hardware design (ai-sdr Mini), the open-source software stack (MBDSDR), and the experimental framework provide a foundation for researchers, hobbyists, and industry to build upon and extend.

---

## References

[1] J. Mitola III, "The software radio architecture," *IEEE Communications Magazine*, vol. 33, no. 5, pp. 26–38, May 1995.

[2] F. K. Jondral, "Software-defined radio: basics and evolution to cognitive radio," *EURASIP Journal on Wireless Communications and Networking*, vol. 2005, no. 3, pp. 1–11, 2005.

[3] GNU Radio Project, "GNU Radio — Free & Open Source Software Radio Ecosystem," 2024. [Online]. Available: https://www.gnuradio.org/

[4] A. Pachler, "SDR++ — The Modern, Cross-Platform SDR Software," 2024. [Online]. Available: https://www.sdrpp.org/

[5] SatDump Team, "SatDump — A generic satellite data processing software," 2024. [Online]. Available: https://github.com/SatDump/SatDump

[6] J. Mitola III and G. Q. Maguire Jr., "Cognitive radio: making software radios more personal," *IEEE Personal Communications*, vol. 6, no. 4, pp. 13–18, Aug. 1999.

[7] S. Haykin, "Cognitive radio: brain-empowered wireless communications," *IEEE Journal on Selected Areas in Communications*, vol. 23, no. 2, pp. 201–220, Feb. 2005.

[8] T. J. O'Shea and J. Hoydis, "An introduction to deep learning-based physical layer," *IEEE Transactions on Cognitive Communications and Networking*, vol. 3, no. 4, pp. 563–575, Dec. 2017.

[9] W. Lee, M. Kim, and D. Cho, "Deep sensing for future spectrum sharing and IoT: A deep learning approach," in *Proc. IEEE Int. Conf. Commun. (ICC)*, 2018, pp. 1–6.

[10] OpenAI, "Function calling and other API updates," OpenAI Blog, Jun. 2023. [Online]. Available: https://openai.com/blog/function-calling-and-other-api-updates

[11] S. Yao et al., "ReAct: Synergizing reasoning and acting in language models," in *Proc. Int. Conf. Learn. Representations (ICLR)*, 2023.

[12] N. Shinn et al., "Reflexion: Language agents with verbal reinforcement learning," in *Proc. Adv. Neural Inf. Process. Syst. (NeurIPS)*, 2023.

[13] X. Zhang et al., "SpectrumFM: A foundation model for intelligent spectrum management," *arXiv preprint arXiv:2505.06256*, 2025.

[14] X. Zhang et al., "SpectrumFM: Redefining spectrum cognition via foundation modeling," *arXiv preprint arXiv:2508.02742*, 2025.

[15] M. A. Alim et al., "AI-native PHY-layer in 6G orchestrated spectrum-aware networks," *PMC*, 2026. [Online]. Available: https://pmc.ncbi.nlm.nih.gov/articles/PMC12694481/

[16] Anthropic, "Model Context Protocol," 2024. [Online]. Available: https://modelcontextprotocol.io/

[17] Realtek Semiconductor, "RTL2832U — DVB-T/FM/DAB USB dongle IC," datasheet, 2013.

[18] Ettus Research, "USRP (Universal Software Radio Peripheral)," 2024. [Online]. Available: https://www.ettus.com/

[19] Silicon Laboratories, "Si4732/33/34/35 — AM/FM/SW/LW radio receiver IC," datasheet, Rev. 1.0, 2018.

[20] I. F. Akyildiz, W.-Y. Lee, M. C. Vuran, and S. Mohanty, "NeXt generation/dynamic spectrum access/cognitive radio wireless networks: A survey," *Computer Networks*, vol. 50, no. 13, pp. 2127–2159, 2006.

[21] T. Yucek and H. Arslan, "A survey of spectrum sensing algorithms for cognitive radio applications," *IEEE Communications Surveys & Tutorials*, vol. 11, no. 1, pp. 116–130, 2009.

[22] A. Goldsmith, S. A. Jafar, I. Maric, and S. Srinivasa, "Breaking spectrum gridlock with cognitive radios: An information theoretic perspective," *Proceedings of the IEEE*, vol. 97, no. 5, pp. 894–914, 2009.

[23] L. Zhang, Y.-C. Liang, and M. Xiao, "Cognitive radio networking: A contemporary survey," *IEEE Wireless Communications*, vol. 30, no. 2, pp. 112–119, Apr. 2023.

[24] S. Wang et al., "Deep learning-based wideband spectrum sensing with dual-representation inputs and subband shuffling augmentation," *arXiv preprint arXiv:2504.07427*, 2025.

[25] A. K. Jagannatham et al., "Edge-efficient transformer for end-to-end RF spectrum monitoring," *arXiv preprint arXiv:2607.18285*, 2026.

[26] T. Brown et al., "Language models are few-shot learners," in *Proc. Adv. Neural Inf. Process. Syst. (NeurIPS)*, 2020, pp. 1877–1901.

[27] G. Wang et al., "Voyager: An open-ended embodied agent with large language models," *arXiv preprint arXiv:2305.16291*, 2023.

[28] Nous Research, "Hermes Agent — Self-evolving code agent," 2024. [Online]. Available: https://github.com/NousResearch/Hermes-Function-Calling

[29] OpenCode Team, "OpenCode — Open source coding agent," 2024. [Online]. Available: https://github.com/opencode-ai/opencode

[30] M. A. Raza et al., "LLM-based network management: A survey," *IEEE Communications Surveys & Tutorials*, 2024.

[31] K. T. Trinh et al., "AI-assisted signal analysis for software-defined radio," in *Proc. IEEE Int. Symp. Dyn. Spectr. Access Netw. (DySPAN)*, 2023.

[32] SDR Touch, "SDR Touch — Live radio via USB SDR receiver," Google Play, 2024.

[33] RF Analyzer, "RF Analyzer — Analyze radio frequencies on Android," Google Play, 2024.

[34] Stellarium Team, "Stellarium — Free open-source planetarium," 2024. [Online]. Available: https://stellarium.org/

[35] A. Radford et al., "Robust speech recognition via large-scale weak supervision," in *Proc. Int. Conf. Mach. Learn. (ICML)*, 2023, pp. 28492–28518.

[36] Kilo Code Team, "Kilo Code — Context management for coding agents," 2024. [Online]. Available: https://github.com/khulnasoft/kilo-code

[37] MIMO Code Team, "MIMO Code — Multi-input multi-output context for code agents," 2024. [Online]. Available: https://github.com/mimo-code/mimo-code

[38] Microchip Technology, "USB2514B — USB 2.0 Hi-Speed 4-Port Hub Controller," datasheet, 2014.

[39] Espressif Systems, "ESP32-S3 — 2.4 GHz Wi-Fi and Bluetooth 5 (LE) SoC with AI acceleration," datasheet, 2023.

[40] Bosch Sensortec, "BMI260 — 6-axis inertial measurement unit," datasheet, 2022.

[41] Texas Instruments, "TMAG5273 — High-accuracy 3D Hall-effect sensor with I2C interface," datasheet, 2023.

[42] ATGM336H, "ATGM336H — GNSS module supporting GPS/BDS/GLONASS," datasheet, 2022.

[43] J. Taylor, "WSJT-X — Software for VHF/UHF/microwave communication including FT8," 2024. [Online]. Available: https://www.physics.princeton.edu/pulsar/k1jt/

---

*End of Draft v0.1. This document is a working draft. Experimental results in Section VI are framework descriptions and expected targets, pending hardware validation. All claims should be verified against measured data before submission.*
