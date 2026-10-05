// SPDX-License-Identifier: MIT
//
// Graphical, interactive frequency-calibration wizard -- implementation.
//
// The dialog is a thin, hardware-agnostic shell over the pure
// dsp::frequency_calibrator math: it never touches the engine. A capture
// provider is injected (main window -> SpectrumEngine; tests -> a synthetic
// fixture). With no provider / no data / no reference carrier it shows an
// honest empty state instead of inventing a reading. Every appearance value
// (spacing, radius, font, color) comes from core/tokens.h.

#include "ui/calibration_dialog.h"

#include "core/tokens.h"
#include "dsp/fcch_detector.h"   // kFcchToneHz (expected baseband for GSM FCCH)

#include <QStackedWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QComboBox>
#include <QDoubleSpinBox>
#include <QLabel>
#include <QPushButton>
#include <QTimer>
#include <QFrame>
#include <QPainter>
#include <QPainterPath>
#include <QPaintEvent>
#include <QSizePolicy>

#include <cmath>

namespace mbdsdr {
namespace ui {

namespace {

// Capture length per measurement. Physical constant (not an appearance token):
// long enough to split into `segments=4` independent blocks of >=8k samples so
// the auto FFT (capped at 16k) has frequency resolution well inside 1 ppm at
// UHF. 32768 @ 2.048 MHz -> ~62.5 Hz/bin at 16k, sub-bin parabolic interpolation
// brings it to a fraction of a Hz.
constexpr int kCaptureSamples = 32768;
// Live refresh period (ms) while the user holds PTT -- slow enough that each
// tick finishes a full capture+FFT, fast enough to feel responsive.
constexpr int kLiveIntervalMs = 700;
// Preview dB window for the canvas: 0 dB at the (normalised) peak down to this
// floor. Display scale for a level-independent preview; not a hardware value.
constexpr float kPreviewFloorDb = -80.0f;

// Small live-spectrum canvas. It paints ONLY what buildSpectrumPreview hands it
// (DC-centred, normalised dB bins) plus a kAccent marker at the locked peak
// (peakFractionalBin). With no data it draws a quiet grid and nothing else --
// the dialog shows the honest empty state around it.
class LiveSpectrumCanvas : public QWidget {
public:
    explicit LiveSpectrumCanvas(QWidget* parent = nullptr) : QWidget(parent) {
        setMinimumHeight(tokens::scaled(tokens::kSpecAreaMinH));
        setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    }

    void setPreview(const dsp::SpectrumPreview& pv) {
        preview_ = pv;
        has_ = !pv.dbBins.empty();
        update();
    }
    void clear() {
        preview_ = dsp::SpectrumPreview();
        has_ = false;
        update();
    }

protected:
    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing, true);
        // kSpectrumBg #0a0c0e built numerically (QColor cannot parse rgba()).
        p.fillRect(rect(), tokens::rgbaA(1.0, 10, 12, 14));

        const int top = tokens::scaled(8);
        const int bot = height() - tokens::scaled(8);
        const double w = width();
        auto xOf = [&](double signedBin, int n) {
            return (signedBin + n / 2.0) / n * w;
        };
        // A faint DC centre hairline in every state (orientation aid).
        p.setPen(tokens::rgbaA(0.15));
        p.drawLine(QLineF(xOf(0.0, preview_.fftSize > 0 ? preview_.fftSize : 2),
                           top, xOf(0.0, preview_.fftSize > 0 ? preview_.fftSize : 2),
                           bot));

        if (!has_ || preview_.dbBins.empty()) {
            // Honest empty canvas: no trace, no fabricated peak.
            p.setPen(tokens::rgbaA(tokens::kTextAlphaQuaternary));
            QFont f = p.font();
            f.setPointSizeF(tokens::kFontAuxPt);
            p.setFont(f);
            p.drawText(rect(), Qt::AlignCenter,
                       QString::fromUtf8("等待参考载波…（离线空态，未连接设备）"));
            return;
        }

        const int n = preview_.fftSize > 0 ? preview_.fftSize
                                          : static_cast<int>(preview_.dbBins.size());
        if (n <= 0) return;
        auto yOf = [&](float db) {
            double t = (std::clamp(db, kPreviewFloorDb, 0.0f) - kPreviewFloorDb)
                       / -kPreviewFloorDb;
            return bot - t * (bot - top);
        };

        // Trace: a calm warm secondary line (signal semantics, not interactive).
        QPainterPath path;
        for (int i = 0; i < n; ++i) {
            const double sb = i - n / 2.0;
            const double x = xOf(sb, n);
            const double y = yOf(preview_.dbBins[i]);
            if (i == 0) path.moveTo(x, y);
            else path.lineTo(x, y);
        }
        QPen tracePen(tokens::rgbaA(0.55, 236, 234, 230));
        tracePen.setWidthF(1.1);
        p.setPen(tracePen);
        p.drawPath(path);

        // Locked peak marker in kAccent (instrument blue = signal semantics).
        if (preview_.peakFractionalBin >= -n / 2.0) {
            const double px = xOf(preview_.peakFractionalBin, n);
            QPen peakPen(QColor(QString::fromUtf8(tokens::kAccent)));
            peakPen.setWidthF(1.4);
            peakPen.setStyle(Qt::DashLine);
            p.setPen(peakPen);
            p.drawLine(QLineF(px, top, px, bot));
            const double py = yOf(preview_.peakDb);
            p.setBrush(QColor(QString::fromUtf8(tokens::kAccent)));
            p.setPen(Qt::NoPen);
            p.drawEllipse(QPointF(px, py), tokens::scaled(3), tokens::scaled(3));
        }
    }

private:
    dsp::SpectrumPreview preview_;
    bool has_ = false;
};

} // namespace

CalibrationDialog::CalibrationDialog(QWidget* parent) : QDialog(parent) {
    // Correction already in effect before this wizard (for the before/after).
    priorPpm_ = dsp::currentPpmSetting();
    buildUi();
}

void CalibrationDialog::setCaptureProvider(CalibrationCaptureFn fn) {
    captureFn_ = std::move(fn);
    // A provider arriving after construction can immediately offer a fresh read.
    if (stack_->currentIndex() == 3) measureOnce();
}

void CalibrationDialog::setReferenceKind(dsp::CalibrationReference ref) {
    ref_ = ref;
    if (refCombo_) {
        const int idx = static_cast<int>(ref);   // enum order == combo order
        if (refCombo_->currentIndex() != idx)
            refCombo_->setCurrentIndex(idx);
    }
    updateGuideText();
}

void CalibrationDialog::setKnownFrequencyHz(double hz) {
    knownFreqHz_ = hz;
    if (freqSpin_) freqSpin_->setValue(hz / 1.0e6);   // spinbox shows MHz
}

// --------------------------------------------------------------------- UI --
void CalibrationDialog::buildUi() {
    setWindowTitle(QString::fromUtf8("频率校准向导"));
    setMinimumSize(tokens::scaled(tokens::kSettingsMinW + tokens::kCalibMinWExtra),
                   tokens::scaled(tokens::kCalibMinH));
    setStyleSheet(tokens::buildDarkQss());

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(tokens::scaled(tokens::kSpacingXL),
                            tokens::scaled(tokens::kSpacingXL),
                            tokens::scaled(tokens::kSpacingXL),
                            tokens::scaled(tokens::kSpacingL));
    root->setSpacing(tokens::scaled(tokens::kSpacingL));

    auto* title = new QLabel(QString::fromUtf8("频率校准（晶振 ppm 误差自动测量）"), this);
    title->setObjectName(QStringLiteral("panelTitle"));
    root->addWidget(title);

    stack_ = new QStackedWidget(this);
    buildPageSource();
    buildPageFrequency();
    buildPageGuide();
    buildPageMeasure();
    buildPageApply();
    root->addWidget(stack_, 1);

    // Navigation row.
    auto* nav = new QHBoxLayout;
    nav->setSpacing(tokens::scaled(tokens::kSpacingM));
    backBtn_ = new QPushButton(QString::fromUtf8("上一步"), this);
    nextBtn_ = new QPushButton(QString::fromUtf8("下一步"), this);
    nextBtn_->setDefault(true);
    applyBtn_ = new QPushButton(QString::fromUtf8("应用并保存修正"), this);
    applyBtn_->setEnabled(false);
    nav->addWidget(backBtn_);
    nav->addStretch(1);
    nav->addWidget(applyBtn_);
    nav->addWidget(nextBtn_);
    root->addLayout(nav);

    connect(backBtn_, &QPushButton::clicked, this, &CalibrationDialog::goBack);
    connect(nextBtn_, &QPushButton::clicked, this, &CalibrationDialog::goNext);
    connect(applyBtn_, &QPushButton::clicked, this, &CalibrationDialog::applyCorrection);

    liveTimer_ = new QTimer(this);
    connect(liveTimer_, &QTimer::timeout, this, &CalibrationDialog::measureOnce);

    connect(stack_, &QStackedWidget::currentChanged, this, [this](int idx) {
        // Entering the measure page takes one synchronous reading; leaving it
        // stops the live timer.
        if (idx == 3) measureOnce();
        if (idx != 3) stopLive();
        updateNavButtons();
    });

    updateGuideText();
    updateNavButtons();
}

void CalibrationDialog::buildPageSource() {
    auto* page = new QWidget;
    auto* lay = new QVBoxLayout(page);
    lay->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM));
    lay->setSpacing(tokens::scaled(tokens::kSpacingM));

    auto* hint = new QLabel(
        QString::fromUtf8("选择一个频率已知且精确的参考信号。本能力与具体设备无关："
                          "任何 SDR、任何频率精确的信号都可用于校准。"), page);
    hint->setWordWrap(true);
    hint->setObjectName(QStringLiteral("statusHint"));
    lay->addWidget(hint);

    auto* form = new QFormLayout;
    form->setSpacing(tokens::scaled(tokens::kSpacingM));
    refCombo_ = new QComboBox(page);
    refCombo_->addItem(dsp::calibrationReferenceName(dsp::CalibrationReference::HandheldGuided));
    refCombo_->addItem(dsp::calibrationReferenceName(dsp::CalibrationReference::GsmFcch));
    refCombo_->addItem(dsp::calibrationReferenceName(dsp::CalibrationReference::Manual));
    refCombo_->setCurrentIndex(static_cast<int>(ref_));
    form->addRow(QString::fromUtf8("参考源类型"), refCombo_);
    lay->addLayout(form);
    lay->addStretch(1);

    connect(refCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int i) {
                setReferenceKind(static_cast<dsp::CalibrationReference>(i));
            });

    stack_->addWidget(page);
}

void CalibrationDialog::buildPageFrequency() {
    auto* page = new QWidget;
    auto* lay = new QVBoxLayout(page);
    lay->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM));
    lay->setSpacing(tokens::scaled(tokens::kSpacingM));

    auto* hint = new QLabel(
        QString::fromUtf8("确认或编辑参考信号的精确频率。下列频点仅为占位提示、可任意修改，"
                          "不会被保存为默认接收频率。"), page);
    hint->setWordWrap(true);
    hint->setObjectName(QStringLiteral("statusHint"));
    lay->addWidget(hint);

    auto* form = new QFormLayout;
    form->setSpacing(tokens::scaled(tokens::kSpacingM));
    freqSpin_ = new QDoubleSpinBox(page);
    // Hardware upper bound from tokens; minimum is 0 to mean "not set" (a value
    // <=1 Hz is honestly rejected by measureOnce). We deliberately do NOT pre-set
    // any frequency (409.75 / 438.5 only appear as editable placeholder hints).
    freqSpin_->setRange(0.0, tokens::kFreqMaxHz / 1.0e6);
    freqSpin_->setDecimals(4);
    freqSpin_->setSingleStep(0.001);
    freqSpin_->setSuffix(QString::fromUtf8(" MHz"));
    freqSpin_->setValue(0.0);
    form->addRow(QString::fromUtf8("已知精确频率"), freqSpin_);
    lay->addLayout(form);

    // The example anchors (409.75 / 438.5 MHz, FCCH centre) live ONLY here as
    // an editable placeholder hint -- never as a set value or preset.
    auto* freqHint = new QLabel(page);
    freqHint->setWordWrap(true);
    freqHint->setObjectName(QStringLiteral("statusHint"));
    freqHint->setText(frequencyPlaceholderHint());
    lay->addWidget(freqHint);

    connect(refCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this, freqHint](int) {
                freqHint->setText(frequencyPlaceholderHint());
            });

    connect(freqSpin_, QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, [this](double mhz) { knownFreqHz_ = mhz * 1.0e6; });

    stack_->addWidget(page);
}

void CalibrationDialog::buildPageGuide() {
    auto* page = new QWidget;
    auto* lay = new QVBoxLayout(page);
    lay->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM));
    lay->setSpacing(tokens::scaled(tokens::kSpacingM));

    guideLabel_ = new QLabel(page);
    guideLabel_->setWordWrap(true);
    guideLabel_->setMinimumHeight(tokens::scaled(tokens::kCalibReadoutMinH));
    guideLabel_->setObjectName(QStringLiteral("guideText"));
    lay->addWidget(guideLabel_);
    lay->addStretch(1);

    stack_->addWidget(page);
}

void CalibrationDialog::buildPageMeasure() {
    auto* page = new QWidget;
    auto* lay = new QVBoxLayout(page);
    lay->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM));
    lay->setSpacing(tokens::scaled(tokens::kSpacingM));

    // Spectrum card.
    spectrumHolder_ = new QFrame(page);
    spectrumHolder_->setObjectName(QStringLiteral("panelCard"));
    auto* cardLay = new QVBoxLayout(spectrumHolder_);
    cardLay->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                               tokens::scaled(tokens::kSpacingM),
                               tokens::scaled(tokens::kSpacingM),
                               tokens::scaled(tokens::kSpacingM));
    spectrumCanvas_ = new LiveSpectrumCanvas(spectrumHolder_);
    cardLay->addWidget(spectrumCanvas_);
    lay->addWidget(spectrumHolder_, 1);

    emptyLabel_ = new QLabel(page);
    emptyLabel_->setWordWrap(true);
    emptyLabel_->setObjectName(QStringLiteral("statusHint"));
    emptyLabel_->hide();
    lay->addWidget(emptyLabel_);

    // Readouts row.
    auto* readout = new QHBoxLayout;
    readout->setSpacing(tokens::scaled(tokens::kSpacingXL));
    ppmLabel_ = new QLabel(QString::fromUtf8("—"), page);
    {
        QFont f = ppmLabel_->font();
        f.setPointSizeF(tokens::kFontDisplayPt);
        f.setWeight(static_cast<QFont::Weight>(tokens::kWeightSemi));
        ppmLabel_->setFont(f);
    }
    deltaLabel_ = new QLabel(QString::fromUtf8("残余频偏 —"), page);
    deltaLabel_->setObjectName(QStringLiteral("monoInfo"));
    confidenceLabel_ = new QLabel(QString::fromUtf8("置信度 —"), page);
    confidenceLabel_->setObjectName(QStringLiteral("statusHint"));
    readout->addWidget(ppmLabel_);
    readout->addWidget(deltaLabel_);
    readout->addWidget(confidenceLabel_);
    readout->addStretch(1);
    lay->addLayout(readout);

    measureLabel_ = new QLabel(page);
    measureLabel_->setWordWrap(true);
    measureLabel_->setObjectName(QStringLiteral("statusHint"));
    lay->addWidget(measureLabel_);

    auto* liveRow = new QHBoxLayout;
    auto* liveBtn = new QPushButton(QString::fromUtf8("实时刷新"), page);
    liveBtn->setCheckable(true);
    liveRow->addWidget(liveBtn);
    liveRow->addStretch(1);
    lay->addLayout(liveRow);
    connect(liveBtn, &QPushButton::toggled, this,
            [this](bool on) { on ? startLive() : stopLive(); });

    stack_->addWidget(page);
}

void CalibrationDialog::buildPageApply() {
    auto* page = new QWidget;
    auto* lay = new QVBoxLayout(page);
    lay->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM),
                            tokens::scaled(tokens::kSpacingM));
    lay->setSpacing(tokens::scaled(tokens::kSpacingM));

    auto* head = new QLabel(
        QString::fromUtf8("应用修正前 / 后对比"), page);
    head->setObjectName(QStringLiteral("panelTitle"));
    lay->addWidget(head);

    compareLabel_ = new QLabel(
        QString::fromUtf8("尚未测得有效参考载波。请返回测量页完成一次锁定。"), page);
    compareLabel_->setWordWrap(true);
    compareLabel_->setMinimumHeight(tokens::scaled(tokens::kCalibReadoutMinH));
    compareLabel_->setObjectName(QStringLiteral("monoInfo"));
    lay->addWidget(compareLabel_);
    lay->addStretch(1);

    stack_->addWidget(page);
}

// ----------------------------------------------------------------- helpers --
QString CalibrationDialog::frequencyPlaceholderHint() const {
    switch (ref_) {
    case dsp::CalibrationReference::HandheldGuided:
        return QString::fromUtf8("例如 409.7500 MHz（占位提示，可修改）");
    case dsp::CalibrationReference::Manual:
        return QString::fromUtf8("例如 438.5000 MHz（占位提示，可修改）");
    case dsp::CalibrationReference::GsmFcch:
        return QString::fromUtf8("例如 GSM ARFCN 中心 935.000 MHz（占位提示，可修改）");
    }
    return QString::fromUtf8("");
}

void CalibrationDialog::updateGuideText() {
    if (!guideLabel_) return;
    switch (ref_) {
    case dsp::CalibrationReference::HandheldGuided:
        guideLabel_->setText(QString::fromUtf8(
            "① 将手台调到上一步确认的已知精确频率；\n"
            "② 按住 PTT 发射并保持数秒（保持纯音稳定）；\n"
            "③ 软件将在该频点锁定纯音、测量晶振频偏。\n\n"
            "松开 PTT 可停止；信号过弱时软件会如实提示「未检测到参考载波」，不会编造读数。"));
        break;
    case dsp::CalibrationReference::GsmFcch:
        guideLabel_->setText(QString::fromUtf8(
            "① 确认接收位置有 GSM 基站信号（无需操作手台）；\n"
            "② 软件将调谐到 ARFCN 中心频率，自动锁定中心上方约 67.7 kHz 的 FCCH 精确纯音；\n"
            "③ 保持接收稳定数秒即可完成测量。\n\n"
            "无 GSM 信号时软件会如实提示未检测到参考载波。"));
        break;
    case dsp::CalibrationReference::Manual:
        guideLabel_->setText(QString::fromUtf8(
            "① 用信号源 / 标准频率源发射一个频率已知且精确的连续载波；\n"
            "② 保持发射数秒；\n"
            "③ 软件将在该频点锁定纯音并测量晶振频偏。\n\n"
            "任意精确信号都可作为参考，本能力与具体电台无关。"));
        break;
    }
}

bool CalibrationDialog::captureBlock(std::vector<std::complex<float>>& out,
                                     double& sr, double& centre) {
    out.clear();
    if (!captureFn_) return false;
    bool ok = captureFn_(knownFreqHz_, kCaptureSamples, out, sr, centre);
    return ok && !out.empty();
}

void CalibrationDialog::updateNavButtons() {
    const int idx = stack_->currentIndex();
    backBtn_->setEnabled(idx > 0);
    nextBtn_->setVisible(idx < 4);
    applyBtn_->setVisible(idx == 4);
    if (idx == 4) {
        nextBtn_->setText(QString::fromUtf8("完成"));
    } else if (idx == 2) {
        nextBtn_->setText(QString::fromUtf8("开始测量"));
    } else if (idx == 3) {
        nextBtn_->setText(QString::fromUtf8("查看结果"));
    } else {
        nextBtn_->setText(QString::fromUtf8("下一步"));
    }
    // Apply is only meaningful once a carrier was actually locked.
    applyBtn_->setEnabled(idx == 4 && lastResult_.detected);
}

void CalibrationDialog::goNext() {
    const int idx = stack_->currentIndex();
    if (idx == 1) {
        // Commit the edited frequency before moving on.
        knownFreqHz_ = freqSpin_->value() * 1.0e6;
    }
    if (idx < stack_->count() - 1)
        stack_->setCurrentIndex(idx + 1);
}

void CalibrationDialog::goBack() {
    const int idx = stack_->currentIndex();
    if (idx > 0) stack_->setCurrentIndex(idx - 1);
}

void CalibrationDialog::startLive() {
    if (!liveTimer_->isActive()) {
        liveTimer_->start(kLiveIntervalMs);
        measureOnce();
    }
}
void CalibrationDialog::stopLive() {
    if (liveTimer_->isActive()) liveTimer_->stop();
}

// ------------------------------------------------------------- measurement --
void CalibrationDialog::measureOnce() {
    std::vector<std::complex<float>> iq;
    double sr = 0.0, centre = 0.0;

    // Honest empty states: no provider / no data / no frequency.
    auto showEmpty = [this](const QString& msg) {
        lastResult_ = dsp::CalibrationResult();
        ppmLabel_->setText(QString::fromUtf8("—"));
        deltaLabel_->setText(QString::fromUtf8("残余频偏 —"));
        confidenceLabel_->setText(QString::fromUtf8("置信度 —"));
        if (spectrumCanvas_) static_cast<LiveSpectrumCanvas*>(spectrumCanvas_)->clear();
        if (emptyLabel_) { emptyLabel_->setText(msg); emptyLabel_->show(); }
        if (measureLabel_) measureLabel_->setText(QString());
        updateNavButtons();
    };

    if (!captureFn_) {
        showEmpty(QString::fromUtf8("未连接设备：当前为离线空态，没有可采集的信号（不伪造读数）。"));
        return;
    }
    if (knownFreqHz_ <= 1.0) {
        showEmpty(QString::fromUtf8("尚未填写已知精确频率，请返回上一步确认参考频率。"));
        return;
    }
    if (!captureBlock(iq, sr, centre)) {
        showEmpty(QString::fromUtf8("未采集到数据：请确认设备已连接且未被占用。"));
        return;
    }

    dsp::CalibratorConfig cfg;
    cfg.centreFreqHz = (std::fabs(centre) > 1.0) ? centre : knownFreqHz_;
    // Handheld / manual: reference lands at DC; GSM FCCH: +kFcchToneHz.
    cfg.expectedBasebandHz =
        (ref_ == dsp::CalibrationReference::GsmFcch) ? dsp::kFcchToneHz : 0.0;
    cfg.thresholdDb = tokens::kPeakThresholdDefault;

    lastResult_ = dsp::calibrateFromCapture(iq, sr, ref_, cfg, 4);

    // Live preview trace (same block, level-independent normalisation).
    if (spectrumCanvas_) {
        dsp::SpectrumPreview pv = dsp::buildSpectrumPreview(iq, sr, cfg);
        static_cast<LiveSpectrumCanvas*>(spectrumCanvas_)->setPreview(pv);
    }

    if (!lastResult_.detected) {
        showEmpty(lastResult_.status.isEmpty()
                      ? QString::fromUtf8("未检测到参考载波：请确认参考源正在发射、频率正确。")
                      : lastResult_.status);
        return;
    }

    // A carrier was locked: populate readouts.
    if (emptyLabel_) emptyLabel_->hide();
    ppmLabel_->setText(QString::fromUtf8("%1 ppm").arg(lastResult_.ppm, 0, 'f', 3));
    deltaLabel_->setText(QString::fromUtf8("残余频偏 %1 Hz")
                            .arg(lastResult_.meanOffsetHz, 0, 'f', 1));
    confidenceLabel_->setText(
        QString::fromUtf8("置信度 %1% · 散布 ±%2 ppm · 最差 SNR %3 dB")
            .arg(std::lround(lastResult_.confidence * 100.0))
            .arg(lastResult_.spreadPpm, 0, 'f', 3)
            .arg(lastResult_.worstSnrDb, 0, 'f', 1));
    if (measureLabel_) measureLabel_->setText(lastResult_.status);
    updateNavButtons();
}

void CalibrationDialog::applyCorrection() {
    if (!lastResult_.detected) {
        compareLabel_->setText(
            QString::fromUtf8("尚未测得有效参考载波，未做任何修改。请先返回测量页完成锁定。"));
        return;
    }

    // The tuned centre (ppm denominator) is the frequency we tuned to; use the
    // known reference frequency for the residual prediction.
    const double centreHz = knownFreqHz_;
    predictedResidualHz_ = dsp::predictedResidualHz(
        lastResult_.meanOffsetHz, centreHz, lastResult_.ppm);

    dsp::savePpmSetting(lastResult_.ppm);
    applied_ = true;
    stopLive();

    compareLabel_->setText(
        QString::fromUtf8(
            "校准前晶振修正： %1 ppm\n"
            "本次测得并应用： %2 ppm（已保存到设置 rtl/ppm）\n"
            "校准前残余频偏： %3 Hz\n"
            "应用后预计残余频偏： %4 Hz（≈0 表示已补偿）\n\n"
            "提示：主窗口会在下次读取设备时把该 ppm 下发到 source。")
            .arg(priorPpm_, 0, 'f', 3)
            .arg(lastResult_.ppm, 0, 'f', 3)
            .arg(lastResult_.meanOffsetHz, 0, 'f', 1)
            .arg(predictedResidualHz_, 0, 'f', 2));
    updateNavButtons();
}

} // namespace ui
} // namespace mbdsdr
