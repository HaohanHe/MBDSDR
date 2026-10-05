// SPDX-License-Identifier: MIT
#include "ui/vor_panel.h"

#include "core/tokens.h"

#include <QHBoxLayout>
#include <QLabel>
#include <QPainter>
#include <QPaintEvent>
#include <QVBoxLayout>

#include <cmath>

namespace mbdsdr {
namespace ui {

namespace {
// Local geometry (base px, scaled() at runtime).
constexpr int kHeaderH  = 26;   // top strip: mode hint + lock badge
// Bottom read-out strip meets the logical touch minimum (>=44px tap target);
// derive from the shared token rather than re-hardcoding 44.
constexpr int kReadoutH  = tokens::kTouchMinDim;
constexpr int kDialPad   = 10;   // inset around the compass ring
constexpr double kDeg2Rad = 3.14159265358979323846 / 180.0;
} // namespace

VorPanel::VorPanel(QWidget* parent) : QWidget(parent) {
    setMinimumSize(tokens::scaled(tokens::kVorPanelMinW), tokens::scaled(tokens::kVorPanelMinH));

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                             tokens::scaled(tokens::kSpacingS),
                             tokens::scaled(tokens::kSpacingM),
                             tokens::scaled(tokens::kSpacingM));
    root->setSpacing(tokens::scaled(tokens::kSpacingS));

    // Header: mode hint left, honest lock badge right.
    auto* header = new QHBoxLayout;
    header->setContentsMargins(0, 0, 0, 0);
    header->setSpacing(tokens::scaled(tokens::kSpacingM));
    auto* hint = new QLabel(QStringLiteral("VOR 径向 · 108-118 MHz"), this);
    hint->setObjectName("monoInfo");
    header->addWidget(hint, 1);
    lockLabel_ = new QLabel(QStringLiteral("未锁定"), this);
    lockLabel_->setObjectName("monoInfo");
    header->addWidget(lockLabel_);
    root->addLayout(header);

    root->addStretch(1);   // painted dial area (paintEvent fills the rest)

    // Bottom read-out strip: hero radial number + Morse ID + quality.
    auto* row = new QHBoxLayout;
    row->setContentsMargins(0, 0, 0, 0);
    row->setSpacing(tokens::scaled(tokens::kSpacingM));
    radialLabel_ = new QLabel(QStringLiteral("—"), this);
    radialLabel_->setObjectName("panelTitle");
    QFont rf = radialLabel_->font();
    rf.setPointSizeF(tokens::kFontDisplayPt);
    rf.setBold(true);
    radialLabel_->setFont(rf);
    row->addWidget(radialLabel_);
    auto* mid = new QVBoxLayout;
    morseLabel_ = new QLabel(QStringLiteral("识别码 —"), this);
    morseLabel_->setObjectName("monoInfo");
    qualityLabel_ = new QLabel(QStringLiteral("质量 —"), this);
    qualityLabel_->setObjectName("monoInfo");
    mid->addWidget(morseLabel_);
    mid->addWidget(qualityLabel_);
    row->addLayout(mid, 1);
    root->addLayout(row);

    updateReadouts();
}

void VorPanel::setResult(const dsp::VorResult& result) {
    result_ = result;
    updateReadouts();
    update();
}

QString VorPanel::radialText() const {
    // Honest gate: unlocked -> no bearing, ever.
    if (!result_.locked) return QStringLiteral("—");
    return QStringLiteral("%1°").arg(int(result_.radialDeg + 0.5) % 360);
}

void VorPanel::updateReadouts() {
    radialLabel_->setText(radialText());
    const QColor fg = result_.locked ? QColor(QString::fromUtf8(tokens::kSuccess))
                                     : QColor(QString::fromUtf8(tokens::kTextSecondary));
    radialLabel_->setStyleSheet(QStringLiteral("color: %1;").arg(fg.name()));
    lockLabel_->setText(result_.locked ? QStringLiteral("已锁定")
                                       : QStringLiteral("未锁定"));
    lockLabel_->setStyleSheet(QStringLiteral("color: %1;").arg(fg.name()));

    const QString id = result_.morseId.trimmed();
    morseLabel_->setText(id.isEmpty() ? QStringLiteral("识别码 —")
                                      : QStringLiteral("识别码 %1").arg(id));
    qualityLabel_->setText(result_.locked
                               ? QStringLiteral("质量 %1").arg(result_.quality, 0, 'f', 2)
                               : QStringLiteral("质量 —"));
}

void VorPanel::paintEvent(QPaintEvent* /*event*/) {
    QPainter p(this);
    p.fillRect(rect(), QColor(QString::fromUtf8(tokens::kSpectrumBg)));

    const int headerH = tokens::scaled(kHeaderH);
    const int readoutH = tokens::scaled(kReadoutH);
    const int pad = tokens::scaled(kDialPad);
    QRect dial = rect().adjusted(pad, headerH + pad, -pad, -readoutH - pad);
    if (dial.width() < 20 || dial.height() < 20) return;

    const double cx = dial.center().x();
    const double cy = dial.center().y();
    const double r = std::min(dial.width(), dial.height()) / 2.0 - tokens::scaled(6);
    if (r < 10) return;

    // Compass ring + ticks. Dim when unlocked (honest "searching" state).
    const double dim = result_.locked ? 1.0 : 0.45;
    p.setPen(QPen(tokens::rgbaA(tokens::kTextAlphaTertiary * dim), 1.0));
    p.drawEllipse(QPointF(cx, cy), r, r);

    QFont f = font();
    f.setPointSizeF(tokens::kFontAuxPt);
    p.setFont(f);
    for (int deg = 0; deg < 360; deg += 10) {
        const double a = (deg - 90) * kDeg2Rad;   // 0 deg = N (top), clockwise
        const double c = std::cos(a), s = std::sin(a);
        const bool cardinal = (deg % 90 == 0);
        const double r1 = r - (cardinal ? tokens::scaled(7) : tokens::scaled(3));
        p.setPen(QPen(tokens::rgbaA((cardinal ? tokens::kTextAlphaSecondary
                                              : tokens::kTextAlphaTertiary) * dim),
                      cardinal ? 1.4 : 0.8));
        p.drawLine(QPointF(cx + r1 * c, cy + r1 * s),
                   QPointF(cx + r * c, cy + r * s));
        if (cardinal) {
            const char* label = deg == 0 ? "N" : deg == 90 ? "E"
                           : deg == 180 ? "S" : "W";
            const double lr = r - tokens::scaled(15);
            const QRectF rt(cx + lr * c - tokens::scaled(10),
                            cy + lr * s - tokens::scaled(7),
                            tokens::scaled(20), tokens::scaled(14));
            p.drawText(rt, Qt::AlignCenter, QString::fromLatin1(label));
        }
    }

    if (result_.locked) {
        // Real measured bearing: needle along radialDeg (N=up, clockwise).
        const double a = (result_.radialDeg - 90.0) * kDeg2Rad;
        const double c = std::cos(a), s = std::sin(a);
        const double tipR = r - tokens::scaled(12);
        p.setPen(QPen(QColor(QString::fromUtf8(tokens::kAccent)), 2.0));
        p.drawLine(QPointF(cx - tipR * 0.35 * c, cy - tipR * 0.35 * s),
                   QPointF(cx + tipR * c, cy + tipR * s));
        p.setBrush(QColor(QString::fromUtf8(tokens::kAccent)));
        p.setPen(Qt::NoPen);
        p.drawEllipse(QPointF(cx, cy), tokens::scaled(3), tokens::scaled(3));
    } else {
        // Honest no-lock: no fabricated bearing. Just a faint center cross.
        p.setPen(QPen(tokens::rgbaA(tokens::kTextAlphaQuaternary), 1.0));
        p.drawLine(QPointF(cx - tokens::scaled(5), cy), QPointF(cx + tokens::scaled(5), cy));
        p.drawLine(QPointF(cx, cy - tokens::scaled(5)), QPointF(cx, cy + tokens::scaled(5)));
    }
}

} // namespace ui
} // namespace mbdsdr
