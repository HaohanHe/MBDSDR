// SPDX-License-Identifier: MIT
// CW keyer: Morse encoder + timing. Unit length follows the PARIS standard
// (unit = 60/(50*wpm) seconds). Produces a deterministic key schedule that can
// drive any ICwKey (serial RTS line, CAT keying, or a sidetone generator).
#pragma once

#include "radio_link.h"

#include <QString>
#include <vector>

namespace mbdsdr {
namespace radio {

class ICwKey {
public:
    virtual ~ICwKey() = default;
    virtual void setKey(bool down) = 0;
};

struct CwEvent {
    bool down;
    double seconds;
};

class CwKeyer {
public:
    explicit CwKeyer(int wpm = 20);

    void setWpm(int wpm);
    int wpm() const { return wpm_; }
    double unitSeconds() const;  // 60/(50*wpm)

    // Morse for a character ('.'/'-'), empty for unsupported characters.
    QString morseOf(QChar c) const;

    // Deterministic key timeline for the given text (spaces = word gap).
    std::vector<CwEvent> buildSchedule(const QString& text) const;

    // Apply a schedule to a real key, sleeping between changes.
    void play(ICwKey& key, const std::vector<CwEvent>& schedule) const;

private:
    int wpm_;
};

// Key output that toggles a link's hardware key line (RTS).
class LinkCwKey : public ICwKey {
public:
    explicit LinkCwKey(IRadioLink& link) : link_(link) {}
    void setKey(bool down) override { link_.setKeyLine(down); }
private:
    IRadioLink& link_;
};

} // namespace radio
} // namespace mbdsdr
