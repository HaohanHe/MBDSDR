// SPDX-License-Identifier: MIT
// UI soft-break helper: long driver identifiers must be wrappable inside a
// narrow label without losing any characters (zero-width spaces only).
#include <QtTest>
#include "ui/text_util.h"

using mbdsdr::ui::softBreakable;

class TestTextUtil : public QObject {
    Q_OBJECT
private slots:
    void insertsSoftBreaksAfterSeparators() {
        const QString in = QStringLiteral("rtlsdr_get_tuner_type/librtlsdr-x.y");
        const QString out = softBreakable(in);
        QCOMPARE(out.count(QChar(0x200B)), 6);
        QString stripped = out;
        stripped.remove(QChar(0x200B));
        QCOMPARE(stripped, in);
    }

    void plainTextWithoutSeparatorsIsUnchanged() {
        QCOMPARE(softBreakable(QStringLiteral("未连接")), QStringLiteral("未连接"));
    }

    void chineseWithSlashGetsBreak() {
        const QString out = softBreakable(QStringLiteral("GNSS / 站址"));
        QVERIFY(out.contains(QChar(0x200B)));
        QString stripped = out;
        stripped.remove(QChar(0x200B));
        QCOMPARE(stripped, QStringLiteral("GNSS / 站址"));
    }
};

QTEST_MAIN(TestTextUtil)
#include "test_text_util.moc"
