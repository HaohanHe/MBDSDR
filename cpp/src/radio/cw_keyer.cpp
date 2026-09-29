// SPDX-License-Identifier: MIT
#include "cw_keyer.h"

#include <QHash>
#include <QThread>

namespace mbdsdr {
namespace radio {

namespace {
const QHash<QChar, QString>& morseTable() {
    static const QHash<QChar, QString> t = {
        {'A',".-"},  {'B',"-..."},{'C',"-.-."},{'D',"-.."}, {'E',"."},
        {'F',"..-."},{'G',"--."}, {'H',"...."},{'I',".."},  {'J',".---"},
        {'K',"-.-"}, {'L',".-.."},{'M',"--"},  {'N',"-."},  {'O',"---"},
        {'P',".--."},{'Q',"--.-"},{'R',".-."}, {'S',"..."}, {'T',"-"},
        {'U',"..-"}, {'V',"...-"},{'W',".--"}, {'X',"-..-"},{'Y',"-.--"},
        {'Z',"--.."},
        {'0',"-----"},{'1',".----"},{'2',"..---"},{'3',"...--"},{'4',"....-"},
        {'5',"....."},{'6',"-...."},{'7',"--..."},{'8',"---.."},{'9',"----."},
        {'.',".-.-.-"},{',',"--..--"},{'?',"..--.."},{'/',"-..-."},
        {'=',"-...-"},{'+',".-.-."},{'@',".--.-."}
    };
    return t;
}
}

CwKeyer::CwKeyer(int wpm) : wpm_(wpm < 1 ? 20 : wpm) {}

void CwKeyer::setWpm(int wpm) {
    wpm_ = wpm < 1 ? 20 : wpm;
}

double CwKeyer::unitSeconds() const {
    return 60.0 / (50.0 * wpm_);
}

QString CwKeyer::morseOf(QChar c) const {
    return morseTable().value(c.toUpper());
}

std::vector<CwEvent> CwKeyer::buildSchedule(const QString& text) const {
    std::vector<CwEvent> s;
    const double u = unitSeconds();
    auto add = [&](bool down, int units) {
        if (!s.empty() && s.back().down == down)
            s.back().seconds += units * u;          // merge same-state
        else
            s.push_back({down, units * u});
    };

    const int n = text.size();
    for (int i = 0; i < n; ++i) {
        const QChar c = text[i];
        if (c == QLatin1Char(' ')) continue;        // handled as gap lookahead
        const QString m = morseOf(c);
        if (m.isEmpty()) continue;
        for (int k = 0; k < m.size(); ++k) {
            add(true, m[k] == QLatin1Char('.') ? 1 : 3);
            if (k + 1 < m.size()) add(false, 1);   // inter-element gap
        }
        // Gap to the next real character: word gap 7 if a space follows,
        // otherwise inter-letter gap 3.
        int j = i + 1;
        while (j < n && text[j] == QLatin1Char(' ')) ++j;
        if (j < n)
            add(false, (j > i + 1) ? 7 : 3);
        else
            add(false, 3);  // tail gap
        i = j - 1;          // skip consumed spaces
    }
    return s;
}

void CwKeyer::play(ICwKey& key, const std::vector<CwEvent>& schedule) const {
    for (const CwEvent& e : schedule) {
        key.setKey(e.down);
        QThread::msleep(static_cast<unsigned long>(e.seconds * 1000.0));
    }
    key.setKey(false);  // always end with the key up
}

} // namespace radio
} // namespace mbdsdr
