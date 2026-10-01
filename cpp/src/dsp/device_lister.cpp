// SPDX-License-Identifier: MIT
#include "device_lister.h"

#include <algorithm>

#ifdef HAVE_RTLSDR
#include <rtl-sdr.h>
#endif

namespace mbdsdr {
namespace dsp {

#ifdef HAVE_RTLSDR
std::vector<RtlDeviceInfo> RtlSdrDeviceEnumerator::enumerate() {
    std::vector<RtlDeviceInfo> out;
    const int count = rtlsdr_get_device_count();
    for (uint32_t i = 0; i < static_cast<uint32_t>(count); ++i) {
        RtlDeviceInfo info;
        info.index = i;
        // rtlsdr_get_device_name never returns NULL for a valid index; guard
        // anyway so a driver quirk degrades to an honest empty list, not a
        // crash.
        const char* name = rtlsdr_get_device_name(i);
        info.name = name ? QString::fromUtf8(name) : QStringLiteral("RTL-SDR #%1").arg(i);
        out.push_back(info);
    }
    return out;
}
#endif

DeviceLister::DeviceLister(IRtlDeviceEnumerator* enumerator, QObject* parent)
    : QObject(parent), enumerator_(enumerator) {}

std::vector<RtlDeviceInfo> DeviceLister::poll() {
    std::vector<RtlDeviceInfo> next = enumerator_ ? enumerator_->enumerate()
                                                  : std::vector<RtlDeviceInfo>();

    if (!baselineSet_) {
        // First poll: establish baseline, no diff events.
        current_     = next;
        baselineSet_ = true;
        emit deviceListChanged(current_);
        return current_;
    }

    // Diff: added = in next but not in current_; removed = in current_ but not
    // in next. Identity is (index, name) -- a re-named device at the same index
    // counts as removed+added (the driver identity changed).
    std::vector<RtlDeviceInfo> added;
    std::vector<RtlDeviceInfo> removed;
    for (const auto& d : next) {
        if (std::find(current_.begin(), current_.end(), d) == current_.end())
            added.push_back(d);
    }
    for (const auto& d : current_) {
        if (std::find(next.begin(), next.end(), d) == next.end())
            removed.push_back(d);
    }

    current_ = next;
    emit deviceListChanged(current_);
    if (!added.empty())   emit devicesAdded(added);
    if (!removed.empty()) emit devicesRemoved(removed);
    return current_;
}

} // namespace dsp
} // namespace mbdsdr
