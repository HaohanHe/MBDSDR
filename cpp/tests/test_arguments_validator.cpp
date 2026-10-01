// SPDX-License-Identifier: MIT
// M2 arguments validator tests: fully deterministic, offline (no network,
// no real API). Covers every check in spec §4: required / type / enum /
// min-max / whitelist / null-as-missing, plus the role=tool errorJson shape.
#include <QtTest/QtTest>
#include <QJsonObject>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonValue>
#include "ai/arguments_validator.h"

using namespace mbdsdr::ai;

namespace {

// A tune_frequency-like tool: freq_hz(number, required, bounded),
// mode(string, required, enum), step_hz(integer, optional, min=1).
QJsonObject sampleSchema() {
    QJsonObject props;

    QJsonObject freq;
    freq["type"] = "number";
    freq["minimum"] = 24000000.0;
    freq["maximum"] = 1700000000.0;
    props["freq_hz"] = freq;

    QJsonObject mode;
    mode["type"] = "string";
    QJsonArray en; en.append("AM"); en.append("FM");
    mode["enum"] = en;
    props["mode"] = mode;

    QJsonObject step;
    step["type"] = "integer";
    step["minimum"] = 1.0;
    props["step_hz"] = step;

    QJsonObject s;
    s["type"] = "object";
    s["properties"] = props;
    QJsonArray req; req.append("freq_hz"); req.append("mode");
    s["required"] = req;
    return s;
}

QJsonObject emptySchema() { // no-arg tool (start_recording / stop_recording / get_status)
    QJsonObject s;
    s["type"] = "object";
    s["properties"] = QJsonObject();
    s["required"] = QJsonArray();
    return s;
}

QString joinedReasons(const ValidationResult& r) { return r.reasons.join("|"); }

} // namespace

class TestArgumentsValidator : public QObject {
    Q_OBJECT
private slots:
    void validArgsPass();
    void missingRequiredRejected();
    void requiredNullCountsAsMissing();
    void wrongTypeRejected();
    void integerRejectsFraction();
    void enumOutOfRangeRejected();
    void belowMinimumRejected();
    void aboveMaximumRejected();
    void hallucinatedKeyRejected();
    void optionalNullIsSkipped();
    void noArgToolAcceptsEmptyObject();
    void noArgToolRejectsAnyExtra();
    void booleanTypeChecked();
    void errorJsonShapeIsToolReady();
};

void TestArgumentsValidator::validArgsPass() {
    QJsonObject args;
    args["freq_hz"] = 145000000.0;
    args["mode"] = "AM";
    args["step_hz"] = 200000.0;
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY2(r.ok, qPrintable(joinedReasons(r)));
    QVERIFY(r.errorJson.isEmpty());
    QVERIFY(r.reasons.isEmpty());
}

void TestArgumentsValidator::missingRequiredRejected() {
    QJsonObject args;
    args["mode"] = "FM"; // freq_hz missing
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.contains("缺少必填参数：freq_hz"));
}

void TestArgumentsValidator::requiredNullCountsAsMissing() {
    QJsonObject args;
    args["freq_hz"] = QJsonValue::Null; // present but null → treated as missing
    args["mode"] = "AM";
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.contains("缺少必填参数：freq_hz"));
}

void TestArgumentsValidator::wrongTypeRejected() {
    QJsonObject args;
    args["freq_hz"] = "not-a-number"; // string where number expected
    args["mode"] = "AM";
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.join("|").contains("类型错误"));
    QVERIFY(r.reasons.join("|").contains("freq_hz"));
}

void TestArgumentsValidator::integerRejectsFraction() {
    QJsonObject args;
    args["freq_hz"] = 145000000.0;
    args["mode"] = "AM";
    args["step_hz"] = 200.5; // integer field with fractional value
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.join("|").contains("step_hz"));
}

void TestArgumentsValidator::enumOutOfRangeRejected() {
    QJsonObject args;
    args["freq_hz"] = 145000000.0;
    args["mode"] = "USB"; // not in {AM, FM}
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.contains("参数 mode 取值不在枚举中"));
}

void TestArgumentsValidator::belowMinimumRejected() {
    QJsonObject args;
    args["freq_hz"] = 100.0; // below 24 MHz floor
    args["mode"] = "AM";
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.join("|").contains("超出下界"));
}

void TestArgumentsValidator::aboveMaximumRejected() {
    QJsonObject args;
    args["freq_hz"] = 3.0e9; // above 1700 MHz ceiling
    args["mode"] = "AM";
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.join("|").contains("超出上界"));
}

void TestArgumentsValidator::hallucinatedKeyRejected() {
    QJsonObject args;
    args["freq_hz"] = 145000000.0;
    args["mode"] = "AM";
    args["band"] = "120m"; // hallucinated, not declared in properties
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.contains("未知参数（schema 外）：band"));
}

void TestArgumentsValidator::optionalNullIsSkipped() {
    QJsonObject args;
    args["freq_hz"] = 145000000.0;
    args["mode"] = "AM";
    args["step_hz"] = QJsonValue::Null; // optional + null → ignored, not an error
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY2(r.ok, qPrintable(joinedReasons(r)));
}

void TestArgumentsValidator::noArgToolAcceptsEmptyObject() {
    QJsonObject args; // anything-but-keys empty object
    ValidationResult r = validateArguments("get_status", args, emptySchema());
    QVERIFY2(r.ok, qPrintable(joinedReasons(r)));
}

void TestArgumentsValidator::noArgToolRejectsAnyExtra() {
    QJsonObject args;
    args["bogus"] = 1;
    ValidationResult r = validateArguments("get_status", args, emptySchema());
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.contains("未知参数（schema 外）：bogus"));
}

void TestArgumentsValidator::booleanTypeChecked() {
    QJsonObject props;
    QJsonObject flag; flag["type"] = "boolean";
    props["active"] = flag;
    QJsonObject s; s["type"] = "object"; s["properties"] = props; s["required"] = QJsonArray();

    QJsonObject good; good["active"] = true;
    QVERIFY(validateArguments("x", good, s).ok);

    QJsonObject bad; bad["active"] = "yes"; // string instead of bool
    ValidationResult r = validateArguments("x", bad, s);
    QVERIFY(!r.ok);
    QVERIFY(r.reasons.join("|").contains("类型错误"));
}

void TestArgumentsValidator::errorJsonShapeIsToolReady() {
    QJsonObject args;
    args["freq_hz"] = "oops";      // wrong type
    args["mode"] = "USB";          // also out of enum
    args["ghost"] = 1;             // plus hallucinated key
    ValidationResult r = validateArguments("tune_frequency", args, sampleSchema());
    QVERIFY(!r.ok);
    QVERIFY(!r.errorJson.isEmpty());

    // errorJson must itself be compact JSON, directly usable as role=tool content.
    QJsonParseError pe{};
    QJsonDocument doc = QJsonDocument::fromJson(r.errorJson.toUtf8(), &pe);
    QVERIFY2(pe.error == QJsonParseError::NoError, r.errorJson.toUtf8().constData());
    QJsonObject o = doc.object();
    QCOMPARE(o.value("ok").toBool(), false);
    QCOMPARE(o.value("tool").toString(), QString::fromLatin1("tune_frequency"));
    QVERIFY(o.value("error").toString().startsWith(QString::fromUtf8("参数校验失败：")));
    QJsonArray ra = o.value("reasons").toArray();
    QVERIFY(!ra.isEmpty());
    // reasons array mirrors the returned reasons list.
    QCOMPARE(ra.size(), r.reasons.size());
    for (int i = 0; i < ra.size(); ++i) {
        QCOMPARE(ra.at(i).toString(), r.reasons.at(i));
    }
    // Compact form carries no newline/indent whitespace.
    QVERIFY(!r.errorJson.contains('\n'));
}

QTEST_MAIN(TestArgumentsValidator)
#include "test_arguments_validator.moc"
