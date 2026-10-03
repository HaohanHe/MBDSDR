// SPDX-License-Identifier: MIT
//
// VOR radial instrument panel (right "VOR" tab).
//
// Honest data policy: the dial + read-outs paint ONLY the latest VorResult the
// engine pushes via vorRadialChanged(). The decoder's `locked` flag is the
// gate: when it is false the needle is NOT drawn, the radial number reads "—",
// and the badge says "未锁定" -- the panel never invents a bearing on noise.
// The Morse ID is whatever the 1020 Hz keying actually spells (empty = none
// decoded yet), never a baked-in station table.
//
// The compass dial is painted in paintEvent() from tokens::scaled() geometry so
// it scales with DPI and shrinks with the narrow rail instead of overflowing.
#pragma once

#include <QWidget>

#include "dsp/vor_receiver.h"

class QLabel;

namespace mbdsdr {
namespace ui {

class VorPanel : public QWidget {
    Q_OBJECT
public:
    explicit VorPanel(QWidget* parent = nullptr);

public slots:
    // Push the latest finished reading. locked=false -> honest no-lock state.
    void setResult(const dsp::VorResult& result);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    void updateReadouts();

    dsp::VorResult result_;

    QLabel* radialLabel_ = nullptr;   // big hero number ("123°" / "—")
    QLabel* lockLabel_    = nullptr;   // 已锁定 / 未锁定 badge
    QLabel* morseLabel_   = nullptr;  // station ID from 1020 Hz keying
    QLabel* qualityLabel_ = nullptr;

public:
    // Read-outs for QtTest.
    bool    locked() const       { return result_.locked; }
    double  radialDeg() const    { return result_.radialDeg; }
    QString morseId() const      { return result_.morseId; }
    QString radialText() const;  // "—" while unlocked (no fake bearing)
};

} // namespace ui
} // namespace mbdsdr
