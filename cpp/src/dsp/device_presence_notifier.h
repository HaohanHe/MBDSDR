// SPDX-License-Identifier: MIT
//
// Hot-plug UI notice seam. DeviceLister already does the pull/diff of the
// enumeration set (see device_lister.h); this thin QObject turns a diff event
// into the EXACT user-facing string the status bar / device panel should show.
//
// The string mapping is split out as free functions so the "diff event -> notice
// text" contract is unit-testable with no QWidget and no librtlsdr: a scripted
// fake enumerator drives DeviceLister, the notifier forwards devicesAdded /
// devicesRemoved, and the test asserts the produced text verbatim.
//
// MECHANISM (learned clean-room from librtlsdr GPLv2): librtlsdr itself does no
// libusb hotplug callback; "plugged" / "unplugged" is an application-level diff
// of re-enumerated devices. This class only formats whatever the injected
// enumerator reports -- it never opens a device. True physical USB plug/unplug
// recovery (auto-open) is 「真机待验」.
#pragma once

#include "dsp/device_lister.h"

#include <QObject>
#include <QString>
#include <vector>

namespace mbdsdr {
namespace dsp {

// ---- Pure text mapping (unit-tested directly) ----------------------------

// "RTL-SDR #0 (Fake Name), RTL-SDR #1" -- comma joined driver names.
QString formatDeviceNames(const std::vector<RtlDeviceInfo>& devices);

// A device appeared in the enumeration: "RTL-SDR 已连接：<names>".
QString deviceAddedNotice(const std::vector<RtlDeviceInfo>& added);

// A device disappeared from the enumeration: "RTL-SDR 已移除：<names>".
QString deviceRemovedNotice(const std::vector<RtlDeviceInfo>& removed);

// Forwards DeviceLister::devicesAdded / devicesRemoved to a single UI-ready
// signal. Owns nothing: the DeviceLister (and its injected enumerator) is owned
// by the caller. The UI timer just calls pollOnce() on its own cadence.
class DevicePresenceNotifier : public QObject {
    Q_OBJECT
public:
    enum class Kind { Connected, Removed };

    explicit DevicePresenceNotifier(DeviceLister* lister, QObject* parent = nullptr);

public slots:
    // Pull the enumerator one step. The diff (if any) is delivered through the
    // connected signals below. Safe to call on a 1 Hz UI timer.
    void pollOnce();

signals:
    // text is ready to drop straight into sourceBanner_ / statusLabel_; the UI
    // does no further formatting. kind tells the widget whether to tint the
    // notice as "appeared" (calm green) or "disappeared" (calm amber).
    void presenceNotice(const QString& text, Kind kind);

private:
    DeviceLister* lister_;   // not owned
};

} // namespace dsp
} // namespace mbdsdr
