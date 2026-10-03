// SPDX-License-Identifier: MIT
//
// QtTest (offscreen) for the Phase18 Wave2 decoder panels.
//
// *** NOT HARDWARE / 非硬件合成 — SYNTHETIC FIXTURE, TEST ONLY ***
//
// We hand the panels the SAME snapshot shapes the engine pushes on its
// diff'd signals (pocsagMessagesChanged / m17CallsChanged / vorRadialChanged)
// straight into their slots -- the "direct snapshot" feed, mirroring how
// test_digital_link_integration drives VfoManager. No radio, no file, no
// station database, no pre-stored pager / callsign / bearing.
//
// Contract under test:
//   1. Synthetic rows render with their real decoded fields.
//   2. An empty list / unlocked VorResult is the HONEST empty state (the VOR
//      needle must never show a fabricated bearing -- radial reads "—").
//   3. m17 voice frames carry the explicit "未解码" note (Codec2 not bundled).
//   4. clear() returns to empty AND emits clearRequested (engine hook).
//   5. A narrow right-rail resize renders without crashing / overflow.
#include "ui/pocsag_panel.h"
#include "ui/m17_panel.h"
#include "ui/vor_panel.h"

#include <QtTest>
#include <QApplication>
#include <QImage>
#include <QLayout>
#include <QSignalSpy>
#include <QWidget>

#include <string>
#include <vector>

using namespace mbdsdr;
using mbdsdr::dsp::M17Call;
using mbdsdr::dsp::PocsagMessage;
using mbdsdr::dsp::VorResult;
using mbdsdr::ui::M17Panel;
using mbdsdr::ui::PocsagPanel;
using mbdsdr::ui::VorPanel;

namespace {
PocsagMessage makePocsag(uint32_t addr, int func, const std::string& text,
                         PocsagMessage::Type t) {
    PocsagMessage m;
    m.address = addr;
    m.function = func;
    m.text = text;
    m.type = t;
    return m;
}

M17Call makeM17(const std::string& src, const std::string& dst,
                bool stream, int payloadClass, bool voiceUndecoded) {
    M17Call c;
    c.src = src;
    c.dst = dst;
    c.type = 0x0000;
    c.isStream = stream;
    c.payloadClass = payloadClass;
    c.frameKind = 1;
    c.crcOk = true;
    c.voiceUndecoded = voiceUndecoded;
    return c;
}
} // namespace

class TestDigitalPanelUi : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() {
        pocsag_ = new PocsagPanel();
        m17_    = new M17Panel();
        vor_    = new VorPanel();
        for (QWidget* w : {static_cast<QWidget*>(pocsag_),
                            static_cast<QWidget*>(m17_),
                            static_cast<QWidget*>(vor_)}) {
            w->resize(300, 360);
            w->show();
        }
        QTest::qWait(20);
    }

    // ---- POCSAG -----------------------------------------------------------
    void pocsagStartsEmpty() {
        QVERIFY(pocsag_->isEmptyView());
        QCOMPARE(pocsag_->messageCount(), 0);
        QImage img = pocsag_->grab().toImage();   // empty state must render
        QVERIFY(!img.isNull());
    }

    void pocsagSyntheticRowsRender() {
        std::vector<PocsagMessage> msgs;
        msgs.push_back(makePocsag(2134567u, 1, "HELLO-ALPHA", PocsagMessage::Type::Alpha));
        msgs.push_back(makePocsag(911u, 0, "1234", PocsagMessage::Type::Numeric));
        pocsag_->setMessages(msgs);

        QCOMPARE(pocsag_->messageCount(), 2);
        QVERIFY(!pocsag_->isEmptyView());
        QVERIFY2(pocsag_->rowAddress(0).contains(QString::number(2134567)),
                 "first row must carry the real RIC");
        QCOMPARE(pocsag_->rowPayload(0), QStringLiteral("HELLO-ALPHA"));
        QCOMPARE(pocsag_->rowPayload(1), QStringLiteral("1234"));
        QImage img = pocsag_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    void pocsagGrowingListKeepsArrivalStamps() {
        // Engine re-pushes the WHOLE list; a second message must not re-time or
        // drop the first row.
        std::vector<PocsagMessage> one;
        one.push_back(makePocsag(111u, 2, "FIRST", PocsagMessage::Type::Alpha));
        pocsag_->setMessages(one);
        QCOMPARE(pocsag_->messageCount(), 1);

        std::vector<PocsagMessage> two = one;
        two.push_back(makePocsag(222u, 3, "SECOND", PocsagMessage::Type::Numeric));
        pocsag_->setMessages(two);
        QCOMPARE(pocsag_->messageCount(), 2);
        QVERIFY(pocsag_->rowPayload(0) == QStringLiteral("FIRST") ||
                pocsag_->rowPayload(1) == QStringLiteral("FIRST"));
    }

    void pocsagClearEmitsRequestAndEmpties() {
        QSignalSpy spy(pocsag_, &PocsagPanel::clearRequested);
        pocsag_->clear();
        QCOMPARE(spy.count(), 1);
        QVERIFY(pocsag_->isEmptyView());
        // Pure-noise / left-mode honest empty edge: engine pushes empty vector.
        pocsag_->setMessages({});
        QVERIFY(pocsag_->isEmptyView());
    }

    // ---- m17 --------------------------------------------------------------
    void m17StartsEmpty() {
        QVERIFY(m17_->isEmptyView());
        QImage img = m17_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    void m17SyntheticCallsRender() {
        M17Call data = makeM17("DB0MBD", "BROADCAST", /*stream=*/false,
                               /*payloadClass=*/1, /*voiceUndecoded=*/false);
        data.frameKind = 1;
        data.payload = {0x48, 0x69};   // raw data bytes -> hex preview
        M17Call voice = makeM17("N0CALL", "DB0MBD", /*stream=*/true,
                                /*payloadClass=*/2, /*voiceUndecoded=*/true);
        voice.frameKind = 2;
        std::vector<M17Call> calls = {data, voice};
        m17_->setCalls(calls);

        QCOMPARE(m17_->callCount(), 2);
        QVERIFY2(m17_->rowSrc(0).contains("DB0MBD"),
                 "source callsign must come from the real LSF decode");
        QVERIFY2(m17_->rowData(0).contains("48") && m17_->rowData(0).contains("69"),
                 "data frame shows the real raw payload bytes as hex");
        // Voice stream: honestly flagged as not Codec2-decoded.
        const QString typeRow = m17_->rowType(1);
        QVERIFY2(typeRow.contains(QStringLiteral("未解码")),
                 "voiceUndecoded frames MUST carry the honest '未解码' note");
        QImage img = m17_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    void m17ClearEmitsRequestAndEmpties() {
        QSignalSpy spy(m17_, &M17Panel::clearRequested);
        m17_->clear();
        QCOMPARE(spy.count(), 1);
        QVERIFY(m17_->isEmptyView());
    }

    // ---- VOR ---------------------------------------------------------------
    void vorStartsUnlocked() {
        // No reading yet (or pure noise): honest no-lock, no bearing.
        QVERIFY(!vor_->locked());
        QCOMPARE(vor_->radialText(), QStringLiteral("—"));
        QCOMPARE(vor_->morseId(), QString());
        QImage img = vor_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    void vorLockedReadingRenders() {
        VorResult r;
        r.locked = true;
        r.radialDeg = 123.4;
        r.quality = 0.82;
        r.morseId = QStringLiteral("MBD");
        vor_->setResult(r);

        QVERIFY(vor_->locked());
        QVERIFY2(vor_->radialText().contains(QStringLiteral("123")),
                 "locked radial must show the real measured bearing");
        QCOMPARE(vor_->morseId(), QStringLiteral("MBD"));
        QImage img = vor_->grab().toImage();   // needle paints without crashing
        QVERIFY(!img.isNull());
    }

    void vorUnlockDropsFabricatedBearing() {
        // The honest gate: once lock is lost the reading reverts to "—" --
        // the panel must NOT keep painting the stale 123° as a live bearing.
        VorResult unlocked;   // defaults: locked=false, radialDeg=0, no morse
        vor_->setResult(unlocked);
        QVERIFY(!vor_->locked());
        QCOMPARE(vor_->radialText(), QStringLiteral("—"));
        QImage img = vor_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    // ---- narrow rail: no horizontal overflow ------------------------------
    void narrowResizeRenders() {
        // Feed one row each so the overflow check covers the VISIBLE table path
        // (not just the hidden empty state): at ~240 px rail width every visible
        // child must stay inside the panel's own edge.
        {
            std::vector<PocsagMessage> msgs;
            msgs.push_back(makePocsag(12345u, 1, "NARROW-CHECK-PAYLOAD",
                                      PocsagMessage::Type::Alpha));
            pocsag_->setMessages(msgs);
        }
        {
            std::vector<M17Call> calls;
            calls.push_back(makeM17("DB0MBD", "BROADCAST", false, 1, false));
            m17_->setCalls(calls);
        }
        VorResult locked;
        locked.locked = true;
        locked.radialDeg = 45.0;
        vor_->setResult(locked);

        // Right rail at its narrowest (~240 px). Elastic layout contract: the
        // panel accepts the narrow width (Qt clamps to its internal minimum,
        // never the other way round) and NO child widget may overflow the
        // panel's own right edge -- tables elide/scroll instead of spilling.
        for (QWidget* w : {static_cast<QWidget*>(pocsag_),
                            static_cast<QWidget*>(m17_),
                            static_cast<QWidget*>(vor_)}) {
            w->resize(240, 360);
            w->show();
            if (w->layout()) w->layout()->activate();
            QTest::qWait(30);
            QApplication::processEvents();
            const int right = w->rect().right();
            QVERIFY2(w->width() <= 260,
                     "panel must not demand much more than the narrow rail width");
            for (QObject* ch : w->children()) {
                if (auto* cw = qobject_cast<QWidget*>(ch)) {
                    // Only VISIBLE widgets can overflow what the user sees; a
                    // hidden table keeps its stale pre-hide geometry and is not
                    // an overflow (the empty-state label is centered instead).
                    if (!cw->isVisible()) continue;
                    QVERIFY2(cw->geometry().right() <= right + 1,
                             "a visible child widget overflows the narrow panel edge");
                }
            }
            QImage img = w->grab().toImage();
            QVERIFY(!img.isNull());
        }
    }

    void cleanupTestCase() {
        delete pocsag_;
        delete m17_;
        delete vor_;
    }

private:
    PocsagPanel* pocsag_ = nullptr;
    M17Panel*    m17_    = nullptr;
    VorPanel*    vor_    = nullptr;
};

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    TestDigitalPanelUi tc;
    return QTest::qExec(&tc, argc, argv);
}
#include "test_digital_panel_ui.moc"
