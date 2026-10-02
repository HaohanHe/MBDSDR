// SPDX-License-Identifier: MIT
#include "dsp/device_presence_notifier.h"

#include <algorithm>

namespace mbdsdr {
namespace dsp {

QString formatDeviceNames(const std::vector<RtlDeviceInfo>& devices) {
    QStringList parts;
    for (const auto& d : devices) {
        const QString nm = d.name.trimmed().isEmpty()
            ? QStringLiteral("RTL-SDR #%1").arg(d.index)
            : d.name.trimmed();
        parts << nm;
    }
    return parts.join(QStringLiteral("、"));
}

QString deviceAddedNotice(const std::vector<RtlDeviceInfo>& added) {
    if (added.empty()) return {};
    return QStringLiteral("RTL-SDR 已连接：%1").arg(formatDeviceNames(added));
}

QString deviceRemovedNotice(const std::vector<RtlDeviceInfo>& removed) {
    if (removed.empty()) return {};
    return QStringLiteral("RTL-SDR 已移除：%1").arg(formatDeviceNames(removed));
}

DevicePresenceNotifier::DevicePresenceNotifier(DeviceLister* lister, QObject* parent)
    : QObject(parent), lister_(lister) {
    if (!lister_) return;
    // Forward the lister's diff events to the single UI-ready notice signal.
    // Connections live as long as the notifier; the lister outlives it in the
    // production wiring (MainWindow owns both).
    connect(lister_, &DeviceLister::devicesAdded, this,
        [this](const std::vector<RtlDeviceInfo>& added) {
            emit presenceNotice(deviceAddedNotice(added), Kind::Connected);
        });
    connect(lister_, &DeviceLister::devicesRemoved, this,
        [this](const std::vector<RtlDeviceInfo>& removed) {
            emit presenceNotice(deviceRemovedNotice(removed), Kind::Removed);
        });
}

void DevicePresenceNotifier::pollOnce() {
    if (lister_) lister_->poll();
}

} // namespace dsp
} // namespace mbdsdr
