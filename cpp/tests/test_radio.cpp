// SPDX-License-Identifier: GPL-3.0-or-later
// CAT (CI-V + Kenwood) and CW keyer self-check (not hardware).
#include "radio/cat_client.h"
#include "radio/cw_keyer.h"
#include "radio/radio_link.h"

#include <QByteArray>
#include <cmath>
#include <cstdio>

using namespace mbdsdr::radio;

static int g_failures = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { std::printf("FAIL: %s\n", msg); ++g_failures; } \
} while (0)

static QByteArray bv(std::initializer_list<int> xs) {
    QByteArray b;
    for (int x : xs) b.append(static_cast<char>(x));
    return b;
}

int main() {
    // ============ CI-V ============
    {
        MemoryRadioLink link;
        CatClient cat(link, CatClient::Protocol::CIV);
        CHECK(cat.open(), "civ link open");

        // --- set frequency 14,100,000 ---
        link.enqueue(bv({0xFE,0xFE,0x00,0xE0,0x05,0x00,0x00,0x10,0x14,0x00,0xFD}));
        CHECK(cat.setFrequencyHz(14100000.0), "civ set freq accepted");
        CHECK(link.takeWritten() ==
              bv({0xFE,0xFE,0xE0,0x00,0x05,0x00,0x00,0x10,0x14,0x00,0xFD}),
              "civ set-freq byte vector");

        // --- get frequency ---
        link.enqueue(bv({0xFE,0xFE,0x00,0xE0,0x03,0x00,0x00,0x10,0x14,0x00,0xFD}));
        double f = 0;
        CHECK(cat.getFrequencyHz(f) && f == 14100000.0, "civ get freq parse");
        link.takeWritten();

        // --- set mode USB ---
        link.enqueue(bv({0xFE,0xFE,0x00,0xE0,0x06,0x01,0x00,0xFD}));
        CHECK(cat.setMode("USB"), "civ set mode");
        const QByteArray mw = link.takeWritten();
        CHECK(mw == bv({0xFE,0xFE,0xE0,0x00,0x06,0x01,0x00,0xFD}),
              "civ set-mode USB bytes");

        // --- PTT on ---
        link.enqueue(bv({0xFE,0xFE,0x00,0xE0,0x1C,0x00,0x01,0xFD}));
        CHECK(cat.setPtt(true), "civ PTT on");
        CHECK(link.takeWritten() ==
              bv({0xFE,0xFE,0xE0,0x00,0x1C,0x00,0x01,0xFD}),
              "civ PTT bytes");

        // --- NG (0xFA) rejected ---
        link.enqueue(bv({0xFE,0xFE,0x00,0xE0,0x05,0xFA,0xFD}));
        CHECK(!cat.setFrequencyHz(999), "civ NG status rejected");
        link.takeWritten();
    }

    // ============ Kenwood ============
    {
        MemoryRadioLink link;
        CatClient cat(link, CatClient::Protocol::Kenwood);
        CHECK(cat.open(), "kw link open");

        link.enqueue(QByteArray("FA00014100000;"));
        CHECK(cat.setFrequencyHz(14100000.0), "kw set freq");
        CHECK(link.takeWritten() == QByteArray("FA00014100000;"),
              "kw set-freq string");

        link.enqueue(QByteArray("FA00014100000;"));
        double f = 0;
        CHECK(cat.getFrequencyHz(f) && f == 14100000.0, "kw get freq parse");
        link.takeWritten();

        link.enqueue(QByteArray("TX1;"));
        CHECK(cat.setPtt(true), "kw PTT on");
        CHECK(link.takeWritten() == QByteArray("TX1;"), "kw PTT string");

        link.enqueue(QByteArray("MD1;"));
        CHECK(cat.setMode("USB"), "kw set mode USB");
        CHECK(link.takeWritten() == QByteArray("MD1;"), "kw mode string");
    }

    // ============ CW ============
    {
        CwKeyer keyer(20);
        CHECK(std::fabs(keyer.unitSeconds() - 0.06) < 1e-9, "cw unit 0.06s @20wpm");
        CHECK(keyer.morseOf('E') == ".", "cw E = .");
        CHECK(keyer.morseOf('T') == "-", "cw T = -");

        // "PARIS": 43 element/letter units + 3 tail = 46 units.
        auto paris = keyer.buildSchedule("PARIS");
        double units = 0;
        for (const auto& e : paris) units += e.seconds / keyer.unitSeconds();
        CHECK(std::fabs(units - 46.0) < 1e-6, "cw PARIS 46 units (incl tail)");
        CHECK(paris.front().down && !paris.back().down, "cw starts key-down ends up");

        // Word gap between "EE" and "E" is 7 units; total = 16 units.
        auto wg = keyer.buildSchedule("EE E");
        double u2 = 0;
        bool found7 = false;
        for (const auto& e : wg) {
            const double uu = e.seconds / keyer.unitSeconds();
            u2 += uu;
            if (!e.down && std::fabs(uu - 7.0) < 1e-6) found7 = true;
        }
        CHECK(found7, "cw word gap = 7 units");
        CHECK(std::fabs(u2 - 16.0) < 1e-6, "cw EE E = 16 units");
    }

    if (g_failures == 0) std::printf("cat/cw: all checks passed\n");
    return g_failures ? 1 : 0;
}
