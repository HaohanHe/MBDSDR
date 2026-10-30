// SPDX-License-Identifier: MIT
#include "spectrum_widget.h"
#include "core/app_settings.h"
#include "spectrum_display.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLayout>
#include <QLayoutItem>
#include <QRect>
#include <QComboBox>
#include <QSpinBox>
#include <QLabel>
#include <QTableWidget>
#include <QHeaderView>
#include <QTableWidgetItem>
#include <QCheckBox>
#include <QPushButton>
#include <QSlider>
#include <QToolButton>
#include <QSettings>
#include <algorithm>
#include <cmath>

#include "core/tokens.h"

namespace mbdsdr {
namespace ui {

namespace {
// FlowLayout: a left-to-right wrap layout for the compact tool strip. On wide
// windows it lays every control out on ONE row (visually identical to the old
// fixed QHBoxLayout); on a narrow window it WRAPS the overflow onto a second row
// instead of crushing the combos/buttons below their readable size -- the Phase37
// narrow-window findings 1&2 (combo value elided to "2"/"H", cursor buttons
// jammed). No new fixed pixel: row/column gaps come from the tokens passed in.
// Embedded here (not a new file) so CMakeLists stays untouched.
class FlowLayout : public QLayout {
public:
    FlowLayout(int hSpacing, int vSpacing)
        : QLayout(), m_hSpace(hSpacing), m_vSpace(vSpacing) {
        setContentsMargins(0, 0, 0, 0);
    }
    ~FlowLayout() override { while (QLayoutItem* it = takeAt(0)) delete it; }

    void addItem(QLayoutItem* item) override { m_items.append(item); }
    int count() const override { return m_items.size(); }
    QLayoutItem* itemAt(int i) const override {
        return (i >= 0 && i < m_items.size()) ? m_items.at(i) : nullptr;
    }
    QLayoutItem* takeAt(int i) override {
        if (i >= 0 && i < m_items.size()) return m_items.takeAt(i);
        return nullptr;
    }

    Qt::Orientations expandingDirections() const override { return Qt::Orientations(); }
    bool hasHeightForWidth() const override { return true; }
    int heightForWidth(int width) const override { return doLayout(QRect(0, 0, width, 0), true); }

    QSize sizeHint() const override { return minimumSize(); }
    QSize minimumSize() const override {
        QSize sz;
        for (QLayoutItem* it : m_items) sz = sz.expandedTo(it->minimumSize());
        int l, t, r, b; getContentsMargins(&l, &t, &r, &b);
        sz += QSize(l + r, t + b);
        return sz;
    }

    void setGeometry(const QRect& rect) override {
        QLayout::setGeometry(rect);
        doLayout(rect, false);
    }

private:
    int doLayout(const QRect& rect, bool testOnly) const {
        int l, t, r, b; getContentsMargins(&l, &t, &r, &b);
        const QRect area = rect.adjusted(l, t, -r, -b);
        int x = area.x();
        int y = area.y();
        int lineH = 0;
        for (QLayoutItem* it : m_items) {
            const QSize sz = it->sizeHint();
            const int nextX = x + sz.width() + m_hSpace;
            if (nextX - m_hSpace > area.right() && x > area.x()) {
                // Does not fit on this line: wrap to the next line.
                x = area.x();
                y += lineH + m_vSpace;
                lineH = 0;
            }
            if (!testOnly) it->setGeometry(QRect(QPoint(x, y), sz));
            x += sz.width() + m_hSpace;
            lineH = qMax(lineH, sz.height());
        }
        return y + lineH - rect.y() + b;
    }
    QList<QLayoutItem*> m_items;
    int m_hSpace, m_vSpace;
};
} // namespace

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

    // ---- Row 1: compact tool strip (wraps elastically on narrow windows) --
    // FlowLayout: one row when wide (standard/hidpi), wraps to a second row when
    // narrow so the combos/buttons keep their readable size instead of being
    // crushed (Phase42 narrow-window fix).
    auto* topRow = new FlowLayout(tokens::scaled(tokens::kSpacingS) + 6,
                                   tokens::scaled(tokens::kSpacingS));

    // ⚙ 弹出开关：点击展开/收起次级控件（Glass 风格：干净表面，按需展开）。
    auto* advToggle = new QToolButton(this);
    advToggle->setObjectName("advToggleBtn");
    advToggle->setText(QStringLiteral("⚙"));
    advToggle->setCheckable(true);
    advToggle->setAutoRaise(true);
    advToggle->setToolTip(QStringLiteral("展开/收起高级参数（FFT 细节/标记/游标/dB）"));
    topRow->addWidget(advToggle);
    // 次级控件容器（默认收起）。
    QList<QWidget*> advWidgets;

    topRow->addWidget(new QLabel("FFT", this));
    fftCombo_ = new QComboBox(this);
    fftCombo_->addItems({"1024", "2048", "4096"});
    fftCombo_->setCurrentIndex(1);
    // Elastic combo width: size to the LONGEST item so the closed box never
    // collapses to an elided "2" (Phase42 narrow fix). The flow layout already
    // prevents crushing; this keeps the closed read-out honest. A live tooltip
    // echoes the full current value as a belt-and-suspenders.
    fftCombo_->setSizeAdjustPolicy(QComboBox::AdjustToContents);
    connect(fftCombo_, &QComboBox::currentTextChanged,
            fftCombo_, [this](const QString& t) { fftCombo_->setToolTip(t); });
    fftCombo_->setToolTip(fftCombo_->currentText());
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
    winCombo->setSizeAdjustPolicy(QComboBox::AdjustToContents);
    connect(winCombo, &QComboBox::currentTextChanged,
            winCombo, [winCombo](const QString& t) { winCombo->setToolTip(t); });
    winCombo->setToolTip(winCombo->currentText());
    connect(winCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) { emit windowTypeRequested(idx); });
    topRow->addWidget(winCombo);

    topRow->addWidget(new QLabel("平均", this));
    auto* avgCombo = new QComboBox(this);
    avgCombo->addItems({"Off", "Slow", "Fast"});
    avgCombo->setSizeAdjustPolicy(QComboBox::AdjustToContents);
    connect(avgCombo, &QComboBox::currentTextChanged,
            avgCombo, [avgCombo](const QString& t) { avgCombo->setToolTip(t); });
    avgCombo->setToolTip(avgCombo->currentText());
    connect(avgCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) { emit averageModeRequested(idx); });
    topRow->addWidget(avgCombo);

    auto* maxHoldChk = new QCheckBox("Max", this);
    maxHoldChk->setObjectName("maxHoldChk");
    connect(maxHoldChk, &QCheckBox::toggled,
            this, &SpectrumWidget::setMaxHoldEnabled);
    advWidgets << maxHoldChk;
    topRow->addWidget(maxHoldChk);
    auto* maxRst = new QPushButton("Rst", this);
    connect(maxRst, &QPushButton::clicked, this, [this]() {
        if (canvas_) canvas_->clearMaxHold();
    });
    topRow->addWidget(maxRst);

    // ---- Peak-hold decay rate (慢/中/快) ----------------------------------
    // Merged into the existing Max/Rst peak row (zero new buttons): picks how fast
    // the held peak eases down each rendered frame. Labels read a fall-off SPEED,
    // not a raw dB number; itemData carries the honest dB/frame tier. The canvas
    // ctor ALSO reads kSettingsKeyMaxHoldDecay, so the chosen tier is honoured on
    // the very first frame even before any combo interaction; the combo is the
    // live UI reflection + the change entry point. Mirrors the depthCombo pattern:
    // setCurrentIndex BEFORE connect so construction cannot fire the lambda against
    // a not-yet-existing canvas_.
    topRow->addWidget(new QLabel("衰减", this));
    auto* decayCombo = new QComboBox(this);
    decayCombo->setObjectName("maxHoldDecayCombo");
    decayCombo->setSizeAdjustPolicy(QComboBox::AdjustToContents);
    decayCombo->addItem("慢", tokens::kMaxHoldDecaySlow);     // 0.5 dB/frame
    decayCombo->addItem("中", tokens::kMaxHoldDecayMedium);  // 1.5 dB/frame (default)
    decayCombo->addItem("快", tokens::kMaxHoldDecayFast);     // 3.0 dB/frame
    decayCombo->setToolTip("峰值保持衰减速率：慢=峰值久留，快=峰值快落（dB/帧）");
    {
        mbdsdr::core::AppSettings s;
        const double persisted = s.value(tokens::kSettingsKeyMaxHoldDecay,
                                        tokens::kMaxHoldDecayDefault).toDouble();
        int idx = 1;   // default 中 (1.5) is the middle entry
        int i = 0;
        for (float c : tokens::kMaxHoldDecayChoices) {
            if (std::abs(c - persisted) < 1e-6f) idx = i;
            ++i;
        }
        decayCombo->setCurrentIndex(idx);
    }
    connect(decayCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this, decayCombo](int idx) {
        const double db = decayCombo->itemData(idx).toDouble();
        mbdsdr::core::AppSettings().setValue(tokens::kSettingsKeyMaxHoldDecay, db);
        if (canvas_) canvas_->setMaxHoldDecayDb(static_cast<float>(db));
        emit viewChanged();
    });
    topRow->addWidget(decayCombo);

    // ---- Min-hold overlay (symmetric to Max above) ------------------------
    // A second trace overlay: the running per-bin minimum (no decay). Independent
    // of max-hold so both can be on together. Toggling it off then on re-arms a
    // fresh envelope (the canvas handles re-seeding).
    auto* minHoldChk = new QCheckBox("Min", this);
    minHoldChk->setObjectName("minHoldChk");
    minHoldChk->setToolTip("最小保持：叠加每帧最低电平包络线");
    connect(minHoldChk, &QCheckBox::toggled,
            this, &SpectrumWidget::setMinHoldEnabled);
    topRow->addWidget(minHoldChk);

    // ---- Persistence (余晖): 关/低/高 + clear ----------------------------
    topRow->addWidget(new QLabel("余晖", this));
    auto* persistCombo = new QComboBox(this);
    persistCombo->setObjectName("persistCombo");
    persistCombo->addItems({"关", "低", "高"});
    connect(persistCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
        if (canvas_) canvas_->setPersistenceMode(idx);
        mbdsdr::core::AppSettings().setValue("view/persistMode", idx);
        emit viewChanged();
    });
    topRow->addWidget(persistCombo);
    auto* persistRst = new QPushButton("清", this);
    persistRst->setToolTip("清除余晖轨迹");
    connect(persistRst, &QPushButton::clicked, this, [this]() {
        if (canvas_) canvas_->clearPersistence();
    });
    topRow->addWidget(persistRst);

    // ---- Fixed marker: add a named vertical line at the visible center ----
    auto* addMarkerBtn = new QPushButton("标记", this);
    addMarkerBtn->setObjectName("addFixedMarkerBtn");
    addMarkerBtn->setToolTip("在当前视窗中心加固定频率标记（可在 QSettings 持久化）");
    connect(addMarkerBtn, &QPushButton::clicked, this, [this]() {
        if (!canvas_) return;
        const double f = canvas_->viewCenterHz();
        int n = canvas_->fixedMarkers().size() + 1;
        canvas_->addFixedMarker(f, QString("M%1").arg(n));
        saveFixedMarkers();
    });
    auto* clearMarkerBtn = new QPushButton("清标记", this);
    connect(clearMarkerBtn, &QPushButton::clicked, this, [this]() {
        if (!canvas_) return;
        canvas_->clearFixedMarkers();
        saveFixedMarkers();
    });
    topRow->addWidget(addMarkerBtn);
    topRow->addWidget(clearMarkerBtn);

    // ---- Dual measurement cursors: place A/B at visible centre, then drag --
    auto* curABtn = new QPushButton("游标A", this);
    curABtn->setObjectName("cursorABtn");
    curABtn->setToolTip("在视窗中心放置游标 A（可拖动，显示与 B 的频差）");
    connect(curABtn, &QPushButton::clicked, this, [this]() {
        if (canvas_) canvas_->placeCursorA(canvas_->viewCenterHz());
    });
    auto* curBBtn = new QPushButton("游标B", this);
    curBBtn->setObjectName("cursorBBtn");
    curBBtn->setToolTip("在视窗中心放置游标 B（可拖动）");
    connect(curBBtn, &QPushButton::clicked, this, [this]() {
        if (canvas_) canvas_->placeCursorB(canvas_->viewCenterHz());
    });
    auto* curClr = new QPushButton("清游标", this);
    connect(curClr, &QPushButton::clicked, this, [this]() {
        if (canvas_) canvas_->clearCursors();
    });
    topRow->addWidget(curABtn);
    topRow->addWidget(curBBtn);
    topRow->addWidget(curClr);

    topRow->addWidget(new QLabel("dB", this));
    dbMinSpin_ = new QSpinBox(this);
    dbMinSpin_->setObjectName("dbMinSpin");
    dbMinSpin_->setRange(tokens::kDbSpinLowerMin, tokens::kDbSpinLowerMax);
    dbMinSpin_->setValue(tokens::kDbLowerDefault);
    topRow->addWidget(dbMinSpin_);
    dbMaxSpin_ = new QSpinBox(this);
    dbMaxSpin_->setObjectName("dbMaxSpin");
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
        if (canvas_) {
            canvas_->setAutoRangeOn(on);
            if (!on) {
                // Sync the frozen range back to the spinboxes so the UI displays
                // the range that's now locked. The eased auto ceiling becomes the
                // new manual ceiling shown in the dB spinboxes. Block signals so
                // this programmatic update does not re-trigger setDbRange().
                const int ceilVal = static_cast<int>(std::round(canvas_->currentDbCeil()));
                const int floorVal = static_cast<int>(std::round(canvas_->currentDbFloor()));
                dbMinSpin_->blockSignals(true);
                dbMaxSpin_->blockSignals(true);
                dbMinSpin_->setValue(floorVal);
                dbMaxSpin_->setValue(ceilVal);
                dbMinSpin_->blockSignals(false);
                dbMaxSpin_->blockSignals(false);
            }
        }
        paintAutoBtn();
    });
    topRow->addWidget(autoDbBtn);

    // ---- dB reference gridline density (格线 10/20/40 dB) -------------------
    // Merged into the existing dB-axis row (zero new buttons): picks the spacing
    // between horizontal dB reference lines (and y labels). The canvas ctor ALSO
    // reads kSettingsKeyDbGridStep, so the chosen density is honoured on the very
    // first frame even before any combo interaction; the combo is the live UI
    // reflection + the change entry point. Mirrors the decayCombo pattern:
    // setCurrentIndex BEFORE connect so construction cannot fire the lambda
    // against a not-yet-existing canvas_.
    topRow->addWidget(new QLabel("格线", this));
    auto* gridCombo = new QComboBox(this);
    gridCombo->setObjectName("dbGridStepCombo");
    gridCombo->setSizeAdjustPolicy(QComboBox::AdjustToContents);
    for (int step : tokens::kDbGridStepChoices)
        gridCombo->addItem(QString("%1 dB").arg(step), step);
    gridCombo->setToolTip("dB 参考格线密度：相邻水平格线（及 y 轴刻度）间距（dB）");
    {
        mbdsdr::core::AppSettings s;
        const int persisted = s.value(tokens::kSettingsKeyDbGridStep,
                                      tokens::kDbGridStepDefault).toInt();
        int idx = 1;   // default 20 dB is the middle entry of {10,20,40}
        int i = 0;
        for (int c : tokens::kDbGridStepChoices) {
            if (c == persisted) idx = i;
            ++i;
        }
        gridCombo->setCurrentIndex(idx);
    }
    connect(gridCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this, gridCombo](int idx) {
        const int step = gridCombo->itemData(idx).toInt();
        mbdsdr::core::AppSettings().setValue(tokens::kSettingsKeyDbGridStep, step);
        if (canvas_) canvas_->setDbGridStepDb(step);
        emit viewChanged();
    });
    topRow->addWidget(gridCombo);

    topRow->addWidget(new QLabel("门限", this));
    peakThreshSpin_ = new QSpinBox(this);
    peakThreshSpin_->setRange(tokens::kPeakThresholdMin, tokens::kPeakThresholdMax);
    peakThreshSpin_->setSuffix(" dB");
    {
        mbdsdr::core::AppSettings s;
        int saved = s.value("rx/peakThresholdDb",
                            static_cast<int>(tokens::kPeakThresholdDefault)).toInt();
        saved = std::clamp(saved, tokens::kPeakThresholdMin, tokens::kPeakThresholdMax);
        peakThreshSpin_->setValue(saved);
    }
    connect(peakThreshSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
            this, [this](int v) {
        mbdsdr::core::AppSettings().setValue("rx/peakThresholdDb", v);
        if (canvas_) canvas_->setPeakThresholdDb(static_cast<float>(v));
    });
    topRow->addWidget(peakThreshSpin_);

    // ---- Compact gain control (always visible in toolbar) ----
    topRow->addWidget(new QLabel(QStringLiteral("增益"), this));
    auto* gainSpin = new QSpinBox(this);
    gainSpin->setObjectName("toolbarGainSpin");
    gainSpin->setRange(static_cast<int>(tokens::kGainMinDb),
                       static_cast<int>(tokens::kGainMaxDb));
    gainSpin->setSuffix(" dB");
    gainSpin->setToolTip(QStringLiteral("接收增益（负值=RF 衰减，SDR++ 风格）"));
    connect(gainSpin, QOverload<int>::of(&QSpinBox::valueChanged),
            this, [this](int v) { emit gainRequested(static_cast<double>(v)); });
    topRow->addWidget(gainSpin);

    infoLabel_ = new QLabel(this);
    infoLabel_->setObjectName("monoInfo");
    infoLabel_->setText(" ");
    // FlowLayout ignores stretch (no right-stretch in a wrap strip); the mono
    // read-out simply sits as the last item.
    topRow->addWidget(infoLabel_);
    outer->addLayout(topRow);

    // ⚙ 弹出逻辑（Glass：干净表面，按需展开）。核心常显 6 件：
    //   [0]⚙ [1-2]FFT [3-4]窗 [5-6]平均 …… [最后-2]增益 [最后-1]info
    // 其余（Max/衰减/余晖/标记/游标/dB/格线/门限 及其文字标签）全收进 ⚙。
    // 按布局索引收集而不是 objectName——标签是匿名 QLabel，靠名字会漏。
    {
        // 核心区结束边界：'平均' combo 之后（index 7 起全是次级）。
        const int coreHeadEnd = 7;
        // 核心区开始边界：'增益' 标签（从尾部往前找，与 infoLabel_ 相邻）。
        int coreTailStart = topRow->count() - 2;  // 增益标签 / gainSpin / info
        // 兜底：从尾部回找文字为"增益"的 QLabel。
        for (int i = topRow->count() - 1; i >= 0; --i) {
            auto* item = topRow->itemAt(i);
            if (!item || !item->widget()) continue;
            auto* lab = qobject_cast<QLabel*>(item->widget());
            if (lab && lab->text() == QLatin1String("增益")) {
                coreTailStart = i;
                break;
            }
        }
        for (int i = coreHeadEnd; i < coreTailStart; ++i) {
            auto* item = topRow->itemAt(i);
            if (!item) continue;
            if (auto* w = item->widget()) {
                w->setVisible(false);
                advWidgets << w;
            } else if (auto* sp = item->spacerItem()) {
                (void)sp;  // spacer 不动
            }
        }
        connect(advToggle, &QToolButton::toggled, this,
                [advWidgets](bool on) {
                    for (QWidget* w : advWidgets) w->setVisible(on);
                });
        // 默认收起。
        advToggle->setChecked(false);
    }

    // ---- Row 2: waterfall controls + honest test-signal banner ------------
    auto* wfRow = new QHBoxLayout();
    wfRow->setSpacing(tokens::scaled(tokens::kSpacingS));
    wfRow->addWidget(new QLabel("瀑布", this));
    scrollCombo_ = new QComboBox(this);
    scrollCombo_->addItems({"1x", "2x", "4x"});
    {
        mbdsdr::core::AppSettings s;
        int spd = s.value(tokens::kSettingsKeyScrollSpeed, 1).toInt();
        scrollCombo_->setCurrentIndex(spd == 1 ? 0 : (spd == 2 ? 1 : 2));
    }
    connect(scrollCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
        const int spd = (idx == 0 ? 1 : (idx == 1 ? 2 : 4));
        mbdsdr::core::AppSettings().setValue(tokens::kSettingsKeyScrollSpeed, spd);
        if (canvas_) canvas_->setScrollSpeed(spd);
    });
    wfRow->addWidget(scrollCombo_);

    paletteCombo_ = new QComboBox(this);
    paletteCombo_->setObjectName("paletteCombo");
    paletteCombo_->addItems({"经典", "单色", "Viridis"});
    {
        mbdsdr::core::AppSettings s;
        int pal = s.value(tokens::kSettingsKeyPalette, 0).toInt();
        paletteCombo_->setCurrentIndex(std::clamp(pal, 0, 2));
    }
    connect(paletteCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
        const int pal = std::clamp(idx, 0, 2);
        mbdsdr::core::AppSettings().setValue(tokens::kSettingsKeyPalette, pal);
        if (canvas_) canvas_->setPalette(pal);
    });
    wfRow->addWidget(paletteCombo_);

    // Waterfall history depth (rows): rebuilds the ring to 128/256/512. The
    // persisted value is read here into the combo (unset/illegal -> default 256).
    // The canvas ctor ALSO reads kSettingsKeyWfDepth, so the depth is honoured on
    // the very first frame even before any combo interaction; the combo is the
    // live UI reflection + the change entry point. Mirrors the scroll/palette
    // pattern: setCurrentIndex BEFORE connect so construction cannot fire the
    // lambda against a not-yet-existing canvas_.
    depthCombo_ = new QComboBox(this);
    for (int d : tokens::kWaterfallDepthChoices)
        depthCombo_->addItem(QString("%1 行").arg(d), d);
    {
        mbdsdr::core::AppSettings s;
        int depth = s.value(tokens::kSettingsKeyWfDepth,
                            tokens::kWaterfallDepthDefault).toInt();
        int idx = 1;   // default 256 is the middle entry of {128,256,512}
        int i = 0;
        for (int c : tokens::kWaterfallDepthChoices) {
            if (c == depth) idx = i;
            ++i;
        }
        depthCombo_->setCurrentIndex(idx);
    }
    connect(depthCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
        const int depth = depthCombo_->itemData(idx).toInt();
        mbdsdr::core::AppSettings().setValue(tokens::kSettingsKeyWfDepth, depth);
        if (canvas_) canvas_->setRingDepth(depth);
    });
    wfRow->addWidget(depthCombo_);

    // ---- Waterfall scroll pause/resume (SDR++ waterfall "scroll pause") ----
    // Checkable toolbutton merged into the existing 瀑布 row (zero new layout).
    // ON freezes the waterfall DISPLAY on a snapshot: the spectrum trace keeps
    // updating live and real frames keep entering the history ring (no data
    // loss) -- this pauses the scroll, never the measurement. Persisted under
    // kSettingsKeyWfScrollPaused; the canvas ctor ALSO reads it, so the very
    // first paint honours the persisted state. setChecked BEFORE connect so
    // construction cannot fire the lambda against a not-yet-existing canvas_.
    auto* wfPauseBtn = new QToolButton(this);
    wfPauseBtn->setObjectName("wfPauseBtn");
    wfPauseBtn->setText("暂停滚动");
    wfPauseBtn->setCheckable(true);
    wfPauseBtn->setAutoRaise(true);
    wfPauseBtn->setToolTip(
        "暂停瀑布滚动：显示冻结在当前帧（频谱 trace 仍实时更新，真实数据继续入历史缓冲）");
    wfPauseBtn->setChecked(
        mbdsdr::core::AppSettings()
            .value(tokens::kSettingsKeyWfScrollPaused, false).toBool());
    auto paintWfPauseBtn = [wfPauseBtn]() {
        if (wfPauseBtn->isChecked()) {
            wfPauseBtn->setStyleSheet(
                QStringLiteral("QToolButton{color:%1;} QToolButton:hover{color:%2;}")
                    .arg(QString::fromUtf8(tokens::kAccent),
                         QString::fromUtf8(tokens::kAccentHover)));
        } else {
            wfPauseBtn->setStyleSheet(QString());
        }
    };
    paintWfPauseBtn();
    connect(wfPauseBtn, &QToolButton::toggled, this, [this, paintWfPauseBtn](bool on) {
        mbdsdr::core::AppSettings()
            .setValue(tokens::kSettingsKeyWfScrollPaused, on);
        if (canvas_) canvas_->setScrollPaused(on);
        paintWfPauseBtn();
    });
    wfRow->addWidget(wfPauseBtn);

    wfRow->addSpacing(tokens::scaled(tokens::kSpacingM));

    testLabel_ = new QLabel("", this);
    testLabel_->setObjectName("testBanner");
    wfRow->addWidget(testLabel_);
    wfRow->addStretch();
    outer->addLayout(wfRow);

    // ---- Embedded unified canvas (takes the stretch) ----------------------
    canvas_ = new SpectrumDisplay(this);
    // ---- 阶段 B1: right-edge vertical rail (缩放 / 上限 / 下限) ------------
    // SDR++ pins the three highest-frequency display controls to the right edge
    // of the canvas as vertical sliders: "广度"(span/zoom) and the dB window
    // "高度"(上下限). They own NO state of their own -- each drives the same
    // canvas API / toolbar spinbox the settings dialog and saveUiState use, so
    // one value keeps exactly one truth. The rail can be switched off in
    // 设置 → 界面 (ui/edgeRailVisible).
    {
        auto* canvasRow = new QHBoxLayout();
        canvasRow->setContentsMargins(0, 0, 0, 0);
        canvasRow->setSpacing(tokens::scaled(tokens::kSpacingS));
        canvasRow->addWidget(canvas_, 1);

        edgeRail_ = new QWidget(this);
        edgeRail_->setObjectName("spectrumEdgeRail");
        edgeRail_->setFixedWidth(tokens::scaled(tokens::kEdgeRailW));
        auto* railLay = new QVBoxLayout(edgeRail_);
        railLay->setContentsMargins(0, 0, 0, 0);
        railLay->setSpacing(tokens::scaled(tokens::kSpacingS));

        auto addRailSlider = [this, railLay](const QString& objectName,
                                             const QString& caption,
                                             const QString& tip,
                                             QSlider*& slider, QLabel*& value,
                                             int lo, int hi, int val) {
            auto* cap = new QLabel(caption, edgeRail_);
            cap->setAlignment(Qt::AlignCenter);
            cap->setToolTip(tip);
            slider = new QSlider(Qt::Vertical, edgeRail_);
            slider->setObjectName(objectName);
            slider->setRange(lo, hi);
            slider->setValue(std::clamp(val, lo, hi));
            slider->setToolTip(tip);
            slider->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Expanding);
            value = new QLabel(edgeRail_);
            value->setObjectName(objectName + QStringLiteral("Value"));
            value->setAlignment(Qt::AlignCenter);
            value->setToolTip(tip);
            railLay->addWidget(cap);
            railLay->addWidget(slider, 1);
            railLay->addWidget(value);
        };
        const int zoomSteps = int(std::lround((tokens::kZoomMax - tokens::kZoomMin)
                                              / tokens::kZoomSliderStep));
        addRailSlider(QStringLiteral("edgeRailZoom"), QString::fromUtf8("缩放"),
                      QString::fromUtf8("可视广度：上=放大（窄 span），下=缩小（宽 span）"),
                      zoomSlider_, zoomValue_, 0, zoomSteps, 0);
        addRailSlider(QStringLiteral("edgeRailDbMax"), QString::fromUtf8("上限"),
                      QString::fromUtf8("显示上限 dB：谱线顶到哪（信号高度上端）"),
                      dbMaxSlider_, dbMaxValue_, tokens::kDbSpinUpperMin,
                      tokens::kDbSpinUpperMax, tokens::kDbUpperDefault);
        addRailSlider(QStringLiteral("edgeRailDbMin"), QString::fromUtf8("下限"),
                      QString::fromUtf8("显示下限 dB：噪底落在哪（信号高度下端）"),
                      dbMinSlider_, dbMinValue_, tokens::kDbSpinLowerMin,
                      tokens::kDbSpinLowerMax, tokens::kDbLowerDefault);
        canvasRow->addWidget(edgeRail_);
        outer->addLayout(canvasRow, 1);

        // Wiring: every rail slider drives the ONE canonical handle (the canvas
        // for zoom, the toolbar spinbox for the dB window) -- never a private
        // copy of the value. The spinbox path in turn applies the range to the
        // canvas and emits viewChanged, so persistence stays single-sourced.
        connect(zoomSlider_, &QSlider::valueChanged, this, [this](int v) {
            const double z = tokens::kZoomMin
                             + static_cast<double>(v) * tokens::kZoomSliderStep;
            if (canvas_) canvas_->setZoomFactor(z);
            if (zoomValue_)
                zoomValue_->setText(QStringLiteral("%1x").arg(z, 0, 'f', 1));
            emit viewChanged();
        });
        connect(dbMaxSlider_, &QSlider::valueChanged, this, [this](int v) {
            if (dbMaxSpin_) dbMaxSpin_->setValue(v);
        });
        connect(dbMinSlider_, &QSlider::valueChanged, this, [this](int v) {
            if (dbMinSpin_) dbMinSpin_->setValue(v);
        });
        // Reverse direction: the rail follows whenever the value is moved from
        // anywhere else (settings dialog / restore / ControlHub).
        connect(dbMinSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
                this, [this](int) { syncEdgeRail(); });
        connect(dbMaxSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
                this, [this](int) { syncEdgeRail(); });
        syncEdgeRail();
        // Visibility: read at construction so the very first paint honours it
        // (the settings dialog 界面 tab and setEdgeRailVisible write the key).
        setEdgeRailVisible(
            mbdsdr::core::AppSettings()
                .value(tokens::kSettingsKeyEdgeRailVisible,
                       tokens::kEdgeRailDefaultVisible).toBool());
    }

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
    // Fixed-marker edits (drag / nudge / delete) persist immediately.
    connect(canvas_, &SpectrumDisplay::fixedMarkersEdited,
            this, &SpectrumWidget::saveFixedMarkers);
    canvas_->setFocusPolicy(Qt::StrongFocus);

    // ---- Bottom peak table (auto signal list) ------------------------------
    // Columns: # | frequency (MHz) | power (dBFS) | offset from dial (Δ kHz).
    // The Δ column is the signed distance of the carrier from the tuned
    // frequency -- a single click tunes the receiver there (peakTuned).
    peakTable_ = new QTableWidget(0, 4, this);
    peakTable_->setObjectName("peakTable");
    peakTable_->setHorizontalHeaderLabels({"#", "频率(MHz)", "强度(dBFS)", "Δ(kHz)"});
    peakTable_->horizontalHeader()->setStretchLastSection(true);
    peakTable_->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    peakTable_->verticalHeader()->setVisible(false);
    peakTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    peakTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    peakTable_->setSelectionMode(QAbstractItemView::SingleSelection);
    peakTable_->setFixedHeight(tokens::scaled(tokens::kPeakTableH));
    // A single click on a row tunes to that carrier (SDR++ signal-list click);
    // a double click does the same (kept as an alias for power users).
    connect(peakTable_, &QTableWidget::cellClicked,
            this, [this](int row, int) { activatePeakRow(row); });
    connect(peakTable_, &QTableWidget::cellDoubleClicked,
            this, [this](int row, int) { activatePeakRow(row); });
    connect(peakTable_, &QTableWidget::currentCellChanged,
            this, [this](int row, int, int, int) {
        if (canvas_) canvas_->setHighlightedPeak(row);
    });
    outer->addWidget(peakTable_);
    // 默认隐藏峰值表（用户反馈：底部这行没有明确用途，占空间）。
    // 通过快捷键 Ctrl+P 或菜单可显示。
    peakTable_->hide();

    // Restore persisted fixed markers (default empty) and the last persist mode.
    loadFixedMarkers();
    {
        mbdsdr::core::AppSettings s;
        const int pm = s.value("view/persistMode", 0).toInt();
        if (auto* c = findChild<QComboBox*>("persistCombo"))
            c->setCurrentIndex(std::clamp(pm, 0, 2));
    }
}

void SpectrumWidget::saveFixedMarkers() {
    if (!canvas_) return;
    mbdsdr::core::AppSettings s;
    QVariantList list;
    for (const auto& m : canvas_->fixedMarkers())
        list.append(QVariantMap{{"freqHz", m.freqHz}, {"name", m.name}});
    s.setValue("view/fixedMarkers", list);
}

void SpectrumWidget::loadFixedMarkers() {
    if (!canvas_) return;
    mbdsdr::core::AppSettings s;
    const QVariantList list = s.value("view/fixedMarkers").toList();
    QVector<SpectrumDisplay::FixedMarker> out;
    for (const QVariant& v : list) {
        const QVariantMap m = v.toMap();
        SpectrumDisplay::FixedMarker fm;
        fm.freqHz = m.value("freqHz").toDouble();
        fm.name   = m.value("name").toString();
        out.append(fm);
    }
    canvas_->setFixedMarkers(out);
}

void SpectrumWidget::rebuildPeakTable(const QList<mbdsdr::dsp::PeakInfo>& peaks,
                                      const QList<int>& ids) {
    peakTable_->clearSpans();
    peakTable_->setRowCount(0);

    if (peaks.isEmpty()) {
        // Honest empty state: a pure-noise / no-frame band shows one spanned row
        // saying there is nothing above the detection gate -- never a blank grid
        // and never an invented row.
        peakTable_->setRowCount(1);
        peakTable_->setSpan(0, 0, 1, 4);
        auto* it = new QTableWidgetItem(QStringLiteral("无信号峰值（纯噪声）"));
        it->setTextAlignment(Qt::AlignCenter);
        it->setFlags(Qt::NoItemFlags);
        peakTable_->setItem(0, 0, it);
        return;
    }
    // Authoritative rows (with the signed Δ vs the tuned frequency) come from
    // the canvas's matured PeakEntry list, which is in the SAME order as the
    // (peaks, ids) signal this was called with.
    const std::vector<ui::SpectrumDisplay::PeakEntry> entries =
        canvas_ ? canvas_->peaks() : std::vector<ui::SpectrumDisplay::PeakEntry>{};
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
        // Signed offset from the tuned frequency, in kHz. Falls back to 0 when
        // the authoritative entry is unavailable (size-mismatch guard).
        const double dkHz = (k < static_cast<int>(entries.size()))
                                ? entries[k].deltaHz / 1e3 : 0.0;
        peakTable_->setItem(row, 3,
            new QTableWidgetItem(QString("%1%2").arg(dkHz >= 0 ? "+" : "")
                                                    .arg(dkHz, 0, 'f', 1)));
    }
}

void SpectrumWidget::activatePeakRow(int row) {
    if (!canvas_) return;
    if (row < 0) return;
    auto* item = peakTable_->item(row, 1);
    if (!item) return;
    bool ok = false;
    const double mhz = item->data(Qt::UserRole).toDouble(&ok);
    if (!ok) return;
    const double hz = mhz * 1e6;
    canvas_->tuneAndCenter(hz);   // recentre the view locally (no engine call)
    emit peakTuned(hz);           // the container retunes the selected VFO
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
void SpectrumWidget::followTunedFrequency(double hz) {
    if (canvas_) canvas_->followTunedFrequency(hz);
}
void SpectrumWidget::setZoomFactor(double z) {
    if (canvas_) canvas_->setZoomFactor(z);
    syncEdgeRail();   // the rail is a handle on the same value, not a second copy
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
    syncEdgeRail();
}

// ---- 阶段 B1: right-edge rail --------------------------------------------
void SpectrumWidget::setEdgeRailVisible(bool on) {
    edgeRailOn_ = on;
    if (edgeRail_) edgeRail_->setVisible(on);
    // Written straight through so hiding the rail survives a restart even if
    // the settings dialog is never opened (same key the dialog uses).
    mbdsdr::core::AppSettings().setValue(tokens::kSettingsKeyEdgeRailVisible, on);
}
bool SpectrumWidget::edgeRailVisible() const {
    return edgeRailOn_;
}

// Pull the rail handles back in line with the canonical values (zoom factor +
// the two dB spinboxes). Signals are blocked so this can never re-enter the
// sliders' own handlers.
void SpectrumWidget::syncEdgeRail() {
    if (!edgeRail_) return;
    if (zoomSlider_) {
        const double z = canvas_ ? canvas_->zoomFactor() : tokens::kZoomMin;
        const int v = std::clamp(
            int(std::lround((z - tokens::kZoomMin) / tokens::kZoomSliderStep)),
            zoomSlider_->minimum(), zoomSlider_->maximum());
        const QSignalBlocker blk(zoomSlider_);
        zoomSlider_->setValue(v);
        if (zoomValue_)
            zoomValue_->setText(QStringLiteral("%1x").arg(z, 0, 'f', 1));
    }
    if (dbMinSlider_ && dbMinSpin_) {
        const QSignalBlocker blk(dbMinSlider_);
        dbMinSlider_->setValue(std::clamp(dbMinSpin_->value(),
                                          dbMinSlider_->minimum(),
                                          dbMinSlider_->maximum()));
        if (dbMinValue_) dbMinValue_->setText(QString::number(dbMinSpin_->value()));
    }
    if (dbMaxSlider_ && dbMaxSpin_) {
        const QSignalBlocker blk(dbMaxSlider_);
        dbMaxSlider_->setValue(std::clamp(dbMaxSpin_->value(),
                                          dbMaxSlider_->minimum(),
                                          dbMaxSlider_->maximum()));
        if (dbMaxValue_) dbMaxValue_->setText(QString::number(dbMaxSpin_->value()));
    }
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

// ---- phase82: programmatic mirrors of the toolbar combos (settings dialog).
// Push into the SAME combo the toolbar lambda uses so the toolbar stays the
// live read-out and the two paths cannot drift.
void SpectrumWidget::setPersistMode(int idx) {
    if (auto* c = findChild<QComboBox*>("persistCombo")) {
        c->blockSignals(true);
        c->setCurrentIndex(std::clamp(idx, 0, 2));
        c->blockSignals(false);
    }
    if (canvas_) canvas_->setPersistenceMode(std::clamp(idx, 0, 2));
    mbdsdr::core::AppSettings().setValue("view/persistMode", std::clamp(idx, 0, 2));
}

void SpectrumWidget::setWaterfallDepth(int rows) {
    if (depthCombo_) {
        int idx = -1;
        for (int i = 0; i < depthCombo_->count(); ++i) {
            if (depthCombo_->itemData(i).toInt() == rows) { idx = i; break; }
        }
        if (idx >= 0) {
            depthCombo_->blockSignals(true);
            depthCombo_->setCurrentIndex(idx);
            depthCombo_->blockSignals(false);
        }
    }
    if (canvas_) canvas_->setRingDepth(rows);
    mbdsdr::core::AppSettings().setValue(tokens::kSettingsKeyWfDepth, rows);
}

// ---- D1 reverse-audit: mirrors in the SAME phase82 paradigm. Each one pushes
// into the SAME toolbar control the user touches, WITHOUT blocking signals: the
// existing change handler persists the key and applies to the canvas, so the
// control stays the single source of truth. A value with no matching item is an
// honest no-op (never snap to a different tier).
void SpectrumWidget::setDbGridStepDb(int stepDb) {
    if (auto* c = findChild<QComboBox*>("dbGridStepCombo")) {
        for (int i = 0; i < c->count(); ++i) {
            if (c->itemData(i).toInt() == stepDb) {
                c->setCurrentIndex(i);   // fires the existing handler
                return;
            }
        }
    }
}

void SpectrumWidget::setPeakThresholdDb(int db) {
    // Member spinbox: setValue fires the valueChanged handler (key + canvas);
    // the widget's own 5..40 range clamps out-of-band values as always.
    if (peakThreshSpin_) peakThreshSpin_->setValue(db);
}

void SpectrumWidget::setMaxHoldDecayDb(double dbPerFrame) {
    if (auto* c = findChild<QComboBox*>("maxHoldDecayCombo")) {
        for (int i = 0; i < c->count(); ++i) {
            if (std::abs(c->itemData(i).toDouble() - dbPerFrame) < 1e-6) {
                c->setCurrentIndex(i);   // fires the existing handler
                return;
            }
        }
    }
}

void SpectrumWidget::setMaxHoldEnabled(bool on) {
    if (canvas_) canvas_->setMaxHoldEnabled(on);
}

void SpectrumWidget::setMinHoldEnabled(bool on) {
    if (canvas_) canvas_->setMinHoldEnabled(on);
}

void SpectrumWidget::setScrollSpeed(int linesPerFrame) {
    if (canvas_) canvas_->setScrollSpeed(linesPerFrame);
}
void SpectrumWidget::setPalette(int p) {
    if (canvas_) canvas_->setPalette(p);
}

void SpectrumWidget::setCustomPalette(const QString& absPath) {
    if (!canvas_) return;
    QString err;
    if (!canvas_->loadColormapFromFile(absPath, &err)) {
        qWarning() << "[mbdsdr] setCustomPalette 加载失败:" << err;
        return;
    }
    // 同步下拉：追加"自定义"项并选中（QSignalBlocker 防回环触发 setPalette）。
    if (paletteCombo_) {
        if (paletteCombo_->findText(QStringLiteral("自定义")) < 0)
            paletteCombo_->addItem(QStringLiteral("自定义"));
        const int idx = paletteCombo_->findText(QStringLiteral("自定义"));
        const QSignalBlocker block(paletteCombo_);
        paletteCombo_->setCurrentIndex(idx);
    }
}

void SpectrumWidget::setVfoMarkers(const QVector<mbdsdr::dsp::VfoMarker>& markers) {
    if (canvas_) canvas_->setVfoMarkers(markers);
}

void SpectrumWidget::setNoiseFloorDb(float db) {
    if (canvas_) canvas_->setNoiseFloorDb(db);
}

} // namespace ui
} // namespace mbdsdr
