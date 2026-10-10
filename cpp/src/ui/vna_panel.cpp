// SPDX-License-Identifier: MIT
#include "vna_panel.h"
#include "vna_panel_format.h"

#include "core/tokens.h"
#include "dsp/spectrum_engine.h"
#include "vna/nanovna_client.h"

#include <QComboBox>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QPainter>
#include <QPushButton>
#include <QSpinBox>
#include <QTimer>
#include <QVBoxLayout>

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace ui {

// ---------------------------------------------------------------------------
// VnaTraceWidget
// ---------------------------------------------------------------------------
VnaTraceWidget::VnaTraceWidget(const QString& caption, QWidget* parent)
    : QWidget(parent), caption_(caption) {
    setMinimumHeight(tokens::scaled(72));
}

void VnaTraceWidget::setData(const std::vector<double>& y) {
    y_ = y;
    update();
}

void VnaTraceWidget::clear() {
    if (!y_.empty()) { y_.clear(); update(); }
}

QSize VnaTraceWidget::sizeHint() const {
    return QSize(tokens::scaled(220), tokens::scaled(80));
}

void VnaTraceWidget::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);
    const QRectF r = rect().adjusted(2, 2, -2, -2);

    p.setPen(QPen(tokens::cardEdge(), 1.0));
    p.setBrush(tokens::card1());
    p.drawRoundedRect(r, tokens::scaled(4), tokens::scaled(4));

    if (y_.empty()) {
        p.setPen(tokens::rgbaA(tokens::kTextAlphaQuaternary));
        QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
        p.drawText(r, Qt::AlignCenter, caption_);
        return;
    }

    double lo = y_[0], hi = y_[0];
    for (double v : y_) {
        if (std::isfinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    if (!(hi > lo)) { lo -= 1.0; hi += 1.0; }
    const double span = hi - lo;
    auto yOf = [&](double v) {
        if (!std::isfinite(v)) v = lo;
        return r.bottom() - (v - lo) / span * r.height();
    };

    const int n = static_cast<int>(y_.size());
    const double pad = tokens::scaled(2.0);
    const double x0 = r.left() + pad, x1 = r.right() - pad;
    QPolygonF line;
    for (int i = 0; i < n; ++i) {
        const double x = (n == 1) ? (x0 + x1) / 2.0
                                  : x0 + (x1 - x0) * double(i) / double(n - 1);
        line << QPointF(x, yOf(y_[i]));
    }
    QColor trace(QString::fromUtf8(tokens::kRssiTrendColor));
    trace.setAlphaF(0.9);
    p.setPen(QPen(trace, 1.2));
    p.setBrush(Qt::NoBrush);
    p.drawPolyline(line);

    // axis caption (top-left, unobtrusive)
    p.setPen(tokens::rgbaA(tokens::kTextAlphaTertiary));
    QFont f = p.font(); f.setPointSizeF(tokens::kFontAuxPt); p.setFont(f);
    p.drawText(r.adjusted(4, 2, -4, -2), Qt::AlignTop | Qt::AlignLeft, caption_);
}

// ---------------------------------------------------------------------------
// VnaPanel
// ---------------------------------------------------------------------------
VnaPanel::VnaPanel(dsp::SpectrumEngine* engine, QWidget* parent)
    : QWidget(parent), engine_(engine) {
    auto* lay = new QVBoxLayout(this);
    lay->setContentsMargins(tokens::scaled(6), tokens::scaled(6),
                            tokens::scaled(6), tokens::scaled(6));
    lay->setSpacing(tokens::scaled(4));

    statusLabel_ = new QLabel(vnaStatusText(false, "", "", {}), this);
    statusLabel_->setTextFormat(Qt::PlainText);
    lay->addWidget(statusLabel_);

    auto* connRow = new QHBoxLayout;
    portEdit_ = new QLineEdit(this);
    portEdit_->setPlaceholderText(QString::fromUtf8("/dev/ttyACM0"));
    connectBtn_ = new QPushButton(QString::fromUtf8("连接"), this);
    connRow->addWidget(portEdit_, 1);
    connRow->addWidget(connectBtn_);
    lay->addLayout(connRow);

    hintLabel_ = new QLabel(vnaPortHintText(), this);
    hintLabel_->setWordWrap(true);
    QFont hf = hintLabel_->font(); hf.setPointSizeF(tokens::kFontAuxPt);
    hintLabel_->setFont(hf);
    hintLabel_->setStyleSheet(QStringLiteral("color: rgba(255,255,255,0.5);"));
    lay->addWidget(hintLabel_);

    auto* sweepForm = new QFormLayout;
    sweepForm->setLabelAlignment(Qt::AlignRight);
    startSpin_ = new QSpinBox(this); startSpin_->setRange(0, 999999999);
    startSpin_->setSingleStep(1000000); startSpin_->setValue(1000000);
    stopSpin_ = new QSpinBox(this);  stopSpin_->setRange(0, 999999999);
    stopSpin_->setSingleStep(10000000); stopSpin_->setValue(100000000);
    pointsSpin_ = new QSpinBox(this); pointsSpin_->setRange(1, 401);
    pointsSpin_->setValue(101);
    sweepForm->addRow(QString::fromUtf8("起 Hz"), startSpin_);
    sweepForm->addRow(QString::fromUtf8("止 Hz"), stopSpin_);
    sweepForm->addRow(QString::fromUtf8("点数"), pointsSpin_);
    lay->addLayout(sweepForm);

    measureBtn_ = new QPushButton(QString::fromUtf8("开始测量"), this);
    measureBtn_->setEnabled(false);  // enabled only once connected
    lay->addWidget(measureBtn_);

    s11Plot_ = new VnaTraceWidget(QString::fromUtf8("S11 VSWR"), this);
    s21Plot_ = new VnaTraceWidget(QString::fromUtf8("S21 增益 dB"), this);
    lay->addWidget(s11Plot_, 1);
    lay->addWidget(s21Plot_, 1);

    readoutLabel_ = new QLabel(vnaReadoutText(std::nan(""), 0, std::nan("")), this);
    lay->addWidget(readoutLabel_);

    timer_ = new QTimer(this);
    timer_->setInterval(1000);

    connect(connectBtn_, &QPushButton::clicked, this, &VnaPanel::onConnectClicked);
    connect(measureBtn_, &QPushButton::clicked, this, &VnaPanel::onMeasureClicked);
    connect(timer_, &QTimer::timeout, this, &VnaPanel::poll);

    refreshStatus();
}

VnaPanel::~VnaPanel() = default;

void VnaPanel::onConnectClicked() {
    if (!engine_) return;
    auto& vna = engine_->vnaClient();
    if (vna.isConnected()) {
        vna.close();
        timer_->stop();
    } else {
        const QString dev = portEdit_->text().trimmed();
        if (dev.isEmpty()) { refreshStatus(); return; }
        vna.connectSerial(dev);  // honest false on failure -> empty state stays
    }
    measureBtn_->setEnabled(vna.isConnected());
    if (vna.isConnected()) timer_->start(); else timer_->stop();
    refreshStatus();
}

void VnaPanel::onMeasureClicked() {
    if (!engine_) return;
    auto& vna = engine_->vnaClient();
    QString err;
    vna.setSweep(startSpin_->value(), stopSpin_->value(), pointsSpin_->value(), &err);
    poll();
}

void VnaPanel::applyReadout(const std::vector<long>& freqs,
                            const std::vector<std::complex<double>>& s11,
                            const std::vector<std::complex<double>>& s21) {
    std::vector<double> vswr, gain;
    vswr.reserve(s11.size());
    double best = std::nan(""); long bestHz = 0;
    for (size_t i = 0; i < s11.size(); ++i) {
        double v = vna::vswr(s11[i]);
        vswr.push_back(v);
        if (std::isfinite(v) && (!std::isfinite(best) || v < best)) {
            best = v;
            bestHz = i < freqs.size() ? freqs[i] : 0;
        }
    }
    double midGain = std::nan("");
    for (size_t i = 0; i < s21.size(); ++i)
        gain.push_back(vna::s21GainDb(s21[i]));
    if (!gain.empty()) midGain = gain[gain.size() / 2];

    s11Plot_->setData(vswr);
    s21Plot_->setData(gain);
    readoutLabel_->setText(vnaReadoutText(best, bestHz, midGain));
}

void VnaPanel::poll() {
    if (!engine_) return;
    auto& vna = engine_->vnaClient();
    if (!vna.isConnected()) return;
    std::vector<long> freqs = vna.readFrequencies();
    auto s11 = vna.readData(0);
    auto s21 = vna.readData(1);
    if (!s11.empty() || !s21.empty())
        applyReadout(freqs, s11, s21);
    refreshStatus();
}

void VnaPanel::refreshStatus() {
    if (!engine_) return;
    auto& vna = engine_->vnaClient();
    statusLabel_->setText(
        vnaStatusText(vna.isConnected(), vna.model(), vna.version(), vna.calStatus()));
    connectBtn_->setText(vna.isConnected()
                         ? QString::fromUtf8("断开")
                         : QString::fromUtf8("连接"));
}

void VnaPanel::seedSnapshotForTest(const QString& model, const QString& version,
                                   const QStringList& cal,
                                   const std::vector<long>& freqs,
                                   const std::vector<std::complex<double>>& s11,
                                   const std::vector<std::complex<double>>& s21) {
    statusLabel_->setText(vnaStatusText(true, model, version, cal));
    connectBtn_->setText(QString::fromUtf8("断开"));
    measureBtn_->setEnabled(true);
    applyReadout(freqs, s11, s21);
}

} // namespace ui
} // namespace mbdsdr
