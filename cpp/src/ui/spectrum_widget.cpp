// SPDX-License-Identifier: MIT
#include "spectrum_widget.h"
#include "spectrum_display.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QComboBox>
#include <QSpinBox>
#include <QLabel>
#include <QTableWidget>
#include <QHeaderView>
#include <QTableWidgetItem>
#include <QCheckBox>
#include <QPushButton>
#include <QToolButton>
#include <QSettings>
#include <algorithm>

#include "core/tokens.h"

namespace mbdsdr {
namespace ui {

SpectrumWidget::SpectrumWidget(QWidget* parent)
    : QWidget(parent)
{
    setMinimumSize(tokens::scaled(tokens::kSpectrumMinW),
                   tokens::scaled(tokens::kSpectrumMinH));
    setAutoFillBackground(true);

    auto* outer = new QVBoxLayout(this);
    outer->setContentsMargins(tokens::scaled(tokens::kSpectrumPad),
                              tokens::scaled(tokens::kSpectrumPad),
                              tokens::scaled(tokens::kSpectrumPad),
                              tokens::scaled(tokens::kSpectrumPad));
    outer->setSpacing(tokens::scaled(tokens::kSpectrumSpacing));

    // ---- Row 1: compact tool strip ---------------------------------------
    auto* topRow = new QHBoxLayout();
    topRow->setSpacing(tokens::scaled(tokens::kSpacingS));

    topRow->addWidget(new QLabel("FFT", this));
    fftCombo_ = new QComboBox(this);
    fftCombo_->addItems({"1024", "2048", "4096"});
    fftCombo_->setCurrentIndex(1);
    connect(fftCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                switch (idx) {
                    case 0: emit fftSizeRequested(1024); break;
                    case 1: emit fftSizeRequested(2048); break;
                    case 2: emit fftSizeRequested(4096); break;
                }
                emit viewChanged();
            });
    topRow->addWidget(fftCombo_);

    topRow->addWidget(new QLabel("窗", this));
    auto* winCombo = new QComboBox(this);
    winCombo->addItems({"Hann", "FlatTop", "Blackman"});
    connect(winCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) { emit windowTypeRequested(idx); });
    topRow->addWidget(winCombo);

    topRow->addWidget(new QLabel("平均", this));
    auto* avgCombo = new QComboBox(this);
    avgCombo->addItems({"Off", "Slow", "Fast"});
    connect(avgCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) { emit averageModeRequested(idx); });
    topRow->addWidget(avgCombo);

    auto* maxHoldChk = new QCheckBox("Max", this);
    connect(maxHoldChk, &QCheckBox::toggled,
            this, &SpectrumWidget::setMaxHoldEnabled);
    topRow->addWidget(maxHoldChk);
    auto* maxRst = new QPushButton("Rst", this);
    connect(maxRst, &QPushButton::clicked, this, [this]() {
        if (canvas_) canvas_->clearMaxHold();
    });
    topRow->addWidget(maxRst);
    topRow->addSpacing(tokens::scaled(tokens::kSpacingM));

    topRow->addWidget(new QLabel("dB", this));
    dbMinSpin_ = new QSpinBox(this);
    dbMinSpin_->setRange(tokens::kDbSpinLowerMin, tokens::kDbSpinLowerMax);
    dbMinSpin_->setValue(tokens::kDbLowerDefault);
    topRow->addWidget(dbMinSpin_);
    dbMaxSpin_ = new QSpinBox(this);
    dbMaxSpin_->setRange(tokens::kDbSpinUpperMin, tokens::kDbSpinUpperMax);
    dbMaxSpin_->setValue(tokens::kDbUpperDefault);
    topRow->addWidget(dbMaxSpin_);
    auto applyDb = [this]() {
        int lo = dbMinSpin_->value();
        int hi = dbMaxSpin_->value();
        if (lo >= hi) {
            hi = lo + 10;
            if (hi > tokens::kDbSpinUpperMax) { hi = tokens::kDbSpinUpperMax; lo = hi - 10; }
            dbMaxSpin_->blockSignals(true); dbMaxSpin_->setValue(hi); dbMaxSpin_->blockSignals(false);
            dbMinSpin_->blockSignals(true); dbMinSpin_->setValue(lo); dbMinSpin_->blockSignals(false);
        }
        if (canvas_) canvas_->setDbRange(static_cast<float>(lo), static_cast<float>(hi));
    };
    connect(dbMinSpin_, QOverload<int>::of(&QSpinBox::valueChanged), this, applyDb);
    connect(dbMaxSpin_, QOverload<int>::of(&QSpinBox::valueChanged), this, applyDb);
    connect(dbMinSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
            this, &SpectrumWidget::viewChanged);
    connect(dbMaxSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
            this, &SpectrumWidget::viewChanged);

    // dB axis auto-range: when checked the canvas drives its own ceiling from
    // the real sliding peak (trace + waterfall colour scale share the mapping).
    // Active state is marked with the accent colour; state is intentionally not
    // persisted (kept simple, per contract).
    auto* autoDbBtn = new QToolButton(this);
    autoDbBtn->setObjectName("btnAutoDbRange");
    autoDbBtn->setText("自动");
    autoDbBtn->setCheckable(true);
    autoDbBtn->setChecked(true);   // default on
    autoDbBtn->setAutoRaise(true);
    autoDbBtn->setToolTip("dB 轴自动量程（按真实峰值自适应）");
    auto paintAutoBtn = [autoDbBtn]() {
        if (autoDbBtn->isChecked()) {
            autoDbBtn->setStyleSheet(
                QStringLiteral("QToolButton{color:%1;} QToolButton:hover{color:%2;}")
                    .arg(QString::fromUtf8(tokens::kAccent),
                         QString::fromUtf8(tokens::kAccentHover)));
        } else {
            autoDbBtn->setStyleSheet(QString());
        }
    };
    paintAutoBtn();
    connect(autoDbBtn, &QToolButton::toggled, this, [this, paintAutoBtn](bool on) {
        if (canvas_) canvas_->setAutoRangeOn(on);
        paintAutoBtn();
    });
    topRow->addWidget(autoDbBtn);

    topRow->addWidget(new QLabel("门限", this));
    peakThreshSpin_ = new QSpinBox(this);
    peakThreshSpin_->setRange(tokens::kPeakThresholdMin, tokens::kPeakThresholdMax);
    peakThreshSpin_->setSuffix(" dB");
    {
        QSettings s("MBDSDR", "MBDSDR");
        int saved = s.value("rx/peakThresholdDb",
                            static_cast<int>(tokens::kPeakThresholdDefault)).toInt();
        saved = std::clamp(saved, tokens::kPeakThresholdMin, tokens::kPeakThresholdMax);
        peakThreshSpin_->setValue(saved);
    }
    connect(peakThreshSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
            this, [this](int v) {
        QSettings("MBDSDR", "MBDSDR").setValue("rx/peakThresholdDb", v);
        if (canvas_) canvas_->setPeakThresholdDb(static_cast<float>(v));
    });
    topRow->addWidget(peakThreshSpin_);

    infoLabel_ = new QLabel(this);
    infoLabel_->setObjectName("monoInfo");
    infoLabel_->setText(" ");
    topRow->addWidget(infoLabel_, 1);
    outer->addLayout(topRow);

    // ---- Row 2: waterfall controls + honest test-signal banner ------------
    auto* wfRow = new QHBoxLayout();
    wfRow->setSpacing(tokens::scaled(tokens::kSpacingS));
    wfRow->addWidget(new QLabel("瀑布", this));
    scrollCombo_ = new QComboBox(this);
    scrollCombo_->addItems({"1x", "2x", "4x"});
    {
        QSettings s("MBDSDR", "MBDSDR");
        int spd = s.value(tokens::kSettingsKeyScrollSpeed, 1).toInt();
        scrollCombo_->setCurrentIndex(spd == 1 ? 0 : (spd == 2 ? 1 : 2));
    }
    connect(scrollCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
        const int spd = (idx == 0 ? 1 : (idx == 1 ? 2 : 4));
        QSettings("MBDSDR", "MBDSDR").setValue(tokens::kSettingsKeyScrollSpeed, spd);
        if (canvas_) canvas_->setScrollSpeed(spd);
    });
    wfRow->addWidget(scrollCombo_);

    paletteCombo_ = new QComboBox(this);
    paletteCombo_->addItems({"经典", "单色", "Viridis"});
    {
        QSettings s("MBDSDR", "MBDSDR");
        int pal = s.value(tokens::kSettingsKeyPalette, 0).toInt();
        paletteCombo_->setCurrentIndex(std::clamp(pal, 0, 2));
    }
    connect(paletteCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
        const int pal = std::clamp(idx, 0, 2);
        QSettings("MBDSDR", "MBDSDR").setValue(tokens::kSettingsKeyPalette, pal);
        if (canvas_) canvas_->setPalette(pal);
    });
    wfRow->addWidget(paletteCombo_);
    wfRow->addSpacing(tokens::scaled(tokens::kSpacingM));

    testLabel_ = new QLabel("", this);
    testLabel_->setObjectName("testBanner");
    wfRow->addWidget(testLabel_);
    wfRow->addStretch();
    outer->addLayout(wfRow);

    // ---- Embedded unified canvas (takes the stretch) ----------------------
    canvas_ = new SpectrumDisplay(this);
    outer->addWidget(canvas_, 1);

    // Canvas -> container signal forwarding.
    connect(canvas_, &SpectrumDisplay::frequencyChanged,
            this, &SpectrumWidget::frequencyChanged);
    connect(canvas_, &SpectrumDisplay::bandwidthChanged,
            this, &SpectrumWidget::bandwidthChanged);
    connect(canvas_, &SpectrumDisplay::visibleRangeChanged,
            this, &SpectrumWidget::visibleRangeChanged);
    connect(canvas_, &SpectrumDisplay::viewChanged,
            this, &SpectrumWidget::viewChanged);
    connect(canvas_, &SpectrumDisplay::peaksUpdated,
            this, [this](const QList<mbdsdr::dsp::PeakInfo>& peaks,
                        const QList<int>& ids) {
        rebuildPeakTable(peaks, ids);
    });
    connect(canvas_, &SpectrumDisplay::vfoMarkerSelected,
            this, &SpectrumWidget::vfoMarkerSelected);
    connect(canvas_, &SpectrumDisplay::vfoMarkerCenterTuned,
            this, &SpectrumWidget::vfoMarkerCenterTuned);
    connect(canvas_, &SpectrumDisplay::vfoMarkerBandwidthChanged,
            this, &SpectrumWidget::vfoMarkerBandwidthChanged);

    // ---- Bottom peak table -------------------------------------------------
    peakTable_ = new QTableWidget(0, 4, this);
    peakTable_->setHorizontalHeaderLabels({"#", "频率(MHz)", "强度(dBFS)", "带宽(kHz)"});
    peakTable_->horizontalHeader()->setStretchLastSection(true);
    peakTable_->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    peakTable_->verticalHeader()->setVisible(false);
    peakTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    peakTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    peakTable_->setSelectionMode(QAbstractItemView::SingleSelection);
    peakTable_->setFixedHeight(tokens::scaled(tokens::kPeakTableH));
    connect(peakTable_, &QTableWidget::cellDoubleClicked,
            this, [this](int row, int) {
        auto* item = peakTable_->item(row, 1);
        if (!item) return;
        bool ok = false;
        const double mhz = item->data(Qt::UserRole).toDouble(&ok);
        if (ok && canvas_) canvas_->tuneAndCenter(mhz * 1e6);
    });
    connect(peakTable_, &QTableWidget::currentCellChanged,
            this, [this](int row, int, int, int) {
        if (canvas_) canvas_->setHighlightedPeak(row);
    });
    outer->addWidget(peakTable_);
}

void SpectrumWidget::rebuildPeakTable(const QList<mbdsdr::dsp::PeakInfo>& peaks,
                                      const QList<int>& ids) {
    peakTable_->clearSpans();
    peakTable_->setRowCount(0);

    if (peaks.isEmpty()) {
        peakTable_->setRowCount(1);
        peakTable_->setSpan(0, 0, 1, 4);
        auto* it = new QTableWidgetItem(QStringLiteral("未检测到信号"));
        it->setFlags(Qt::NoItemFlags);
        peakTable_->setItem(0, 0, it);
        return;
    }
    for (int k = 0; k < peaks.size(); ++k) {
        const auto& pk = peaks[k];
        const int row = peakTable_->rowCount();
        peakTable_->insertRow(row);
        peakTable_->setItem(row, 0,
            new QTableWidgetItem(QString::number(ids.value(k, -1))));
        auto* fItem = new QTableWidgetItem(QString("%1").arg(pk.freqHz / 1e6, 0, 'f', 3));
        fItem->setData(Qt::UserRole, pk.freqHz / 1e6);
        peakTable_->setItem(row, 1, fItem);
        peakTable_->setItem(row, 2,
            new QTableWidgetItem(QString("%1").arg(pk.dbfs, 0, 'f', 1)));
        peakTable_->setItem(row, 3,
            new QTableWidgetItem(QString("%1").arg(pk.bandwidthHz / 1e3, 0, 'f', 2)));
    }
}

void SpectrumWidget::setSpectrum(const SpectrumFrame& frame) {
    if (canvas_) canvas_->setSpectrum(frame);
    // Honest source banner: the offline fallback is explicitly NOT hardware.
    testLabel_->setText(frame.isTestSignal
        ? QStringLiteral("测试信号（非硬件） · NOT HARDWARE") : QString());
    const QString info = QString("%1  Fs=%2 MHz  F0=%3 MHz  N=%4")
                        .arg(frame.sourceName.isEmpty() ? "?" : frame.sourceName)
                        .arg(frame.sampleRateHz / 1e6, 0, 'f', 1)
                        .arg(frame.centerFreqHz / 1e6, 0, 'f', 1)
                        .arg(frame.fftSize);
    QFontMetrics fm(infoLabel_->font());
    infoLabel_->setText(fm.elidedText(info, Qt::ElideRight,
                                      qMax(50, infoLabel_->width())));
}

void SpectrumWidget::setDbRange(float minDb, float maxDb) {
    if (canvas_) canvas_->setDbRange(minDb, maxDb);
}
void SpectrumWidget::resetZoom() {
    if (canvas_) canvas_->resetZoom();
}
void SpectrumWidget::setBandwidthHz(double hz) {
    if (canvas_) canvas_->setBandwidthHz(hz);
}
double SpectrumWidget::bandwidthHz() const {
    return canvas_ ? canvas_->bandwidthHz() : 0.0;
}
void SpectrumWidget::setStepHz(double hz) {
    if (canvas_) canvas_->setStepHz(hz);
}
void SpectrumWidget::setZoomFactor(double z) {
    if (canvas_) canvas_->setZoomFactor(z);
}
double SpectrumWidget::zoomFactor() const {
    return canvas_ ? canvas_->zoomFactor() : 1.0;
}

int SpectrumWidget::dbMinValue() const {
    return dbMinSpin_ ? dbMinSpin_->value() : 0;
}
int SpectrumWidget::dbMaxValue() const {
    return dbMaxSpin_ ? dbMaxSpin_->value() : 0;
}
void SpectrumWidget::setDbSpinValues(int lo, int hi) {
    if (dbMinSpin_) dbMinSpin_->setValue(lo);
    if (dbMaxSpin_) dbMaxSpin_->setValue(hi);
    if (hi <= lo) hi = lo + 10;
    if (canvas_) canvas_->setDbRange(static_cast<float>(lo), static_cast<float>(hi));
}

int SpectrumWidget::fftSizeValue() const {
    static const int kSizes[] = {1024, 2048, 4096};
    const int idx = fftCombo_ ? fftCombo_->currentIndex() : 1;
    return kSizes[(idx >= 0 && idx <= 2) ? idx : 1];
}
void SpectrumWidget::setFftSizeValue(int n) {
    static const int kSizes[] = {1024, 2048, 4096};
    int idx = 1;
    for (int i = 0; i < 3; ++i) if (kSizes[i] == n) idx = i;
    if (fftCombo_) fftCombo_->setCurrentIndex(idx);
}

void SpectrumWidget::setMaxHoldEnabled(bool on) {
    if (canvas_) canvas_->setMaxHoldEnabled(on);
}

void SpectrumWidget::setScrollSpeed(int linesPerFrame) {
    if (canvas_) canvas_->setScrollSpeed(linesPerFrame);
}
void SpectrumWidget::setPalette(int p) {
    if (canvas_) canvas_->setPalette(p);
}

void SpectrumWidget::setVfoMarkers(const QVector<mbdsdr::dsp::VfoMarker>& markers) {
    if (canvas_) canvas_->setVfoMarkers(markers);
}

void SpectrumWidget::setNoiseFloorDb(float db) {
    if (canvas_) canvas_->setNoiseFloorDb(db);
}

} // namespace ui
} // namespace mbdsdr
