# MBDSDR Spacetime Console · Product README (English)

One system, two forms: **Professional Mode** (traditional SDR tooling plus a model collaboration panel)
and **Planetarium Mode** (the whole sky opens at once; camera-locked satellites stay still while the sky moves).

Everything on screen is a **model-callable capability**, not a prebuilt workflow. Track / Receive / Point
only open a dialogue where the model asks what hardware is actually on site (SDR, gimbal, GNSS, camera…).
Orbit determination is deliberately **not a UI feature**: it can only be triggered by the user saying it to the model.

The capability matrix, tool names, satellite catalogue, ControlHub port, time-rate presets and design
tokens are **generated from the repository source** by `build/sync_from_repo.py` — not hand-copied from
a design draft. Empty tool domains stay empty; 6DOF / AR / VR are marked planned / unverified.
Offline by default: no fake IQ, no fake successes. Writes are gated by the C++ host switch (`writeEnabled_`) for ControlHub, or a one-shot `confirm_token` issued by an external confirmation channel for the Python kernel; a model self-reporting `arm_manual` is rejected. The front end only forwards arguments, never issues tokens, and distinguishes `gated` from a real failure.

- Languages: 中文 / 日本語 / English, switchable at runtime and persisted.
- Design language: the `.figma` export mapped through `desktop/tokens.py` (pure black, greyscale, MiSans, large radii).
- Compliance: MIT self-written code; the AGPL-3.0 Stellarium Web Engine was studied for **mechanisms only**.
- Quick start: open `dist/index.html` (single file). Build: `python build/build.py`.
- Verify: `python tests/verify_product.py` and `node tests/verify_runtime.js`.

Full documentation (Chinese original): `README.md`. The in-product Docs Centre marks missing translations honestly.
