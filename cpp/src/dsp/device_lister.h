// SPDX-License-Identifier: MIT
// Pull-based device presence lister: polls an injectable enumeration seam and
// diffs the result to report devices added / removed. Deliberately NOT a libusb
// hotplug callback -- this mirrors librtlsdr's own model
// (librtlsdr.c:1297 rtlsdr_get_device_count = libusb_get_device_list +
// find_known_device; "hotplug" is done in the app by re-listing and diffing).
//
// SEAM:
//   * Production enumerator (under HAVE_RTLSDR) wraps rtlsdr_get_device_count
//     and rtlsdr_get_device_name.
//   * Tests inject a scripted fake enumerator.
// The diff logic (new / removed detection) is fully deterministic and offline.
// Real physical USB plug/unplug is a hardware event -- this class only reacts
// to whatever the enumerator reports; true unplug recovery is marked
// 「真机待验」where it touches physical open().
#pragma once

#include <QObject>
#include <QString>
#include <vector>
#include <cstdint>

namespace mbdsdr {
namespace dsp {

struct RtlDeviceInfo {
    uint32_t index = 0;   // enumeration index (0-based)
    QString  name;        // driver-reported name
    bool operator==(const RtlDeviceInfo& o) const {
        return index == o.index && name == o.name;
    }
};

/// Enumeration seam. Production: librtlsdr. Tests: scripted fake.
class IRtlDeviceEnumerator {
public:
    virtual ~IRtlDeviceEnumerator() = default;
    virtual std::vector<RtlDeviceInfo> enumerate() = 0;
};

#ifdef HAVE_RTLSDR
/// Production enumerator: pulls rtlsdr_get_device_count / rtlsdr_get_device_name
/// (the librtlsdr pull model, librtlsdr.c:1297). Compiled only when librtlsdr is
/// available. 「真机待验」: real USB plug/unplug detection depends on physical
/// hardware; this wrapper is exercised offline only via the injected fake.
class RtlSdrDeviceEnumerator : public IRtlDeviceEnumerator {
public:
    std::vector<RtlDeviceInfo> enumerate() override;
};
#endif

class DeviceLister : public QObject {
    Q_OBJECT
public:
    /// Takes an enumerator (not owned; may be a fake in tests). The lister
    /// never opens the device -- it only observes the presence set.
    explicit DeviceLister(IRtlDeviceEnumerator* enumerator,
                          QObject* parent = nullptr);

    /// Poll the enumerator and diff against the last poll. The FIRST poll only
    /// establishes the baseline (no added/removed signals). Subsequent polls
    /// always emit deviceListChanged, plus devicesAdded / devicesRemoved when
    /// the set changed. Returns the current full list.
    std::vector<RtlDeviceInfo> poll();

    /// Last known list (baseline after first poll).
    const std::vector<RtlDeviceInfo>& current() const { return current_; }

    /// True once poll() has run at least once (baseline established).
    bool hasBaseline() const { return baselineSet_; }

signals:
    /// Fired on every poll (even unchanged) so a UI can refresh its list.
    void deviceListChanged(const std::vector<RtlDeviceInfo>& devices);
    /// Fired only when the polled set gained devices vs the previous poll.
    void devicesAdded(const std::vector<RtlDeviceInfo>& added);
    /// Fired only when the polled set lost devices vs the previous poll.
    void devicesRemoved(const std::vector<RtlDeviceInfo>& removed);

private:
    IRtlDeviceEnumerator* enumerator_;
    std::vector<RtlDeviceInfo> current_;
    bool baselineSet_ = false;
};

} // namespace dsp
} // namespace mbdsdr
