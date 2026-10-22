# MBDSDR — English research-facing abstract (off-repo narrative material)

> 用途：对外学术职业口径的英语研究向摘要素材（独立文档，不进入 README；README 保持工程作品形态）。
> 只整理素材，不写论文正文；MIT 独立实现；实证状态诚实标注，不伪造完成。

**MBDSDR** is an instrument-democratization research platform: it turns
software-defined-radio (SDR) and vector-network-analyzer (VNA) workflows into
an **agent + tool-call** interface. An external LLM autonomously reasons and
composes atomic tools — there is **no pre-scripted dialogue flow and no
keyword-hardcoded scenario branch** in the product code (calibration steps and
device command words are instrument-protocol facts, exposed as tool knowledge;
the orchestration is always left to the model).

## What it solves for users (altruistic framing)

Professional measurement gear is expensive, closed-source, vendor-locked, and
its official software is often hard to learn and annoying to operate. MBDSDR
lowers the barrier for grassroots and hobbyist users — and for educators — by
making professional-level receive/decode/measurement capability usable through
plain language: the instrument still does the real work (true hardware paths,
honest empty states), while the agent + tool-call layer removes the "must learn
the instrument's workflow first" burden. The value statement is always about
what the user gets, never about the author.

## Surface

- **Desktop C++ core (Qt)**: **72 tools** (read/write; every write gated behind
  an explicit manual arm — a write is executed only after the human/agent arms
  it). Covers tuning/VFO, demod modes, squelch & CTCSS/CDCSS, digital decoders
  (ACARS/NAVTEX/POCSAG/M17/VOR/FT8/LRPT/ADS-B), scan/band presets, bookmarks,
  recording/export, and the full VNA toolchain.
- **VNA toolchain**: closed calibration loop (`vna_calibration` state machine:
  open/short/load/isolation/thru/done, coverage-freshness guard), resonance
  analysis (fr/Q/ESR), impedance matching (LC topologies), TDR cable length,
  filter shape, Touchstone export, and device-miscellany (`vna_device`).
- **External third-party agents**: attach over **MCP stdio** (JSON-RPC 2.0,
  `initialize` / `tools/list` with 8-domain grouping and name/desc/schema detail
  levels + listChanged) or ControlHub HTTP; no LLM dependency inside the server.
- **Python research kernel** (`mbdsdr_ai`): ~280 registered tools incl. a
  decoder long-tail extension domain.

## Design stance

- Independent **MIT** implementation; GPL upstreams (e.g. dump1090, nanovna-saver,
  WSJT-X) consulted for output shapes / public formulas only, never copied.
- Real data only, honest empty states; synthetic injection is test-fixture only.
- No preset TLE / callsigns / stations in code; activity parameters live in docs.

## Empirical status (honest)

- On-hardware validation is **PENDING** (NanoVNA sweep round-trip, device-command
  `help` cross-check, ADS-B 1090 MHz end-to-end, RTL-SDR ENABLED build, 7 Python
  decoder long-tail verifications). No measurement is fabricated; synthetic
  replay covers protocol/code paths only.
