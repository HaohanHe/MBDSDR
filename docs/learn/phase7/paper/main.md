---
title: "MBDSDR: An AI-Defined Software-Defined Radio Stack with Deterministic, Reproducible Experimentation"
venue: "IEEE Wireless Communications Letters (WCL) — submission draft"
status: DRAFT v0.2 (2026-10-02)
authors: "Bi4MIB (open-source, call for co-authors)"
---

# MBDSDR: An AI-Defined Software-Defined Radio Stack with Deterministic, Reproducible Experimentation

> **Reproducibility & honesty note.** Every number in this paper is produced by a
> fixed-seed simulation pipeline under `experiments/` and stored as CSV + Wilson
> 95% confidence intervals. All results in this draft are **`synthetic`**
> (script-generated signals + fixed-seed AWGN). No over-the-air (OTA) or recorded
> hardware results are claimed. The online-LLM decision-quality column is reported
> as `PENDING_ONLINE_RUN` (no API key in this environment) and is **not**
> fabricated. Figures and CSVs carry their data-origin, sample count *N*, and UTC
> generation date in the filename (e.g. `...__synthetic__N3600__20261002.png`).

---

## Abstract

We present **MBDSDR**, a full-stack AI-defined software-defined radio (SDR)
reference platform that couples a C++ real-time receive chain, a Flutter mobile
client, a Python signal-processing kernel, and an OpenAI-compatible
*function-calling* tool layer that exposes every radio operation as an agent-
callable tool. This letter focuses on the **reproducible experimental backbone**:
a fixed-seed Monte-Carlo runner with Wilson-score 95% confidence intervals, an
Eb/N0↔in-band-SNR calibration convention, and per-run manifests locking script,
seed, data-origin, parameters, sample count, git SHA and UTC timestamp. We report
ten synthetic experiments (≈11 800 trials) covering decode success vs. Eb/N0,
vs. sample rate, and vs. receiver bandwidth; the measured benefit of Doppler
compensation; automatic modulation recognition (AMR) against a rule-based
baseline; single-station Doppler orbit determination; and the local deterministic
cost/reliability of the agent tool-call pipeline. At fixed Eb/N0, BPSK packet
success is invariant to sample rate (≈0.60 across 10 kS/s–1 MS/s); residual
Doppler of only ≈10 Hz collapses uncompensated decoding to 6.5% while known-
frequency restoration holds ≈0.95; and KNN-AMR reaches 0.96–1.00 accuracy at
≥10 dB where hand-tuned rules reach 0.67–1.00. The online-LLM decision layer
remains a clearly-marked pending item. All code and experiment artifacts are open.

**Index Terms**—Software-defined radio, AI-defined radio, function calling,
automatic modulation recognition, Doppler compensation, reproducible experiments.

---

## I. Introduction

Software-defined radio (SDR) has moved modulation, filtering and decoding into
software, but mainstream SDR tooling (GNU Radio, SDR++, SatDump) still requires
manual, expert-driven configuration: pick device → set center frequency → set
sample rate → select demod → tune gain → record. Large language models (LLMs)
with tool-calling now make it plausible to drive a radio by natural language, yet
prior AI-SDR work largely wraps existing GUI software rather than exposing a
deterministic, reproducible measurement backbone.

Two problems motivate this work. First, when "AI runs the radio," the measured
numbers (decode success, recognition accuracy, latency) must themselves be
reproducible and honestly labeled — a result that mixes synthetic signals with
un-flagged online-LLM calls is not auditable. Second, the signal-processing
choices an agent makes (sample rate, receiver bandwidth, Doppler correction) have
consequences that should be quantified, not assumed.

**Contributions.** (i) A layered MBDSDR stack whose SDR operations are registered
as OpenAI function-calling tools; (ii) a fixed-seed Monte-Carlo experiment library
(`experiments/common/`) with Wilson 95% CIs, an explicit Eb/N0 calibration, and
per-run manifests; (iii) ten synthetic experiments with origin/N/date labels;
and (iv) an honest separation between the *deterministic local tool pipeline*
(measured) and the *online-LLM decision layer* (reported as pending).

---

## II. System Design

MBDSDR is organized as four cooperating layers (Tab. I).

* **C++ real-time chain** (`cpp/`, CMake, ctest): the low-latency front-end —
  DC blocking, I/Q correction, resampling, demodulation, SigMF recording
  (`recorder.cpp`) with `core:datetime`, and playback. It produces standard
  `.sigmf-data` (cf32) + `.sigmf-meta`.
* **Flutter mobile client** (`mobile/`, package `mbdsdr.app`): USB/OBD control of
  the companion hardware, on-satellite pass prediction, recording library,
  activity log, AGC toggle.
* **Python kernel** (`mbdsdr_ai/`, 211 modules): modems and decoders reused
  *unchanged* by the experiments — AX.25/AFSK Bell-202, ADS-B Mode-S, a
  self-contained coherent BPSK chain, AMR (25-dim features + KNN), orbit
  determination (SGP4 + EKF / reference-epoch RLS), GNSS NMEA parsing.
* **Function-calling tool layer** (`mbdsdr_ai/tool_registry.py`,
  `sdr_tools.py`): every radio operation is a tool with a JSON schema. The
  registry implements four pipeline stages — **register → resolve (name/alias
  normalization) → validate (required-parameter check) → execute (handler)** —
  with closed-failure handling and measured per-call latency.
* **New space-time** (`new_spacetime*.py`): TLE/pass prediction, sky view, and
  GNSS-timestamp alignment of recordings.

The experiment library (`experiments/common/`) provides: `runner` (master seed →
per-cell sub-streams via BLAKE2b hashing; binomial success + Wilson interval),
`ebno` (exact `Eb/N0 = SNR_inband + 10·log10(B/Rb)`), `manifest` (script/seed/
origin/params/*N*/UTC/git-SHA), `plot` (headless Agg; every figure title and
filename carries `[origin, N=..., date]`), and `datasource` (synthetic channel,
SigMF replay, and an explicit *empty state* when no recording/OTA is available).

**TABLE I — MBDSDR layers (representative components).**

| Layer | Language / location | Role | Used in experiments |
|---|---|---|---|
| Real-time chain | C++ (`cpp/`) | front-end, SigMF record/playback | replay source (recorded, empty here) |
| Mobile client | Flutter (`mobile/`) | hardware control, passes, recording UI | — |
| Signal kernel | Python (`mbdsdr_ai/`) | AX.25, ADS-B, BPSK, AMR, orbit-det, GNSS | decoders/AMR/orbit |
| Tool layer | Python (`tool_registry.py`) | function-calling register/resolve/validate/execute | agent-toolcall |
| New space-time | Python (`new_spacetime*.py`) | TLE passes, sky view, time alignment | Doppler/orbit |

---

## III. Experiments

All experiments are **`synthetic`**, fixed-seed (master seed 20261001), and run
on a headless CI VM with no radio hardware. Sample counts and dates are in each
figure/manifest. CIs are Wilson 95%.

### A. Decode success vs. Eb/N0 (Fig. 1, N=2400)

We drive three real decoders — AX.25/AFSK Bell-202, ADS-B Mode-S (CRC-24 gated),
and coherent BPSK — over an Eb/N0 grid and gate a trial on a valid frame/CRC or
zero bit errors. Eb/N0 is obtained from in-band SNR via `B=fs`: Δ=+15.6 dB
(AX.25), +6.0 dB (ADS-B), +10.0 dB (BPSK). BPSK crosses 0.5 at ≈6 dB and reaches
1.0 at ≥10 dB; ADS-B needs ≈12–14 dB; AX.25 (largest B/Rb penalty) needs ≈26 dB.
This is a real power–bit-rate conversion, not a curve-placement choice.

**TABLE II — Representative operating points (Fig. 1; synthetic).**

| Mode | Eb/N0 (dB) | Success | Wilson 95% CI |
|---|---|---|---|
| BPSK 10k | 6 | 0.60 | [0.462, 0.724] |
| BPSK 10k | 8 | 0.92 | [0.812, 0.969] |
| ADS-B 1M | 10 | 0.48 | [0.348, 0.615] |
| ADS-B 1M | 12 | 0.86 | [0.738, 0.931] |
| AX.25 1200 | 24 | 0.70 | [0.563, 0.809] |
| AX.25 1200 | 26 | 0.98 | [0.895, 0.997] |

### B. Decode success vs. sample rate (Fig. 2, N=1400)

At **fixed Rb=10 kbit/s and fixed Eb/N0=6 dB**, we sweep fs (and hence
samples-per-symbol sps = fs/Rb) from 10 kS/s (sps=1) to 1 MS/s (sps=100). The
in-band SNR is re-derived per fs so that Eb/N0 stays constant. The measured
success rate is 0.595–0.705 across the whole range with overlapping Wilson bands
(Tab. III). This confirms that, after Eb/N0 normalization, oversampling does not
buy extra packet success for an ideal integrate-and-dump receiver — the design
takeaway is to choose the lowest fs that satisfies Nyquist, with no energy penalty.

**TABLE III — Sample-rate sweep at fixed Eb/N0=6 dB (Fig. 2; synthetic).**

| fs (kS/s) | sps | In-band SNR (dB) | Success | Wilson 95% CI |
|---|---|---|---|---|
| 10 | 1 | 6.00 | 0.595 | [0.526, 0.660] |
| 50 | 5 | −0.99 | 0.610 | [0.541, 0.675] |
| 100 | 10 | −4.00 | 0.615 | [0.546, 0.680] |
| 200 | 20 | −7.01 | 0.630 | [0.561, 0.695] |
| 500 | 50 | −10.99 | 0.705 | [0.638, 0.765] |
| 1000 | 100 | −14.00 | 0.660 | [0.591, 0.723] |

### C. Decode success vs. receiver bandwidth (Fig. 3, N=900)

With fixed Rb=10 kbit/s and fixed noise PSD, we sweep the receiver low-pass
cutoff B_rx. Below the rectangular BPSK null-to-null bandwidth (≈2·Rb=20 kHz)
the main lobe is clipped → ISI; B_rx=8 kHz gives 0.71, 12 kHz 0.77. At/above
20 kHz success saturates near 0.78–0.88. This bounds the required front-end
bandwidth for a fixed bit rate.

### D. Doppler compensation benefit (Fig. 4, N=3600)

We superimpose a constant Doppler offset fd on a 20 ms BPSK packet at fixed
Eb/N0=8 dB and compare a receiver with **no correction** against one that applies
the orbit-predicted correction exp(−j2π·fd·t). Because a 20 ms packet accumulates
phase 2π·fd·T, a residual of only fd≈10 Hz (≈72° over the packet) drops
uncompensated success to 0.065; by fd≥15 Hz it is 0. With known-frequency
correction the success stays 0.94–0.98 across the whole fd grid — recovering the
baseline. This is the ideal (known-fd) upper bound on the compensation loop.

**TABLE IV — Doppler compensation (Fig. 4; fixed Eb/N0=8 dB, synthetic).**

| Residual fd (Hz) | Uncompensated success | Compensated success |
|---|---|---|
| 0 | 0.960 | 0.970 |
| 5 | 0.880 | 0.980 |
| 10 | 0.065 | 0.940 |
| 15 | 0.000 | 0.970 |
| ≥25 | 0.000 | 0.955–0.975 |

### E. Automatic modulation recognition (Figs. 5–7, N=600 / N=300 / N=640)

On a fixed-seed synthetic dataset of FSK/PSK/NOISE, a hand-tuned rule classifier
(instantaneous-frequency standard-deviation thresholds) is compared against the
real KNN-AMR (25 features, k=5) on the **same samples**. At low in-band SNR the
rule baseline is weak (0.33 at 0 dB, 0.67 at 10 dB) while KNN-AMR is 0.54 and
0.96; both saturate by 15–20 dB (Tab. V). The 8-class confusion matrix at SNR=10 dB
(Fig. 7) has diagonal accuracy 0.9938 [0.984, 0.998]; the only residual confusion
is FSK→QAM (4/80). The online-LLM classification column (Fig. 6) is
`PENDING_ONLINE_RUN`: classic/KNN are computed locally, the LLM cell is left
empty rather than fabricated.

**TABLE V — AMR accuracy vs. in-band SNR (Fig. 5; synthetic, N=120/SNR point).**

| SNR (dB) | Classic rules | KNN-AMR | LLM column |
|---|---|---|---|
| 0 | 0.333 | 0.542 | PENDING_ONLINE_RUN |
| 5 | 0.508 | 0.633 | PENDING_ONLINE_RUN |
| 10 | 0.667 | 0.958 | PENDING_ONLINE_RUN |
| 15 | 1.000 | 0.992 | PENDING_ONLINE_RUN |
| 20 | 1.000 | 1.000 | PENDING_ONLINE_RUN |

### F. Single-station Doppler orbit determination (Figs. 8–9, N=155 / N=6)

Using a fixed public ISS TLE propagated through SGP4, we add fixed-seed Gaussian
noise to the Doppler-rates observed from a single Changchun station and run an EKF
and a reference-epoch RLS. At Doppler-noise σ=1 Hz the RLS reference-epoch error
floor is ≈1.1 km; error degrades as σ grows. Sweeping the observation window
around peak elevation (Fig. 9) shows the expected single-station observability
limit: short windows (60 s) give ≈5.8 km, and only a near-full-pass window
(600 s) drops to ≈4.0 km.

### G. Agent tool-call pipeline (Fig. 10, N=400)

We register five real local tools (Morse encode, haversine distance, APRS frame
encode, RMC parse, plus a short-name alias) and drive the registry with a
deterministic request suite that mixes correct calls, unknown tool names, missing
required parameters, and alias arguments. Across 400 trials the local pipeline
handles **both correct and erroneous requests at 1.000** (Wilson lower bound
≈0.93–0.98 per cell): correct calls execute, bad calls return explicit
`tool_not_found` / `missing_required_params` errors without crashing. Registration
of five tools costs ≈0.016 ms; per-call overhead is sub-millisecond. The *LLM
decision* metrics (whether the model picks the right tool and fills args) are
`PENDING_ONLINE_RUN` (Tab. VI).

**TABLE VI — Agent tool-call local-pipeline stages (Fig. 10; synthetic, N=400).**

| Stage | n | Graceful-handling rate |
|---|---|---|
| Register (5 tools) | 5 | 1.000 |
| Name resolution (correct) | 250 | 1.000 |
| Name resolution (unknown → graceful) | 50 | 1.000 |
| Parameter validation (pass) | 250 | 1.000 |
| Parameter validation (missing → graceful) | 100 | 1.000 |
| Execution (correct path) | 250 | 1.000 |
| Execution (bad path graceful) | 150 | 1.000 |
| *LLM decision (call/pick/arg)* | — | *PENDING_ONLINE_RUN* |

---

## IV. Discussion & Limitations

* **Synthetic-only.** All numbers here are fixed-seed simulation; there is no OTA
  or recorded-IQ evidence yet. The C++ chain and SigMF replay are built and the
  pipeline accepts recordings (`--recordings-dir`), but none were available in this
  environment, so recorded/OTA cells are an explicit empty state.
* **Single-station observability.** Doppler-only orbit determination from one
  site is fundamentally weak early in a pass; the ≈1–4 km floors (Figs. 8–9) are
  that observability limit, not a bug. Multi-station or GPS-aided fusion is future
  work.
* **Ideal compensator.** Fig. 4 uses the *known* Doppler; real compensation
  must estimate fd from noisy observations, so the measured gain is an upper bound.
* **LLM layer pending.** The agent tool layer is real, but the model's
  decision-quality (call/pick/arg) requires an online API key absent here. We leave
  it `PENDING_ONLINE_RUN` rather than quote unverified numbers.
* **Best-case BPSK.** The coherent BPSK chain assumes ideal carrier/timing sync;
  real hardware adds synchronization loss.

---

## V. Related Work

GNU Radio / SDR++ / SatDump provide powerful fixed-function SDR software but
require manual expert configuration. GR-MCP exposes GNU Radio blocks to an LLM but
does not measure the radio itself. O-RAN/6G xApp work uses AI for resource
allocation, not open SDR experimentation. This work differs by (a) treating every
radio operation as a versioned, schema'd tool, and (b) shipping a fixed-seed,
CI-runnable measurement library that reports CIs, data-origin, and manifests so
that "AI runs the radio" claims are auditable.

---

## VI. Conclusion

MBDSDR couples a C++/Flutter/Python radio stack with a function-calling tool layer
and a reproducible, honestly-labeled synthetic experiment backbone. Ten fixed-seed
experiments (≈11.8k trials) quantify decode success across Eb/N0, sample rate, and
bandwidth; show that known-Doppler compensation restores decoding that otherwise
collapses at ≈10 Hz residual offset; show KNN-AMR beating hand rules at low SNR;
and measure the local tool pipeline's closed-failure reliability. The online-LLM
and OTA/recorded results are flagged as pending. Artifacts: scripts in
`experiments/`, CSVs/figures/manifests in `paper/experiments/`, this draft in
`docs/learn/phase7/paper/`.

---

### Appendix — Experiment-to-artifact map (origin / N / date)

| Fig. | Script | Origin | N | Date (UTC) |
|---|---|---|---|---|
| Fig. 1 | `exp_ebno_decode.py` | synthetic | 2400 | 2026-10-01 |
| Fig. 2 | `exp_rate_sweep.py` | synthetic | 1400 | 2026-10-02 |
| Fig. 3 | `exp_rate_bandwidth.py` | synthetic | 900 | 2026-10-01 |
| Fig. 4 | `exp_doppler_comp.py` | synthetic | 3600 | 2026-10-02 |
| Fig. 5 | `exp_baseline_compare.py` | synthetic | 600 | 2026-10-01 |
| Fig. 6 | `exp_llm_baseline.py` | synthetic (LLM pending) | 300 | 2026-10-01 |
| Fig. 7 | `exp_amr.py` | synthetic | 640 | 2026-10-01 |
| Fig. 8 | `exp_doppler_orbit.py` | synthetic | 155 | 2026-10-01 |
| Fig. 9 | `exp_doppler_duration.py` | synthetic | 6 | 2026-10-01 |
| Fig. 10 | `exp_agent_toolcall.py` | synthetic | 400 | 2026-10-02 |
