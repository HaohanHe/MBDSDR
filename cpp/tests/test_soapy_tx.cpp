// SPDX-License-Identifier: GPL-3.0-or-later
// SoapySDR TX backend error-path self-check. On a machine without SoapySDR or
// without the requested device, open() must fail honestly, not crash.
#include "tx/soapy_tx_backend.h"

#include <cstdio>

using namespace mbdsdr::tx;

int main() {
    int failures = 0;
    {
        SoapyTxBackend b("driver=this_driver_does_not_exist_zzz");
        const bool opened = b.open();
        if (opened) {
            std::printf("FAIL: bogus driver unexpectedly opened\n");
            ++failures;
        }
        if (b.errorString().isEmpty()) {
            std::printf("FAIL: no honest error message\n");
            ++failures;
        }
        // PTT must not work when not open.
        if (b.setPtt(true)) {
            std::printf("FAIL: PTT accepted while not open\n");
            ++failures;
        }
        // Destructor must clean up without crashing.
    }
    if (failures == 0) std::printf("soapy tx error path: all checks passed\n");
    return failures ? 1 : 0;
}
