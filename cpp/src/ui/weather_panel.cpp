// SPDX-License-Identifier: MIT
#include "ui/weather_panel.h"

#include "core/tokens.h"

#include <QPainter>
#include <QPaintEvent>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QFont>
#include <QFontMetrics>

namespace mbdsdr {
namespace ui {

namespace {
// Local geometry constants (base px, scaled() at runtime per DESIGN_RULES).
constexpr int kHeaderH     = 26;   // reserved top strip for status row + buttons
constexpr int kPad         = tokens::kSpacingM;   // inset around the image area (==8)
constexpr int kPresetCount  = 3;   // 137.62 / 137.9125 / 137.1 MHz NOAA passes
} // namespace

WeatherSatPanel::WeatherSatPanel(QWidget* parent) : QWidget(parent) {
    setMinimumSize(tokens::scaled(tokens::kWeatherPanelMinW), tokens::scaled(tokens::kWeatherPanelMinH));
    setAutoFillBackground(false);

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                             tokens::scaled(tokens::kSpacingS),
                             tokens::scaled(tokens::kSpacingM),
                             tokens::scaled(tokens::kSpacingM));
    root->setSpacing(tokens::scaled(tokens::kSpacingS));

    // ---- Header row: status on the left, presets + clear on the right ----
    auto* header = new QHBoxLayout;
    header->setContentsMargins(0, 0, 0, 0);
    header->setSpacing(tokens::scaled(tokens::kSpacingM));

    statusLabel_ = new QLabel(QString::fromUtf8("搜索中 · 行 0 · 相关 —"), this);
    statusLabel_->setObjectName("monoInfo");
    header->addWidget(statusLabel_, 1);

    // NOAA APT downlink presets (VHF 137 MHz band). Buttons just retune; the
    // main window also flips the demod to WFM. These are real published passes,
    // not synthetic fixtures.
    static const double kPresets[kPresetCount] = {
        137.620e6, 137.9125e6, 137.100e6
    };
    for (int i = 0; i < kPresetCount; ++i) {
        auto* b = new QPushButton(
            QString::fromUtf8("%1 MHz").arg(kPresets[i] / 1e6, 0, 'f', 4), this);
        b->setToolTip(QString::fromUtf8("调谐到 %1 MHz 并切到 WFM 解调").arg(
                          kPresets[i] / 1e6, 0, 'f', 4));
        const double hz = kPresets[i];
        connect(b, &QPushButton::clicked, this, [this, hz]() {
            emit tuneRequested(hz);
        });
        header->addWidget(b);
    }

    clearBtn_ = new QPushButton(QString::fromUtf8("清除图像"), this);
    connect(clearBtn_, &QPushButton::clicked, this, &WeatherSatPanel::clear);
    header->addWidget(clearBtn_);

    root->addLayout(header);
    // The rest of the widget is painted by paintEvent().

    updateStatusRow();
}

void WeatherSatPanel::setImage(const QImage& image, bool locked, int rows,
                               double syncCorr) {
    image_    = image;
    locked_   = locked;
    rows_     = rows;
    syncCorr_ = syncCorr;
    updateStatusRow();
    update();
}

void WeatherSatPanel::clear() {
    image_    = QImage();
    locked_   = false;
    rows_     = 0;
    syncCorr_ = 0.0;
    updateStatusRow();
    update();
    emit clearRequested();
}

void WeatherSatPanel::updateStatusRow() {
    if (!statusLabel_) return;
    const QColor fg = locked_ ? QColor(tokens::kSuccess)
                             : QColor(tokens::kTextSecondary);
    statusLabel_->setStyleSheet(QStringLiteral("color: %1;").arg(fg.name()));
    if (rows_ <= 0 && !locked_) {
        statusLabel_->setText(QString::fromUtf8("搜索中 · 行 0 · 相关 —"));
    } else {
        statusLabel_->setText(
            QString::fromUtf8("%1 · 行 %2 · 相关 %3")
                .arg(locked_ ? QString::fromUtf8("已锁定")
                              : QString::fromUtf8("搜索中"))
                .arg(rows_)
                .arg(syncCorr_, 0, 'f', 2));
    }
}

void WeatherSatPanel::paintEvent(QPaintEvent* /*event*/) {
    QPainter p(this);
    p.fillRect(rect(), QColor(tokens::kSpectrumBg));

    // Reserve the header strip for the status row / buttons.
    const int headerH = tokens::scaled(kHeaderH);
    QRect imgRect = rect().adjusted(0, headerH, 0, 0);
    const int pad = tokens::scaled(kPad);
    imgRect.adjust(pad, pad, -pad, -pad);

    // Honest empty state: no cloud image, centered two-line hint.
    if (isEmpty()) {
        QFont f = font();
        f.setPointSizeF(tokens::kFontBodyPt);
        p.setFont(f);
        p.setPen(QColor(tokens::kTextSecondary));
        p.drawText(imgRect, Qt::AlignCenter | Qt::TextWordWrap,
                   QString::fromUtf8("未接收到气象卫星信号\n"
                                     "调谐至 137 MHz 频段、WFM 解调，等待 APT 行同步…"));
        return;
    }

    // Scale the growing 1818-wide grayscale image to fit, preserving aspect.
    const QImage scaled = image_.scaled(imgRect.size(), Qt::KeepAspectRatio,
                                        Qt::SmoothTransformation);
    const int x = imgRect.x() + (imgRect.width()  - scaled.width())  / 2;
    const int y = imgRect.y() + (imgRect.height() - scaled.height()) / 2;
    p.drawImage(x, y, scaled);

    // Thin border around the live image so it reads as a picture on the bg.
    p.setPen(QPen(tokens::rgbaA(tokens::kTextAlphaTertiary2), 1.0));
    p.drawRect(x, y, scaled.width(), scaled.height());
}

} // namespace ui
} // namespace mbdsdr
