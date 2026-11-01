// SPDX-License-Identifier: MIT
#include "freq_display_widget.h"

#include <QFontMetrics>
#include <QMouseEvent>
#include <QPainter>
#include <QWheelEvent>

#include "core/tokens.h"

namespace mbdsdr {
namespace ui {

namespace {
// "DDD.DDD.DDD" layout: digit slots at even indices of an 11-char string
// (indices 0,1,2, 4,5,6, 8,9,10 -- the dots sit at 3 and 7).
inline int charIndexForDigit(int digitIndex) {
    return digitIndex + (digitIndex / 3);  // +1 dot before each group after the first
}
inline int digitIndexForChar(int charIndex) {
    if (charIndex == 3 || charIndex == 7) return -1;  // separator dots
    return charIndex - (charIndex / 4);               // 0,1,2, 4,5,6, 8,9,10 -> 0..8
}

// Measured geometry of the 11-char display string. The digit advance and the
// dot advance can DIFFER (the style-hinted font is not guaranteed monospace),
// so painting, sizing and hit-testing all walk this one shared layout --
// assuming one advance for every char overflows the widget by ~2 digits.
struct FreqTextLayout {
    int x[11];        // x offset of each char, relative to the text origin
    int advance[11];  // width reserved for each char
    int width;        // total content width
};
FreqTextLayout freqTextLayout(const QFontMetrics& fm) {
    const int chW  = fm.horizontalAdvance(QLatin1Char('0'));
    const int dotW = fm.horizontalAdvance(QLatin1Char('.'));
    FreqTextLayout L;
    int x = 0;
    for (int i = 0; i < 11; ++i) {
        const bool isDot = (i == 3 || i == 7);
        L.x[i] = x;
        L.advance[i] = isDot ? dotW : chW;
        x += L.advance[i];
    }
    L.width = x;
    return L;
}
constexpr int kTextOriginX = 8;   // left padding before the first digit
}  // namespace

FreqDisplayWidget::FreqDisplayWidget(QWidget* parent) : QWidget(parent) {
    setObjectName(QStringLiteral("bigFreqLabel"));
    setToolTip(QStringLiteral(
        "调谐频率（MHz）。点击数字：上半+1 下半-1（按位）；滚轮按位步进；\n"
        "右键清该位及以下。前导 0 灰显。"));
    digitFont_ = font();
    digitFont_.setPointSizeF(tokens::scaled(28) * 72.0 / 96.0);
    digitFont_.setBold(true);
    digitFont_.setStyleHint(QFont::Monospace);
    setFont(digitFont_);
    // Width must cover the PAINTED extent (9 digits + 2 dots, each at its real
    // advance), not the advance of the string -- on a non-monospace font the
    // per-char sum is wider and the last digit painted past the widget edge.
    setMinimumWidth(freqTextLayout(QFontMetrics(digitFont_)).width + 2 * kTextOriginX);
    setMinimumHeight(QFontMetrics(digitFont_).height() + 8);
    setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
    setCursor(Qt::PointingHandCursor);
}

void FreqDisplayWidget::setFrequency(qint64 hz) {
    if (hz == freqHz_) return;
    freqHz_ = hz;
    update();
}

void FreqDisplayWidget::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setFont(digitFont_);
    const QFontMetrics fm(digitFont_);
    const int textY = (height() + fm.ascent() - fm.descent()) / 2;

    // Render DDD.DDD.DDD digit-by-digit so leading zeros can dim.
    // A zero is "leading" while no non-zero digit has been emitted yet AND we
    // have not yet reached the units digit (the last digit never dims -- a
    // fully-zero frequency still shows one bright 0).
    const qint64 hz = freqHz_;
    qint64 rest = hz < 0 ? 0 : hz;  // display clamps negative to 0
    int digits[kDigitCount];
    for (int i = 0; i < kDigitCount; ++i) {
        const qint64 pv = placeValueHz(i);
        digits[i] = static_cast<int>(rest / pv);
        rest %= pv;
    }
    bool seenSignificant = false;
    const QColor bright(QString::fromUtf8(tokens::kAccent));
    const QColor dim = bright.darker(280);
    const FreqTextLayout L = freqTextLayout(fm);
    int charIdx = 0;
    for (int i = 0; i < kDigitCount; ++i) {
        if (digits[i] != 0) seenSignificant = true;
        const bool isLast = (i == kDigitCount - 1);
        p.setPen((seenSignificant || isLast) ? bright : dim);
        p.drawText(kTextOriginX + L.x[charIdx], textY, QString::number(digits[i]));
        ++charIdx;
        if (i == 2 || i == 5) {  // group separators after 3rd and 6th digit
            p.setPen(dim);
            p.drawText(kTextOriginX + L.x[charIdx], textY, QStringLiteral("."));
            ++charIdx;
        }
    }
}

int FreqDisplayWidget::digitAtX(int x) const {
    const FreqTextLayout L = freqTextLayout(QFontMetrics(digitFont_));
    const int pos = x - kTextOriginX;
    if (pos < 0) return -1;
    // Walk the shared layout (dots are narrower than digits on non-monospace
    // fonts, so a uniform-divide hit test lands on the wrong digit).
    for (int i = 0; i < 11; ++i) {
        if (pos < L.x[i] + L.advance[i])
            return digitIndexForChar(i);
    }
    return -1;
}

void FreqDisplayWidget::applyDigitDelta(int digitIndex, int dir) {
    if (digitIndex < 0 || digitIndex >= kDigitCount) return;
    const qint64 next = nudgeDigit(freqHz_, digitIndex, dir, minHz_, maxHz_);
    if (next == freqHz_) return;
    freqHz_ = next;
    update();
    emit frequencyEdited(freqHz_);
}

void FreqDisplayWidget::mousePressEvent(QMouseEvent* e) {
    const int digit = digitAtX(e->pos().x());
    if (digit < 0) return;
    if (e->button() == Qt::RightButton) {
        const qint64 next = truncateBelow(freqHz_, digit);
        if (next == freqHz_) return;
        freqHz_ = next;
        update();
        emit frequencyEdited(freqHz_);
        return;
    }
    if (e->button() != Qt::LeftButton) return;
    // Upper half of the cell: +1; lower half: -1 at that place value.
    const int dir = (e->pos().y() < height() / 2) ? +1 : -1;
    applyDigitDelta(digit, dir);
}

void FreqDisplayWidget::wheelEvent(QWheelEvent* e) {
    const int digit = digitAtX(e->position().x());
    if (digit < 0) return;
    const int dir = (e->angleDelta().y() > 0) ? +1 : -1;
    applyDigitDelta(digit, dir);
    e->accept();
}

}  // namespace ui
}  // namespace mbdsdr
