// SPDX-License-Identifier: GPL-3.0-or-later
// TX interlock / watchdog self-check (not hardware).
#include "tx/loopback_backend.h"
#include "tx/modulator.h"
#include "tx/tx_controller.h"

#include <cstdio>
#include <vector>

using namespace mbdsdr::tx;

static int g_failures = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { std::printf("FAIL: %s\n", msg); ++g_failures; } \
} while (0)

static std::vector<float> block(int n) {
    return std::vector<float>(n, 0.4f);
}

int main() {
    LoopbackTxBackend backend;
    ModulatorAM mod(48000.0, 10000.0);
    TxController ctrl(backend, mod, 48000.0);

    // 1) No explicit PTT -> nothing emitted at all.
    CHECK(ctrl.transmitAudio(block(4800)) == 0, "no PTT => zero samples");
    CHECK(backend.captured().empty(), "no PTT => no captured IQ");
    CHECK(!ctrl.transmitting(), "not transmitting before PTT");

    // 2) PTT refused while backend closed / frequency invalid.
    CHECK(!ctrl.requestPtt(true), "PTT refused when closed");
    CHECK(backend.open(), "open backend");
    CHECK(!ctrl.requestPtt(true), "PTT refused with invalid freq");
    CHECK(backend.setFrequencyHz(14000000.0), "valid freq");

    // 3) PTT on -> audio captured; release stops further capture.
    CHECK(ctrl.requestPtt(true), "PTT accepted");
    CHECK(ctrl.transmitting(), "transmitting after PTT");
    CHECK(ctrl.transmitAudio(block(4800)) == 4800, "samples pushed while ON AIR");
    CHECK(backend.captured().size() == 4800, "IQ captured while ON AIR");
    CHECK(ctrl.requestPtt(false), "PTT released");
    CHECK(ctrl.transmitAudio(block(4800)) == 0, "no samples after release");
    CHECK(backend.captured().size() == 4800, "capture frozen after release");
    CHECK(!backend.pttActive(), "backend PTT off after release");

    // 4) Watchdog: 0.5 s limit = 24000 samples; blocks are 0.1 s.
    LoopbackTxBackend b2;
    ModulatorAM m2(48000.0, 10000.0);
    TxController c2(b2, m2, 48000.0);
    c2.setMaxPttSeconds(0.5);
    b2.open();
    b2.setFrequencyHz(14000000.0);
    CHECK(c2.requestPtt(true), "c2 PTT on");
    for (int i = 0; i < 5; ++i)
        CHECK(c2.transmitAudio(block(4800)) == 4800, "allowed up to limit");
    CHECK(b2.captured().size() == 24000, "exactly 0.5 s captured");
    // 6th block crosses the limit -> watchdog trips, block rejected.
    CHECK(c2.transmitAudio(block(4800)) == 0, "6th block rejected by watchdog");
    CHECK(c2.watchdogTripped(), "watchdog flagged tripped");
    CHECK(!c2.transmitting() && !b2.pttActive(), "transmitter forced off by watchdog");
    // Cannot re-key while tripped; after reset it works.
    CHECK(!c2.requestPtt(true), "PTT refused while tripped");
    c2.resetWatchdog();
    CHECK(c2.requestPtt(true), "PTT re-armed after watchdog reset");

    if (g_failures == 0) std::printf("tx interlock: all checks passed\n");
    return g_failures ? 1 : 0;
}
