// SPDX-License-Identifier: MIT
//
// 跨语言对拍 golden（C++ 侧钉扎）。
//
// 与 mbdsdr_ai/tests/test_cross_golden.py 钉扎**同一份** JSON
// （tests/golden/cross_decode_vectors.json，Python 参考端生成自洽）：
// 任一侧实现漂移，对应 CI job 即红——FT8 / ADS-B 双实现的自动对拍。
//
// 诚实契约（两层，与 Python 侧逐字同口径）：
//   * 全零码字（线性码数学必然）字段层必须拒绝（unpack "???" -> nullopt），
//   * 杂散 LLR 解码层必须整体放弃（nullopt），绝不编造。
#include <QtTest>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include <complex>
#include <vector>

#include "dsp/adsb_decoder.h"
#include "dsp/ft8_codec.h"

#ifndef MBDSDR_GOLDEN_DIR
#error "MBDSDR_GOLDEN_DIR must be defined by CMake"
#endif

using namespace mbdsdr;

class TestCrossGolden : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void ft8CodewordAndFields();
    void ft8NoisyConverges();
    void ft8HonestFailureLayers();
    void adsbCrc24Head();
    void adsbIdentIqRoundtrip();
    void adsbCprGlobalDecode();

private:
    QJsonObject doc_;
    QJsonArray ft8Cases_;
    QJsonArray identCases_;
    QJsonArray cprCases_;
    static std::vector<double> llr(const QJsonObject& o, const char* key);
};

std::vector<double> TestCrossGolden::llr(const QJsonObject& o, const char* key) {
    std::vector<double> v;
    const QJsonArray a = o.value(QLatin1String(key)).toArray();
    v.reserve(a.size());
    for (const QJsonValue& x : a) v.push_back(x.toDouble());
    return v;
}

void TestCrossGolden::initTestCase() {
    QFile f(QString::fromUtf8(MBDSDR_GOLDEN_DIR) +
            QStringLiteral("/cross_decode_vectors.json"));
    QVERIFY2(f.open(QIODevice::ReadOnly), "golden JSON missing — run the generator");
    doc_ = QJsonDocument::fromJson(f.readAll()).object();
    ft8Cases_ = doc_.value(QStringLiteral("ft8")).toObject()
                    .value(QStringLiteral("cases")).toArray();
    const QJsonObject adsb = doc_.value(QStringLiteral("adsb")).toObject();
    identCases_ = adsb.value(QStringLiteral("ident_cases")).toArray();
    cprCases_ = adsb.value(QStringLiteral("cpr_cases")).toArray();
    QVERIFY(!ft8Cases_.isEmpty());
    QVERIFY(!identCases_.isEmpty());
    QVERIFY(!cprCases_.isEmpty());
}

void TestCrossGolden::ft8CodewordAndFields() {
    const Ft8Codec codec;
    for (const QJsonValue& v : ft8Cases_) {
        const QJsonObject o = v.toObject();
        const QByteArray name = o.value(QStringLiteral("name"))
                                    .toString().toUtf8();
        const std::vector<double> clean = llr(o, "llr_clean");
        QCOMPARE(clean.size(), std::size_t(174));
        const auto got = codec.decode(clean.data(), clean.size());
        QVERIFY2(got.has_value(), name.constData());
        // 字段逐位对拍（与 Python unpack77 同口径）。
        QCOMPARE(QString::fromStdString(got->from),
                 o.value(QStringLiteral("from")).toString());
        QCOMPARE(QString::fromStdString(got->to),
                 o.value(QStringLiteral("to")).toString());
        QCOMPARE(QString::fromStdString(got->exchange),
                 o.value(QStringLiteral("exchange")).toString());
        QCOMPARE(got->report,
                 o.value(QStringLiteral("report")).toBool());
        // CRC14 钉扎：golden 的 91-bit 前缀必须自洽。
        const QJsonArray msg = o.value(QStringLiteral("msg91")).toArray();
        QCOMPARE(msg.size(), 91);
        std::vector<int> bits91(91);
        for (int i = 0; i < 91; ++i) bits91[i] = msg[i].toInt();
        QVERIFY(Ft8Codec::checkCrc14(bits91.data(), 91));
    }
}

void TestCrossGolden::ft8NoisyConverges() {
    const Ft8Codec codec;
    for (const QJsonValue& v : ft8Cases_) {
        const QJsonObject o = v.toObject();
        const QByteArray name = o.value(QStringLiteral("name"))
                                    .toString().toUtf8();
        const std::vector<double> noisy = llr(o, "llr_noisy");
        const auto got = codec.decode(noisy.data(), noisy.size());
        QVERIFY2(got.has_value(), name.constData());
        QCOMPARE(QString::fromStdString(got->from),
                 o.value(QStringLiteral("from")).toString());
        QCOMPARE(QString::fromStdString(got->to),
                 o.value(QStringLiteral("to")).toString());
    }
}

void TestCrossGolden::ft8HonestFailureLayers() {
    const Ft8Codec codec;
    for (const QJsonValue& v : ft8Cases_) {
        const QJsonObject o = v.toObject();
        const QByteArray name = o.value(QStringLiteral("name"))
                                    .toString().toUtf8();
        // 全零码字：BP 可能返回全零，但字段层必须拒绝（unpack "???" -> nullopt）。
        const std::vector<double> zero = llr(o, "llr_zero_cw");
        QVERIFY2(!codec.decode(zero.data(), zero.size()).has_value(),
                 "zero-codeword fields must be null");
        // 杂散 LLR：解码层必须整体放弃。
        const std::vector<double> garbage = llr(o, "llr_garbage");
        QVERIFY2(!codec.decode(garbage.data(), garbage.size()).has_value(),
                 "garbage must not decode");
        (void)name;
    }
}

void TestCrossGolden::adsbCrc24Head() {
    for (const QJsonValue& v : identCases_) {
        const QJsonObject o = v.toObject();
        const QByteArray name = o.value(QStringLiteral("name"))
                                    .toString().toUtf8();
        const QByteArray frame = QByteArray::fromHex(
            o.value(QStringLiteral("frame_hex")).toString().toLatin1());
        QCOMPARE(frame.size(), 14);
        const QByteArray head = frame.left(11);
        const quint32 crc = dsp::ADSBDecoder::crc24(
            reinterpret_cast<const uint8_t*>(head.constData()), 11);
        QCOMPARE(qint64(crc),
                 qint64(o.value(QStringLiteral("crc24_head")).toDouble()));
        (void)name;
    }
}

void TestCrossGolden::adsbIdentIqRoundtrip() {
    for (const QJsonValue& v : identCases_) {
        const QJsonObject o = v.toObject();
        const QByteArray name = o.value(QStringLiteral("name"))
                                    .toString().toUtf8();
        const QByteArray raw = QByteArray::fromBase64(
            o.value(QStringLiteral("iq_b64")).toString().toLatin1());
        QVERIFY(raw.size() % int(sizeof(float)) == 0);
        const int nfloat = raw.size() / int(sizeof(float));
        QCOMPARE(nfloat % 2, 0);
        const float* fp = reinterpret_cast<const float*>(raw.constData());
        std::vector<std::complex<float>> iq;
        iq.reserve(nfloat / 2);
        for (int i = 0; i < nfloat; i += 2) iq.emplace_back(fp[i], fp[i + 1]);

        dsp::ADSBDecoder dec;
        dec.setSampleRate(o.value(QStringLiteral("sample_rate")).toDouble());
        dec.feed(iq);
        const auto aircraft = dec.takeNewAircraft();
        bool hit = false;
        for (const auto& a : aircraft) {
            if (a.icao.compare(o.value(QStringLiteral("icao")).toString(),
                               Qt::CaseInsensitive) == 0) {
                hit = true;
                QVERIFY2(a.crcOk, "golden IQ frame must pass CRC");
                QCOMPARE(a.callsign.trimmed(),
                         o.value(QStringLiteral("callsign")).toString());
            }
        }
        QVERIFY2(hit, name.constData());
    }
}

void TestCrossGolden::adsbCprGlobalDecode() {
    for (const QJsonValue& v : cprCases_) {
        const QJsonObject o = v.toObject();
        const QByteArray name = o.value(QStringLiteral("name"))
                                    .toString().toUtf8();
        dsp::CprPair p;
        p.latEven = o.value(QStringLiteral("even_cprlat")).toInt();
        p.lonEven = o.value(QStringLiteral("even_cprlon")).toInt();
        p.latOdd = o.value(QStringLiteral("odd_cprlat")).toInt();
        p.lonOdd = o.value(QStringLiteral("odd_cprlon")).toInt();
        p.haveEven = p.haveOdd = true;

        double lat = 0, lon = 0;
        // evenNewest=true -> 偶帧槽输出（Python fflag=0 同口径）。
        p.evenNewest = true;
        QVERIFY2(dsp::cprGlobalDecode(p, lat, lon), name.constData());
        const QJsonArray expEven =
            o.value(QStringLiteral("expect_even_slot")).toArray();
        QVERIFY(std::abs(lat - expEven[0].toDouble()) < 1e-4);
        QVERIFY(std::abs(lon - expEven[1].toDouble()) < 1e-4);

        p.evenNewest = false;
        QVERIFY2(dsp::cprGlobalDecode(p, lat, lon), name.constData());
        const QJsonArray expOdd =
            o.value(QStringLiteral("expect_odd_slot")).toArray();
        QVERIFY(std::abs(lat - expOdd[0].toDouble()) < 1e-4);
        QVERIFY(std::abs(lon - expOdd[1].toDouble()) < 1e-4);
    }
}

QTEST_MAIN(TestCrossGolden)
#include "test_cross_golden.moc"
