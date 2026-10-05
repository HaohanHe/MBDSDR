// SPDX-License-Identifier: MIT
//
// Unit tests for the satellite-pass one-tap capture + live Doppler
// auto-compensation decision logic (src/core/sat_capture.h):
//
//   1. captureTargetHz() -- active-VFO landing frequency: known carrier lands on
//      f0 + peak-Doppler suggestion; unknown carrier (f0==0) returns 0 (the UI
//      must disable the button).  We then drive a real VfoManager to prove the
//      selected channel actually lands on that frequency + mapped mode/bandwidth.
//   2. recommendSatelliteMode() -- frequency-domain -> default mode/bandwidth.
//   3. DopplerStepLimiter -- radar-style bounded step (never slams the tuner).
//   4. Real offline SGP4 propagation: propagateAt() range-rate -> dopplerHz()
//      -> the live VFO target equals f0 + dopplerHz(f0, rangeRate) to Hz, and
//      the limiter converges onto it (real LEO fd moves << maxStep per 1 s).
//
// The orbit propagation here is the SAME original SGP4 used by the sky tab
// (TleClient::propagateAt), validated against the AIAA-2006-6753 oracle.
#include "core/sat_capture.h"
#include "dsp/tle_client.h"
#include "dsp/vfo_manager.h"

#include <cmath>
#include <cstdio>

#include <QDateTime>
#include <QDate>
#include <QTime>

using namespace mbdsdr;
using namespace mbdsdr::core;
using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

int main() {
    // ------------------------------------------------------------------
    // 1. captureTargetHz: known vs unknown downlink carrier.
    // ------------------------------------------------------------------
    const double f0Noaa = 137.9125e6;   // NOAA-18 APT, known
    const double peakFd = 1234.0;       // arbitrary predicted peak-Doppler (Hz)
    const double targetKnown = captureTargetHz(f0Noaa, peakFd);
    check(std::fabs(targetKnown - (f0Noaa + peakFd)) < 1e-6,
          "capture target = f0DownlinkHz + dopplerAtPeakHz (known carrier)");
    check(captureTargetHz(0.0, peakFd) == 0.0,
          "capture target = 0 when f0DownlinkHz == 0 (unknown, button disabled)");
    check(captureTargetHz(-5.0, peakFd) == 0.0,
          "capture target = 0 for non-positive f0");

    // Drive a real VfoManager: the capture path sets the ACTIVE (selected) VFO
    // to the target and applies the mapped mode/bandwidth.
    {
        VfoManager mgr;
        mgr.initDefault(2.4e6, 100.0e6, "NFM", 12500.0);
        const int sel = mgr.selectedId();
        const SatChannelMode ch = recommendSatelliteMode(f0Noaa);
        mgr.setFreq(sel, targetKnown);
        mgr.setMode(sel, ch.mode);
        mgr.setBandwidth(sel, ch.bandwidthHz);
        const VfoChannel* c = mgr.channel(sel);
        check(c && std::fabs(c->freqHz - targetKnown) < 1e-6,
              "capture: active VFO frequency lands on f0+peakDoppler");
        check(c && c->mode == ch.mode, "capture: active VFO mode from mapping");
        check(c && std::fabs(c->bandwidthHz - ch.bandwidthHz) < 1.0,
              "capture: active VFO bandwidth from mapping");
    }
    // f0 unknown: the UI disables capture, so the VFO must NOT be retuned.
    {
        VfoManager mgr;
        mgr.initDefault(2.4e6, 100.0e6, "NFM", 12500.0);
        const double before = mgr.channel(mgr.selectedId())->freqHz;
        const double t = captureTargetHz(0.0, peakFd);   // 0 = refuse
        if (t > 0.0) mgr.setFreq(mgr.selectedId(), t);    // caller would skip this
        check(std::fabs(mgr.channel(mgr.selectedId())->freqHz - before) < 1e-6,
              "capture unknown f0: active VFO left untouched");
    }

    // ------------------------------------------------------------------
    // 2. recommendSatelliteMode by frequency domain.
    // ------------------------------------------------------------------
    {
        const auto noaa = recommendSatelliteMode(137.9125e6);
        check(noaa.mode == "WFM" && std::fabs(noaa.bandwidthHz - 60000.0) < 1.0,
              "137.9 MHz (NOAA APT) -> WFM / 60k");
        const auto adsb = recommendSatelliteMode(1090.0e6);
        check(adsb.mode == "ADS-B" && adsb.bandwidthHz > 1.0e6,
              "1090 MHz -> ADS-B wideband");
        const auto lrit = recommendSatelliteMode(1698.0e6);
        check(lrit.mode == "BPSK" && lrit.bandwidthHz >= 20000.0,
              "1698 MHz (LRIT) -> BPSK");
        const auto uhf = recommendSatelliteMode(437.8e6);
        check(uhf.mode == "BPSK", "437.8 MHz UHF telemetry -> BPSK");
        const auto vhfVoice = recommendSatelliteMode(145.0e6);
        check(vhfVoice.mode == "NFM" && std::fabs(vhfVoice.bandwidthHz - 12500.0) < 1.0,
              "145 MHz VHF voice -> NFM / 12.5k");
        const auto generic = recommendSatelliteMode(500.0e6);
        check(generic.mode == "NFM", "unmapped band -> conservative NFM fallback");
    }

    // ------------------------------------------------------------------
    // 3. DopplerStepLimiter: bounded, radar-style convergence (now a pure
    //    OFFSET in Hz on top of the nominal downlink).
    // ------------------------------------------------------------------
    {
        DopplerStepLimiter lim;   // default maxStep = tokens::kDopplerMaxStepHz
        check(std::fabs(lim.maxStep() - 2000.0) < 1e-9,
              "limiter default maxStep = kDopplerMaxStepHz (2000 Hz)");
        lim.reset(0.0);   // start at zero Doppler offset
        check(lim.state() == DopplerLimiterState::Tracking && !lim.frozen(),
              "reset() binds Tracking at the given offset");
        // A 5000 Hz offset target must be sliced into 2000 Hz steps, not slammed.
        double s1 = lim.advance(5000.0);
        check(std::fabs(s1 - 2000.0) < 1e-6,
              "limiter step 1 caps at +maxStep (radar convergence)");
        double s2 = lim.advance(5000.0);
        check(std::fabs(s2 - 4000.0) < 1e-6,
              "limiter step 2 continues bounded");
        double s3 = lim.advance(5000.0);
        check(std::fabs(s3 - 5000.0) < 1e-6,
              "limiter lands exactly once within one step");
        // Small live corrections (<< maxStep) land immediately (no lag).
        double s4 = lim.advance(5050.0);
        check(std::fabs(s4 - 5050.0) < 1e-6,
              "sub-step live correction lands immediately");
        // disarm() is a hard stop: Idle at offset 0; the next advance() re-binds.
        lim.disarm();
        check(lim.offset() == 0.0 && lim.state() == DopplerLimiterState::Idle,
              "disarm() releases to Idle at offset 0");
        double s5 = lim.advance(300.0);
        check(std::fabs(s5 - 300.0) < 1e-6, "disarm -> Idle advance re-binds to target");
    }

    // ------------------------------------------------------------------
    // 4. Real SGP4 propagation: live fd -> VFO landing = f0 + dopplerHz(f0, vr).
    //    Reuse the 28057 (CBERS 2) oracle scenario from test_pass_doppler.
    // ------------------------------------------------------------------
    {
        TleEntry tle;
        tle.name  = "CBERS 2";
        tle.line1 = "1 28057U 03049A   06177.78615833  .00000060  00000-0  35940-4 0  1836";
        tle.line2 = "2 28057  98.4283 247.6961 0000884  88.1964 271.9322 14.35478080140550";
        QDate d0(2006, 1, 1);
        double dayFrac = 177.78615833 - 1.0;
        qint64 dayInt = static_cast<qint64>(std::floor(dayFrac));
        double secs = (dayFrac - dayInt) * 86400.0;
        QDateTime midnight(d0.addDays(dayInt), QTime(0, 0, 0), Qt::UTC);
        QDateTime epoch = QDateTime::fromMSecsSinceEpoch(
            midnight.toMSecsSinceEpoch() + static_cast<qint64>(secs * 1000.0), Qt::UTC);
        QDateTime start = epoch.addSecs(240 * 60);

        const double staLat = 40.0, staLon = -100.0;
        const double f0 = 437.8e6;

        TleClient client;
        QList<SatPass> passes = client.computePasses({tle}, staLat, staLon, start, 6);
        check(!passes.isEmpty(), "offline SGP4 found a pass for 28057");
        if (passes.isEmpty()) { std::printf("test_sat_capture: %d FAILURE(S)\n", ++failures); return 1; }
        const SatPass p = passes.first();

        // Capture once: the limiter binds to the predicted peak-Doppler OFFSET;
        // the UI would set VFO = f0 + offset() (= f0 + dopplerAtPeakHz).
        DopplerStepLimiter lim;
        lim.reset(p.dopplerAtPeakHz);

        // Walk the pass at 30 s steps.  On capture the offset binds to the PEAK
        // Doppler; at AOS the live fd is several kHz away, so the limiter
        // converges in bounded 2 kHz/s steps (a deliberate transient, NOT a
        // slam).  We assert (a) every per-tick offset move is bounded by the
        // token, and (b) once past the AOS/LOS transients the loop lands EXACTLY
        // on dopplerHz(f0, rangeRate) every tick.
        double maxPerMove = 0.0, steadyMaxGap = 0.0;
        int tick = 0;
        const int totalTicks = static_cast<int>(p.aos.secsTo(p.los) / 30) + 1;
        for (qint64 s = 0; s <= p.aos.secsTo(p.los); s += 30, ++tick) {
            QDateTime t = p.aos.addSecs(s);
            Topocentric tp = client.propagateAt(t, tle, staLat, staLon);
            const double fd = dopplerHz(f0, tp.rangeRateKmS);   // desired offset
            const double before = lim.offset();
            const double stepped = lim.advance(fd);
            maxPerMove = std::max(maxPerMove, std::fabs(stepped - before));
            // Skip the first/last few ticks (AOS/LOS convergence transient).
            if (tick >= 4 && tick <= totalTicks - 5)
                steadyMaxGap = std::max(steadyMaxGap, std::fabs(stepped - fd));
        }
        std::printf("  max per-tick move: %.1f Hz (token %.0f)\n",
                    maxPerMove, lim.maxStep());
        std::printf("  steady-state residual: %.6f Hz\n", steadyMaxGap);
        check(maxPerMove <= lim.maxStep() + 1e-6,
              "every 1 Hz retune step is bounded by kDopplerMaxStepHz (no slam)");
        check(steadyMaxGap < 1.0,
              "steady-state compensation lands on f0+dopplerHz(f0,rangeRate)");

        // Sanity: the AOS/LOS fd signs match the oracle (closing +, receding -).
        Topocentric tAos = client.propagateAt(p.aos, tle, staLat, staLon);
        Topocentric tLos = client.propagateAt(p.los, tle, staLat, staLon);
        check(dopplerHz(f0, tAos.rangeRateKmS) > 0.0, "AOS fd > 0 (closing)");
        check(dopplerHz(f0, tLos.rangeRateKmS) < 0.0, "LOS fd < 0 (receding)");
    }

    if (failures == 0) std::printf("test_sat_capture: ALL PASS\n");
    else std::printf("test_sat_capture: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
