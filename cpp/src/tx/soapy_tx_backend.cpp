// SPDX-License-Identifier: MIT
#include "soapy_tx_backend.h"

#include <complex>
#include <cstring>

#if defined(__unix__)
#include <dlfcn.h>
#elif defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace mbdsdr {
namespace tx {

namespace {
// Minimal mirrors of the SoapySDR C ABI (only what TX needs).
struct SoapyKwargs { size_t size; char** keys; char** vals; };
using SoapyDevice = void;
using SoapyStream = void;

using PfnKwargsFromStr = SoapyKwargs (*)(const char*);
using PfnKwargsClear = void (*)(SoapyKwargs*);
using PfnMake = SoapyDevice* (*)(const SoapyKwargs*);
using PfnUnmake = void (*)(SoapyDevice*);
using PfnLastError = char* (*)(void);
using PfnSetRate = int (*)(SoapyDevice*, int, unsigned, double);
using PfnSetFreq = int (*)(SoapyDevice*, int, unsigned, double, const SoapyKwargs*);
using PfnSetGain = int (*)(SoapyDevice*, int, unsigned, double);
using PfnSetGainMode = int (*)(SoapyDevice*, int, unsigned, int);
using PfnSetupStream = SoapyStream* (*)(SoapyDevice*, int, const char*,
                                       const unsigned*, size_t, const SoapyKwargs*);
using PfnActivate = int (*)(SoapyDevice*, SoapyStream*, int, long long, size_t);
using PfnDeactivate = int (*)(SoapyDevice*, SoapyStream*, int, long long);
using PfnCloseStream = int (*)(SoapyDevice*, SoapyStream*);
using PfnWrite = int (*)(SoapyDevice*, SoapyStream*, const void* const*, size_t,
                         int*, long long, long);

// SoapySDR direction code for transmit (ABI: SOAPY_SDR_TX = 0, SOAPY_SDR_RX = 1).
constexpr int SOAPY_TX = 0;
} // namespace

struct SoapyTxBackend::Impl {
    void* lib = nullptr;
    PfnKwargsFromStr kwargsFromStr = nullptr;
    PfnKwargsClear kwargsClear = nullptr;
    PfnMake make = nullptr;
    PfnUnmake unmake = nullptr;
    PfnLastError lastError = nullptr;
    PfnSetRate setRate = nullptr;
    PfnSetFreq setFreq = nullptr;
    PfnSetGain setGain = nullptr;
    PfnSetGainMode setGainMode = nullptr;
    PfnSetupStream setupStream = nullptr;
    PfnActivate activate = nullptr;
    PfnDeactivate deactivate = nullptr;
    PfnCloseStream closeStream = nullptr;
    PfnWrite write = nullptr;

    SoapyDevice* dev = nullptr;
    SoapyStream* stream = nullptr;
    bool opened = false;
    bool ptt = false;
    bool streamActive = false;
    double freq = 0.0, rate = 0.0, gain = 0.0;
};

SoapyTxBackend::SoapyTxBackend(const QString& driverArgs)
    : d_(new Impl), args_(driverArgs) {}

SoapyTxBackend::~SoapyTxBackend() {
    close();
    delete d_;
}

namespace {
void* loadLib() {
#if defined(__unix__)
    void* h = dlopen("libSoapySDR.so", RTLD_NOW | RTLD_LOCAL);
    if (!h) h = dlopen("libSoapySDR.so.0.8", RTLD_NOW | RTLD_LOCAL);
    return h;
#elif defined(_WIN32)
    return LoadLibraryA("SoapySDR.dll");
#else
    return nullptr;
#endif
}

void* loadSym(void* lib, const char* name) {
#if defined(__unix__)
    return dlsym(lib, name);
#elif defined(_WIN32)
    return reinterpret_cast<void*>(GetProcAddress((HMODULE)lib, name));
#else
    (void)lib; (void)name; return nullptr;
#endif
}

void closeLib(void* lib) {
#if defined(__unix__)
    if (lib) dlclose(lib);
#elif defined(_WIN32)
    if (lib) FreeLibrary((HMODULE)lib);
#endif
}
} // namespace

bool SoapyTxBackend::open() {
    d_->lib = loadLib();
    if (!d_->lib) {
        err_ = QStringLiteral("SoapySDR library not found");
        return false;
    }
    auto resolve = [&](auto& pfn, const char* sym) -> bool {
        pfn = reinterpret_cast<std::remove_reference_t<decltype(pfn)>>(
            loadSym(d_->lib, sym));
        return pfn != nullptr;
    };

    bool ok = true;
    ok &= resolve(d_->kwargsFromStr, "SoapySDRKwargs_fromString");
    ok &= resolve(d_->kwargsClear, "SoapySDRKwargs_clear");
    ok &= resolve(d_->make, "SoapySDRDevice_make");
    ok &= resolve(d_->unmake, "SoapySDRDevice_unmake");
    ok &= resolve(d_->lastError, "SoapySDRDevice_lastError");
    ok &= resolve(d_->setRate, "SoapySDRDevice_setSampleRate");
    ok &= resolve(d_->setFreq, "SoapySDRDevice_setFrequency");
    ok &= resolve(d_->setGain, "SoapySDRDevice_setGain");
    ok &= resolve(d_->setGainMode, "SoapySDRDevice_setGainMode");
    ok &= resolve(d_->setupStream, "SoapySDRDevice_setupStream");
    ok &= resolve(d_->activate, "SoapySDRDevice_activateStream");
    ok &= resolve(d_->deactivate, "SoapySDRDevice_deactivateStream");
    ok &= resolve(d_->closeStream, "SoapySDRDevice_closeStream");
    ok &= resolve(d_->write, "SoapySDRDevice_writeStream");
    if (!ok) {
        err_ = QStringLiteral("incompatible SoapySDR library (missing symbols)");
        closeLib(d_->lib); d_->lib = nullptr;
        return false;
    }

    SoapyKwargs kw = d_->kwargsFromStr(args_.toUtf8().constData());
    d_->dev = d_->make(&kw);
    d_->kwargsClear(&kw);
    if (!d_->dev) {
        const char* e = d_->lastError ? d_->lastError() : nullptr;
        err_ = QStringLiteral("Soapy device open failed: %1")
                   .arg(e ? QString::fromUtf8(e) : QStringLiteral("unknown"));
        closeLib(d_->lib); d_->lib = nullptr;
        return false;
    }

    const double rate = d_->rate > 0 ? d_->rate : 2000000.0;
    if (d_->setRate(d_->dev, SOAPY_TX, 0, rate) != 0) {
        err_ = QStringLiteral("Soapy: rejected sample rate");
        close(); return false;
    }
    d_->rate = rate;
    d_->setGainMode(d_->dev, SOAPY_TX, 0, 0);  // manual gain
    if (d_->freq > 0) d_->setFreq(d_->dev, SOAPY_TX, 0, d_->freq, nullptr);

    static const unsigned kChannel = 0;
    d_->stream = d_->setupStream(d_->dev, SOAPY_TX, "CF32", &kChannel, 1, nullptr);
    if (!d_->stream) {
        err_ = QStringLiteral("Soapy: TX stream setup failed");
        close(); return false;
    }

    d_->opened = true;
    return true;
}

void SoapyTxBackend::close() {
    if (!d_) return;
    if (d_->streamActive && d_->deactivate)
        d_->deactivate(d_->dev, d_->stream, 0, 0);
    d_->streamActive = false;
    d_->ptt = false;
    if (d_->stream && d_->closeStream) d_->closeStream(d_->dev, d_->stream);
    d_->stream = nullptr;
    if (d_->dev && d_->unmake) d_->unmake(d_->dev);
    d_->dev = nullptr;
    d_->opened = false;
    if (d_->lib) { closeLib(d_->lib); d_->lib = nullptr; }
}

bool SoapyTxBackend::isOpen() const { return d_->opened; }

bool SoapyTxBackend::setFrequencyHz(double hz) {
    if (hz <= 0) return false;
    d_->freq = hz;
    if (d_->dev && d_->setFreq(d_->dev, SOAPY_TX, 0, hz, nullptr) != 0) {
        err_ = QStringLiteral("Soapy: rejected frequency"); return false;
    }
    return true;
}

bool SoapyTxBackend::setSampleRate(double sps) {
    if (sps <= 0) return false;
    d_->rate = sps;
    if (d_->dev && d_->setRate(d_->dev, SOAPY_TX, 0, sps) != 0) {
        err_ = QStringLiteral("Soapy: rejected sample rate"); return false;
    }
    return true;
}

bool SoapyTxBackend::setGainDb(double db) {
    d_->gain = db;
    if (d_->dev) d_->setGain(d_->dev, SOAPY_TX, 0, db);
    return true;
}

bool SoapyTxBackend::setPtt(bool on) {
    if (!d_->opened) return false;
    if (on) {
        if (d_->freq <= 0) { err_ = QStringLiteral("PTT refused: no frequency"); return false; }
        if (!d_->streamActive &&
            d_->activate(d_->dev, d_->stream, 0, 0, 0) != 0) {
            err_ = QStringLiteral("Soapy: activate stream failed"); return false;
        }
        d_->streamActive = true;
        d_->ptt = true;
    } else {
        if (d_->streamActive) d_->deactivate(d_->dev, d_->stream, 0, 0);
        d_->streamActive = false;
        d_->ptt = false;
    }
    return true;
}

bool SoapyTxBackend::pttActive() const { return d_->ptt; }

int SoapyTxBackend::writeComplex(const std::complex<float>* buf, int n) {
    if (!d_->ptt || !d_->opened || n <= 0) return 0;
    const void* buffs[1] = { buf };
    int flags = 0;
    const int r = d_->write(d_->dev, d_->stream, buffs, n, &flags, 0, 1000000);
    return r < 0 ? 0 : r;
}

double SoapyTxBackend::frequencyHz() const { return d_->freq; }
double SoapyTxBackend::sampleRate() const { return d_->rate; }

} // namespace tx
} // namespace mbdsdr
