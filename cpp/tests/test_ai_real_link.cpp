// SPDX-License-Identifier: MIT
// W2d A4 real-link closed loop (C++ side). Fully offline / deterministic:
//   * no network, no API key (mock transport not even needed -- we call
//     executeTool / the planner directly),
//   * the engine defaults to the offline TestSignalSource (no RTL present),
//   * pass prediction uses the pure predictPassesFromEntries seam with a FIXED
//     known TLE (the celestrak AIAA-2006 SGP4 verification set, same vector the
//     sat_task_planner test uses) -- never the on-disk cache, never a live pull.
//
// Covers the A4 frozen decisions:
//   P1  every tool result honestly discloses connected / source (test signal,
//       not real hardware); get_status aligned with Flutter.
//   P1  scan_band on the synthetic signal is labelled synthetic (no fake RF hit).
//   P1  start_recording propagates the engine bool (no unconditional success).
//   P2  predict_passes read-only tool over fresh cache; deterministic seam +
//       honest empty states (no fresh TLE / bad coords).
//   P2  tool-count audit: registeredToolSpecs() names == the set executeTool
//       actually supports (registry == schema, no omissions).
#include <QtTest/QtTest>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QSet>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QDateTime>
#include <QDate>
#include <QTime>
#include <cmath>
#include <ctime>

#include "ai/agent_tools.h"
#include "ai/tool_schema.h"
#include "ai/sat_task_planner.h"
#include "dsp/spectrum_engine.h"
#include "dsp/tle_client.h"

using namespace mbdsdr;

namespace {
// CBERS 2 (28057) verification epoch, identical to test_sat_task_planner.
QDateTime cbersEpochStart() {
    QDate d0(2006, 1, 1);
    double dayFrac = 177.78615833 - 1.0;
    qint64 dayInt = static_cast<qint64>(std::floor(dayFrac));
    double secs = (dayFrac - dayInt) * 86400.0;
    QDateTime midnight(d0.addDays(dayInt), QTime(0, 0, 0), Qt::UTC);
    return QDateTime::fromMSecsSinceEpoch(
        midnight.toMSecsSinceEpoch() + static_cast<qint64>(secs * 1000.0),
        Qt::UTC).addSecs(240 * 60);
}

QJsonObject parseObj(const QString& s) {
    return QJsonDocument::fromJson(s.toUtf8()).object();
}
} // namespace

class TestAiRealLink : public QObject {
    Q_OBJECT
private slots:
    // Phase21: engine no longer auto-falls back to the offline test source. These
    // cases assert the synthetic source is honestly disclosed (test_signal==true,
    // "测试信号") and scanBand marks it synthetic, so opt in explicitly BEFORE any
    // engine is constructed. The honest-empty POCSAG/m17/VOR slots still hold:
    // they never start the run loop and connected==false (=hasRealSource) for a
    // synthetic source.
    void initTestCase();
    void sourceDisclosure_testSignalHonest();
    void scanBand_marksSynthetic();
    void startRecording_reportsBoolNotUnconditional();
    void getStatus_alignedWithFlutter();
    void predictPasses_deterministicKnownTle();
    void predictPasses_emptyEntriesHonest();
    void predictPasses_badStationHonest();
    void pocsagSnapshot_honestEmptyOffline();
    void m17Snapshot_honestEmptyOffline();
    void vorSnapshot_honestUnlockedOffline();
    void exportIqSegment_writesRealFile();
    void exportIqSegment_noDataHonestError();
    void toolCount_registryEqualsExecution();
    void phase26_tools_callableReturnJson();
    void phase26_badArgsHonestError();
};

void TestAiRealLink::initTestCase() {
    qputenv("MBDSDR_TEST_SOURCE", "1");   // explicit synthetic offline source
}

// Default engine = offline TestSignalSource. Every result must disclose
// connected:false and a 测试信号 source -- never claim real hardware.
void TestAiRealLink::sourceDisclosure_testSignalHonest() {
    dsp::SpectrumEngine engine;
    QJsonObject args; args["freq_hz"] = 103300000;
    QString r = ai::executeTool("tune_frequency", args, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("connected").toBool(), false);
    QCOMPARE(o.value("test_signal").toBool(), true);
    QVERIFY2(o.value("source").toString().contains(QString::fromUtf8("测试信号")),
             qPrintable("source must honestly say test signal, got: " + r));
    QVERIFY2(r.contains("103.3"), qPrintable("human-readable MHz kept, got: " + r));
}

// scan_band on the synthetic source must NOT present a fixed-tone peak as a real
// RF station hit: it carries synthetic:true + an honest note.
void TestAiRealLink::scanBand_marksSynthetic() {
    dsp::SpectrumEngine engine;
    QJsonObject args;
    args["low_hz"] = 88000000;
    args["high_hz"] = 108000000;
    args["step_hz"] = 1000000;
    QString r = ai::executeTool("scan_band", args, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("ok").toBool(), true);
    QCOMPARE(o.value("synthetic").toBool(), true);
    QVERIFY2(o.value("note").toString().contains(QString::fromUtf8("合成")),
             qPrintable("scan on test signal must note it is synthetic, got: " + r));
    QCOMPARE(o.value("connected").toBool(), false);
}

// start_recording must propagate the engine bool: result is a JSON object with an
// "ok" boolean, never the bare string "开始录制".
void TestAiRealLink::startRecording_reportsBoolNotUnconditional() {
    dsp::SpectrumEngine engine;
    QString r = ai::executeTool("start_recording", QJsonObject{}, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable(
        "start_recording must return JSON with an ok bool, got: " + r));
    QVERIFY(o.contains("ok"));
    QVERIFY(o.value("ok").isBool());
    // On the offline test source the recorder really does open a SigMF file, so
    // ok is true here -- but it is the engine's real verdict, not an unconditional.
    QVERIFY(o.value("ok").toBool());
    QVERIFY(o.contains("path"));
    // tidy up
    ai::executeTool("stop_recording", QJsonObject{}, &engine);
}

// get_status carries connected/source + read-back values, aligned with Flutter
// ai_tools.dart get_status (connected first), and keeps a human summary.
void TestAiRealLink::getStatus_alignedWithFlutter() {
    dsp::SpectrumEngine engine;
    QString r = ai::executeTool("get_status", QJsonObject{}, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("connected").toBool(), false);
    QVERIFY(o.contains("source"));
    QVERIFY(o.contains("frequency_hz"));
    QVERIFY(o.contains("mode"));
    QVERIFY2(o.value("summary").toString().contains(QString::fromUtf8("频率")),
             qPrintable("summary keeps human 频率 text, got: " + r));
}

// Deterministic core: inject the fixed known CBERS-2 TLE into the pure seam and
// assert a stable pass list (rise/set/max-elevation), no disk / no network.
void TestAiRealLink::predictPasses_deterministicKnownTle() {
    const QList<dsp::TleEntry> known = dsp::TleClient::builtinTle();
    QVERIFY(!known.isEmpty());
    ai::SatPassListResult r = ai::predictPassesFromEntries(
        known, "CBERS", 40.0, -100.0, cbersEpochStart(), 6);
    QVERIFY2(r.ok, qPrintable(r.error));
    QVERIFY2(!r.passes.isEmpty(), "known CBERS orbit must yield a pass in window");
    const ai::SatPassEntry& p = r.passes.first();
    QVERIFY(p.aosUtc.isValid());
    QVERIFY(p.losUtc.isValid());
    QVERIFY2(p.maxEl > 0.0, qPrintable("pass must rise above the horizon"));
    QVERIFY(p.azAos >= 0.0 && p.azAos < 360.0);
    QCOMPARE(p.catalogNumber, 28057);
    QCOMPARE(p.name, QString::fromLatin1("CBERS 2"));
    // Same inputs -> same outputs (determinism).
    ai::SatPassListResult r2 = ai::predictPassesFromEntries(
        known, "CBERS", 40.0, -100.0, cbersEpochStart(), 6);
    QCOMPARE(r2.passes.size(), r.passes.size());
    QCOMPARE(r2.passes.first().aosUtc, p.aosUtc);
    QCOMPARE(r2.passes.first().losUtc, p.losUtc);
}

// Empty TLE list -> honest ok:false (we never fabricate a pass).
void TestAiRealLink::predictPasses_emptyEntriesHonest() {
    ai::SatPassListResult r = ai::predictPassesFromEntries(
        QList<dsp::TleEntry>{}, "ISS", 40.0, -100.0, cbersEpochStart(), 6);
    QVERIFY2(!r.ok, "empty TLE must be honest empty, not fabricated");
    QVERIFY(!r.error.isEmpty());
}

// The predict_passes tool branch: bad/missing station coordinates -> honest
// error BEFORE any disk/cache read (deterministic).
void TestAiRealLink::predictPasses_badStationHonest() {
    dsp::SpectrumEngine engine;
    QJsonObject args;
    args["satellite_name"] = "ISS";
    args["station_lat_deg"] = 999.0;   // out of range
    args["station_lon_deg"] = 116.0;
    QString r = ai::executeTool("predict_passes", args, &engine);
    QJsonObject o = parseObj(r);
    QCOMPARE(o.value("ok").toBool(), false);
    QVERIFY2(o.value("error").toString().contains(QString::fromUtf8("站点")),
             qPrintable("bad station must be honest, got: " + r));

    // Missing station coords likewise.
    QJsonObject noStation; noStation["satellite_name"] = "ISS";
    QJsonObject o2 = parseObj(ai::executeTool("predict_passes", noStation, &engine));
    QCOMPARE(o2.value("ok").toBool(), false);
}

// Wave2 read-only POCSAG snapshot tool. The offline test source runs a plain
// audio demod mode, so the POCSAG decoder has produced nothing: the tool must
// be callable and return an HONEST empty list (never a fabricated pager).
void TestAiRealLink::pocsagSnapshot_honestEmptyOffline() {
    dsp::SpectrumEngine engine;
    QString r = ai::executeTool("get_pocsag_messages", QJsonObject{}, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("ok").toBool(), true);
    QVERIFY(o.contains("channel_id"));
    QVERIFY(o.value("messages").isArray());
    QCOMPARE(o.value("messages").toArray().size(), 0);   // honest empty state
    QCOMPARE(o.value("count").toInt(), 0);
    QCOMPARE(o.value("connected").toBool(), false);
}

// Wave2 read-only m17 snapshot tool: same honest-empty contract on the offline
// source (no 4FSK carrier decoded yet).
void TestAiRealLink::m17Snapshot_honestEmptyOffline() {
    dsp::SpectrumEngine engine;
    QString r = ai::executeTool("get_m17_calls", QJsonObject{}, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("ok").toBool(), true);
    QVERIFY(o.contains("channel_id"));
    QVERIFY(o.value("calls").isArray());
    QCOMPARE(o.value("calls").toArray().size(), 0);     // honest empty state
    QCOMPARE(o.value("count").toInt(), 0);
    QCOMPARE(o.value("connected").toBool(), false);
}

// Wave2 read-only VOR radial tool: offline source has no VOR carrier, so the
// result is honestly unlocked -- locked=false and NO fabricated bearing.
void TestAiRealLink::vorSnapshot_honestUnlockedOffline() {
    dsp::SpectrumEngine engine;
    QString r = ai::executeTool("get_vor_radial", QJsonObject{}, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("ok").toBool(), true);
    QVERIFY(o.contains("channel_id"));
    QCOMPARE(o.value("locked").toBool(), false);          // honest empty state
    // radial must be explicitly null (meaningless), never a made-up 0 bearing.
    QVERIFY(o.value("radial_deg").isNull());
    QVERIFY2(o.value("note").toString().contains(QString::fromUtf8("锁定")),
             qPrintable("unlocked VOR must say so, got: " + r));
}

// One-shot export_iq_segment tool: on the synthetic source it really writes a
// cf32_le SigMF pair; we check the data file exists, is non-empty, and the
// sidecar meta carries the real sample count + cf32_le datatype (a real header
// check, not just a path echo). Uses a temp dir; tidies up.
void TestAiRealLink::exportIqSegment_writesRealFile() {
    const QString outDir = QDir::tempPath() + "/mbdsdr_ai_export";
    QDir().mkpath(outDir);

    dsp::SpectrumEngine engine;
    engine.setRecordingDir(outDir);

    QJsonObject args;
    args["sample_count"] = 16384;
    QString r = ai::executeTool("export_iq_segment", args, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty() && o.value("ok").toBool(),
             qPrintable("export on test signal must write a file, got: " + r));

    const QString dataPath = o.value("path").toString();
    QVERIFY2(dataPath.endsWith(QStringLiteral(".sigmf-data")),
             qPrintable("export must be a .sigmf-data file, got: " + dataPath));
    QVERIFY2(QFileInfo::exists(dataPath),
             qPrintable("exported data file must exist on disk: " + dataPath));

    // Byte size = samples * sizeof(complex<float>) = 16384*8. Honest lower bound.
    const qint64 bytes = o.value("bytes").toVariant().toLongLong();
    QVERIFY2(bytes > 0 && bytes >= 16384 * 8,
             qPrintable("file size must match the captured cf32 window, got: " +
                        QString::number(bytes)));
    QCOMPARE(o.value("samples").toVariant().toLongLong(), qint64(16384));

    // Sidecar header check: .sigmf-meta must record the real datatype + count.
    const QString metaPath =
        QString(dataPath).replace(QStringLiteral(".sigmf-data"),
                                  QStringLiteral(".sigmf-meta"));
    QVERIFY2(QFileInfo::exists(metaPath),
             qPrintable("export must write its SigMF sidecar: " + metaPath));
    QFile mf(metaPath);
    QVERIFY(mf.open(QIODevice::ReadOnly));
    QJsonObject meta = QJsonDocument::fromJson(mf.readAll()).object();
    QJsonObject global = meta.value("global").toObject();
    QCOMPARE(global.value("core:datatype").toString(), QStringLiteral("cf32_le"));
    QCOMPARE(global.value("core:num_samples").toVariant().toLongLong(),
             qint64(16384));

    // Honest provenance: synthetic source, not real hardware.
    QCOMPARE(o.value("connected").toBool(), false);
    QCOMPARE(o.value("test_signal").toBool(), true);

    QFile::remove(dataPath);
    QFile::remove(metaPath);
}

// No-data honest error: with the synthetic source OPTED OUT the engine lands on
// the empty NullSource, which yields no IQ. The export tool must report
// ok:false with an explicit error and must NOT fabricate an empty file.
void TestAiRealLink::exportIqSegment_noDataHonestError() {
    qunsetenv("MBDSDR_TEST_SOURCE");   // THIS engine -> honest empty NullSource
    dsp::SpectrumEngine emptyEng;
    QJsonObject args; args["sample_count"] = 8192;
    QString r = ai::executeTool("export_iq_segment", args, &emptyEng);
    qputenv("MBDSDR_TEST_SOURCE", "1");   // restore for any later engine

    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("ok").toBool(), false);
    QVERIFY2(o.value("error").toString().contains(QString::fromUtf8("数据")),
             qPrintable("no-data export must say so honestly, got: " + r));
    QVERIFY(!o.contains("path") || o.value("path").toString().isEmpty());
}

// Tool-count audit: the declarative registry must list EXACTLY the tools
// executeTool supports -- no missing registration, no phantom schema entry.
void TestAiRealLink::toolCount_registryEqualsExecution() {
    const QList<ai::ToolSchemaSpec> specs = ai::registeredToolSpecs();
    QSet<QString> registered;
    for (const ai::ToolSchemaSpec& s : specs) registered.insert(s.name);

    // The exact set executeTool() dispatches on (agent_tools.cpp).
    const QSet<QString> supported = {
        "tune_frequency", "set_mode", "start_recording", "stop_recording",
        "scan_band", "set_bandwidth", "get_status", "predict_passes",
        "calibrate_frequency", "apply_frequency_correction",
        "get_pocsag_messages", "get_m17_calls", "get_vor_radial",
        // Phase60 packet-text snapshot tools.
        "get_acars_packets", "get_navtex_messages",
        "export_iq_segment",
        // Phase26: 21 new tools.
        "set_network_audio_sink", "get_network_audio_status",
        "start_scan_link", "stop_scan_link", "get_scan_link_status",
        "set_squelch", "get_squelch_status",
        "set_ctcss", "get_ctcss_status",
        "set_cdcss", "get_cdcss_status",
        "set_ft8", "get_ft8_status",
        "set_lrpt", "get_lrpt_status",
        "set_vna_sweep", "get_vna_data", "get_vna_status",
        "set_noise_blanker", "get_noise_blanker_status",
        "list_bookmarks", "add_bookmark", "tune_to_bookmark", "delete_bookmark",
        "list_vfos", "add_vfo", "switch_vfo", "rename_vfo",
        "list_recordings", "delete_recording", "export_recording",
        "set_fft_params", "set_color_map", "get_spectrum_status",
        // Phase55/58 appended tools.
        "set_doppler_compensation", "connect_network_source",
        // Phase59 armed parallel VFO monitoring.
        "set_vfo_armed",
        // Phase61 VFO fine-grained edit tools.
        "set_vfo_frequency", "set_vfo_mode", "set_vfo_bandwidth",
        // Read-only capability/recording-state snapshots.
        "get_capabilities", "get_recording_state",
    };
    QCOMPARE(registered.size(), supported.size());
    QCOMPARE(registered, supported);

    // predict_passes is read-only (never gated in manual mode).
    QVERIFY(!ai::isWriteTool("predict_passes"));
    // Wave2 digital decode snapshot tools are read-only (never gated either).
    QVERIFY(!ai::isWriteTool("get_pocsag_messages"));
    QVERIFY(!ai::isWriteTool("get_m17_calls"));
    QVERIFY(!ai::isWriteTool("get_vor_radial"));
    // export_iq_segment writes a file to disk -> it IS a gated write tool.
    QVERIFY(ai::isWriteTool("export_iq_segment"));
    // Phase26 write/read split: 14 new writes gated, 7 new reads open.
    QVERIFY(ai::isWriteTool("set_network_audio_sink"));
    QVERIFY(ai::isWriteTool("start_scan_link"));
    QVERIFY(ai::isWriteTool("stop_scan_link"));
    QVERIFY(ai::isWriteTool("set_squelch"));
    QVERIFY(ai::isWriteTool("set_ctcss"));
    QVERIFY(ai::isWriteTool("set_cdcss"));
    QVERIFY(ai::isWriteTool("set_noise_blanker"));
    QVERIFY(ai::isWriteTool("add_bookmark"));
    QVERIFY(ai::isWriteTool("tune_to_bookmark"));
    QVERIFY(ai::isWriteTool("delete_bookmark"));
    QVERIFY(ai::isWriteTool("add_vfo"));
    QVERIFY(ai::isWriteTool("switch_vfo"));
    QVERIFY(ai::isWriteTool("rename_vfo"));
    QVERIFY(ai::isWriteTool("set_vfo_armed"));
    QVERIFY(ai::isWriteTool("set_vfo_frequency"));
    QVERIFY(ai::isWriteTool("set_vfo_mode"));
    QVERIFY(ai::isWriteTool("set_vfo_bandwidth"));
    QVERIFY(ai::isWriteTool("delete_recording"));
    QVERIFY(ai::isWriteTool("export_recording"));
    QVERIFY(ai::isWriteTool("set_fft_params"));
    QVERIFY(ai::isWriteTool("set_color_map"));
    QVERIFY(!ai::isWriteTool("get_network_audio_status"));
    QVERIFY(!ai::isWriteTool("get_scan_link_status"));
    QVERIFY(!ai::isWriteTool("get_squelch_status"));
    QVERIFY(!ai::isWriteTool("get_ctcss_status"));
    QVERIFY(!ai::isWriteTool("get_cdcss_status"));
    QVERIFY(!ai::isWriteTool("get_noise_blanker_status"));
    QVERIFY(!ai::isWriteTool("list_bookmarks"));
    QVERIFY(!ai::isWriteTool("list_vfos"));
    QVERIFY(!ai::isWriteTool("list_recordings"));
    QVERIFY(!ai::isWriteTool("get_spectrum_status"));
    // Read-only capability/recording-state snapshots are never gated.
    QVERIFY(!ai::isWriteTool("get_capabilities"));
    QVERIFY(!ai::isWriteTool("get_recording_state"));
}

// Phase26 contract: the newly registered tools are actually dispatchable
// (executeTool returns JSON, never "未知工具") and the engine-backed ones report
// real read-back. Offline synthetic source; honest source disclosure kept.
void TestAiRealLink::phase26_tools_callableReturnJson() {
    dsp::SpectrumEngine engine;

    // Read tools: callable, honest empty / real read-back.
    QJsonObject spec = parseObj(ai::executeTool("get_spectrum_status", QJsonObject{}, &engine));
    QVERIFY2(!spec.isEmpty() && spec.value("ok").toBool(),
             qPrintable("get_spectrum_status must return JSON, got: " +
                        ai::executeTool("get_spectrum_status", QJsonObject{}, &engine)));
    QVERIFY(spec.contains("fft_size"));

    QJsonObject vfos = parseObj(ai::executeTool("list_vfos", QJsonObject{}, &engine));
    QVERIFY2(vfos.value("ok").toBool(), qPrintable("list_vfos: " + ai::executeTool("list_vfos", QJsonObject{}, &engine)));
    QVERIFY(vfos.value("vfos").isArray());

    QJsonObject recs = parseObj(ai::executeTool("list_recordings", QJsonObject{}, &engine));
    QVERIFY2(recs.value("ok").toBool(), qPrintable("list_recordings: " + ai::executeTool("list_recordings", QJsonObject{}, &engine)));
    QVERIFY(recs.value("recordings").isArray());   // honest empty dir listing

    // Write tools: callable on the non-gated executeTool path and JSON-returning.
    QJsonObject fft; fft["fft_size"] = 4096;
    QJsonObject fftR = parseObj(ai::executeTool("set_fft_params", fft, &engine));
    QVERIFY2(fftR.value("ok").toBool(), qPrintable("set_fft_params: " + ai::executeTool("set_fft_params", fft, &engine)));
    QCOMPARE(fftR.value("fft_size").toInt(), 4096);   // real engine read-back

    QJsonObject sq; sq["enabled"] = true; sq["threshold_db"] = -60.0;
    QJsonObject sqR = parseObj(ai::executeTool("set_squelch", sq, &engine));
    QVERIFY2(sqR.value("ok").toBool(), qPrintable("set_squelch: " + ai::executeTool("set_squelch", sq, &engine)));

    QJsonObject sw; sw["index"] = 0;
    QJsonObject swR = parseObj(ai::executeTool("switch_vfo", sw, &engine));
    QVERIFY2(swR.value("ok").toBool(), qPrintable("switch_vfo: " + ai::executeTool("switch_vfo", sw, &engine)));

    // Every new tool must dispatch (never the "未知工具" fallback).
    const QStringList newTools = {
        "set_network_audio_sink", "get_network_audio_status",
        "start_scan_link", "stop_scan_link", "get_scan_link_status",
        "set_squelch", "get_squelch_status",
        "set_ctcss", "get_ctcss_status",
        "set_cdcss", "get_cdcss_status",
        "set_ft8", "get_ft8_status",
        "set_lrpt", "get_lrpt_status",
        "set_vna_sweep", "get_vna_data", "get_vna_status",
        "set_noise_blanker", "get_noise_blanker_status",
        "list_bookmarks", "add_bookmark", "tune_to_bookmark", "delete_bookmark",
        "list_vfos", "add_vfo", "switch_vfo", "rename_vfo",
        "list_recordings", "delete_recording", "export_recording",
        "set_fft_params", "set_color_map", "get_spectrum_status",
    };
    for (const QString& t : newTools) {
        const QString r = ai::executeTool(t, QJsonObject{}, &engine);
        QVERIFY2(!r.contains(QString::fromUtf8("未知工具")),
                 qPrintable("new tool not dispatched: " + t + " -> " + r));
    }
}

// Phase26 contract: missing / wrong-typed required args -> honest ok:false,
// never a crash, never a fabricated success.
void TestAiRealLink::phase26_badArgsHonestError() {
    dsp::SpectrumEngine engine;

    // set_fft_params without fft_size.
    QJsonObject fftR = parseObj(ai::executeTool("set_fft_params", QJsonObject{}, &engine));
    QCOMPARE(fftR.value("ok").toBool(), false);

    // switch_vfo without index.
    QCOMPARE(parseObj(ai::executeTool("switch_vfo", QJsonObject{}, &engine))
             .value("ok").toBool(), false);

    // add_bookmark without freq_hz.
    QCOMPARE(parseObj(ai::executeTool("add_bookmark", QJsonObject{}, &engine))
             .value("ok").toBool(), false);

    // set_network_audio_sink without enable.
    QCOMPARE(parseObj(ai::executeTool("set_network_audio_sink", QJsonObject{}, &engine))
             .value("ok").toBool(), false);

    // delete_recording without name.
    QCOMPARE(parseObj(ai::executeTool("delete_recording", QJsonObject{}, &engine))
             .value("ok").toBool(), false);

    // set_noise_blanker without `on` (and with a non-boolean `on`) -> honest error.
    QCOMPARE(parseObj(ai::executeTool("set_noise_blanker", QJsonObject{}, &engine))
             .value("ok").toBool(), false);
    QJsonObject badOn; badOn["on"] = "yes";
    QCOMPARE(parseObj(ai::executeTool("set_noise_blanker", badOn, &engine))
             .value("ok").toBool(), false);

    // Real round-trip: set flips the engine, get reads the real switch back.
    QJsonObject nb; nb["on"] = true;
    QJsonObject nbR = parseObj(ai::executeTool("set_noise_blanker", nb, &engine));
    QVERIFY2(nbR.value("ok").toBool(), qPrintable(ai::executeTool("set_noise_blanker", nb, &engine)));
    QJsonObject nbS = parseObj(ai::executeTool("get_noise_blanker_status", QJsonObject{}, &engine));
    QVERIFY(nbS.value("ok").toBool());
    QCOMPARE(nbS.value("enabled").toBool(), true);
}

QTEST_MAIN(TestAiRealLink)
#include "test_ai_real_link.moc"
