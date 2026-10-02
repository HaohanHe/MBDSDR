// RxVFO-style bandwidth short-circuit + transition-band parameterization tests.
//
// Clean-room mapping of SDR++ rx_vfo.h (GPLv3, mechanism only):
//   - filterNeeded = (bandwidth != outSamplerate)  -> whole-band passthrough
//   - generateTaps low-pass transition width = 0.1 * (bandwidth/2)
// We test the PURE decision function (planChannelFilter), the Channelizer fast
// path that skips the low-pass when bandwidth == output rate, and NCO retune
// phase continuity. Standalone, no Qt GUI, no FFT dependency.
#include <cmath>
#include <vector>
#include <complex>
#include <cstdio>
#include "dsp/channelizer.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool cond, const char* msg) {
    if (!cond) { ++failures; std::printf("FAIL: %s\n", msg); }
}

static bool near(double a, double b, double tol) {
    return std::fabs(a - b) <= tol;
}

int main() {
    // ---- 1) Pure decision: short-circuit when bandwidth == output rate ------
    {
        auto s = planChannelFilter(48000.0, 48000.0, 0.1);
        check(s.filterNeeded == false, "bw==outRate => low-pass NOT needed (short-circuit)");
        check(near(s.passbandEdgeHz, 24000.0, 1e-9), "passband edge = bw/2 = 24k");
        check(near(s.transitionWidthHz, 2400.0, 1e-9),
              "transition width = 0.1 * bw/2 = 2.4k (upstream default)");
    }

    // ---- 2) Pure decision: narrow channel needs the low-pass ----------------
    {
        auto s = planChannelFilter(48000.0, 10000.0, 0.1);
        check(s.filterNeeded == true, "bw<outRate => low-pass needed");
        check(near(s.passbandEdgeHz, 5000.0, 1e-9), "passband edge = 5k");
        check(near(s.transitionWidthHz, 500.0, 1e-9), "transition width = 0.1*5k = 500");
        check(near(s.designCutoffHz, 5000.0, 1e-9),
              "design cutoff = bw/2 (not clamped: 5k << Nyquist 24k)");
    }

    // ---- 3) Pure decision: transition band is a configurable ratio ----------
    {
        auto s = planChannelFilter(48000.0, 10000.0, 0.2);
        check(near(s.transitionWidthHz, 1000.0, 1e-9),
              "custom transitionRatio 0.2 -> width = 0.2*5k = 1k");
    }

    // ---- 4) Pure decision: cutoff clamped so passband+transition < Nyquist -
    {
        // bw=47k at 48k rate: bw/2=23.5k would exceed Nyquist(24k)-transition.
        auto s = planChannelFilter(48000.0, 47000.0, 0.1);
        check(s.filterNeeded == true, "47k < 48k => still needs filtering");
        check(near(s.designCutoffHz, 24000.0 - 2350.0, 1e-6),
              "cutoff clamped to Nyquist - transition band (21.65k)");
    }

    // ---- 5) Channelizer integration: bw==rate, no rate change -> skip LPF ----
    {
        Channelizer ch;
        ch.configure(48000.0, 48000.0, 48000.0, 31);
        check(ch.decimation() == 1, "in==out => decimation 1");
        check(ch.filterSkipped() == true, "whole-band passthrough skips the FIR");
        check(ch.mode() == ChannelizerMode::Integer, "integer-exact ratio");

        // A +1 kHz analytic tone; tune the VFO to +1 kHz. With NO low-pass the
        // only thing that may act on the signal is the NCO, which must mix the
        // tone to a flat DC (1,0) -- proving the passthrough path still tunes.
        const double fs = 48000.0, f = 1000.0;
        std::vector<std::complex<float>> in(2048);
        for (int i = 0; i < 2048; ++i) {
            const double a = 2.0 * M_PI * f * i / fs;
            in[i] = {static_cast<float>(std::cos(a)), static_cast<float>(std::sin(a))};
        }
        ch.setVfoOffsetHz(f);
        auto out = ch.process(in);
        check(out.size() == in.size(), "passthrough keeps every sample (no decimation)");
        bool flat = true;
        for (auto& s : out)
            if (std::fabs(s.real() - 1.0f) > 1e-3f || std::fabs(s.imag()) > 1e-3f) {
                flat = false; break;
            }
        check(flat, "tuned tone mixed to flat DC by the NCO (filter skipped)");
    }

    // ---- 6) Channelizer integration: wide but NOT whole-band -> FIR kept ----
    {
        Channelizer ch;
        ch.configure(2400000.0, 2400000.0, 1000000.0, 31); // mirrors channelizer Test 3
        check(ch.decimation() == 1, "passthrough-rate decimation 1");
        check(ch.filterSkipped() == false, "bw=1MHz < 2.4MHz rate => FIR kept (no short-circuit)");
    }

    // ---- 7) NCO retune phase continuity (no reset on frequency change) -----
    {
        Nco n;
        const double fs = 48000.0;
        const double f1 = 1000.0, f2 = 2500.0;
        const double d1 = 2.0 * M_PI * f1 / fs;
        const double d2 = 2.0 * M_PI * f2 / fs;

        // (a) Short burst must match the analytic oscillator e^{-j 2pi f i/fs}.
        // Kept short so float phase accumulation stays inside the tolerance.
        // Nco::configure(shiftHz, sampleRateHz) -- note argument order.
        n.configure(f1, fs);
        bool f1ok = true;
        for (int i = 0; i < 64; ++i) {
            std::complex<float> ref(static_cast<float>(std::cos(i * d1)),
                                    static_cast<float>(-std::sin(i * d1)));
            if (std::abs(n.next() - ref) > 1e-4f) { f1ok = false; break; }
        }
        check(f1ok, "NCO output matches e^{-j 2pi f i/fs}");

        // (b) Retune mid-stream. Continuity is judged by per-step angle DELTAS
        // (robust to absolute float accumulation drift): the step into the
        // retune boundary must still be d1 (old rate), then the following steps
        // must switch smoothly to d2, with NO reset to (1,0). A reset would make
        // the boundary step a large, out-of-rate jump.
        n.configure(f1, fs);
        const int N = 200;
        std::vector<std::complex<float>> a(N);
        for (int i = 0; i < N; ++i) a[i] = n.next();
        const double angPrev = std::arg(std::complex<double>(a[N - 1]));
        n.configure(f2, fs);                 // retune: dphi changes, phase persists
        std::complex<float> b0 = n.next();   // first sample AFTER retune
        std::complex<float> b1 = n.next();   // second sample AFTER retune
        // Step a[N-2] -> a[N-1] used the old rate d1.
        const double stepOld = std::arg(std::complex<double>(a[N - 1] * std::conj(a[N - 2])));
        // Step a[N-1] -> b0 : boundary, still old rate d1 (retune happens after).
        const double stepBnd = std::arg(std::complex<double>(b0 * std::conj(a[N - 1])));
        // Step b0 -> b1 : new rate d2.
        const double stepNew = std::arg(std::complex<double>(b1 * std::conj(b0)));
        auto nearStep = [](double got, double want) {
            double d = std::fmod(got + M_PI, 2.0 * M_PI);
            if (d < 0) d += 2.0 * M_PI;
            d -= M_PI;
            // expected step on the DOWN-mix e^{-j w n}: angle decrements by w.
            return std::fabs(d - (-want)) < 0.02;
        };
        check(nearStep(stepOld, d1), "pre-retune step == old rate");
        check(nearStep(stepBnd, d1), "boundary step continuous (no reset jump)");
        check(nearStep(stepNew, d2), "post-retune step == new rate (smooth)");
        (void)angPrev;
    }

    if (failures == 0) std::printf("test_bandwidth_shortcut: ALL PASS\n");
    else std::printf("test_bandwidth_shortcut: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
