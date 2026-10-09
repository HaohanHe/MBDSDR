// SPDX-License-Identifier: MIT
#include "agent_tools.h"
#include "tool_schema.h"
#include "sat_task_planner.h"
#include "core/tokens.h"
#include "dsp/spectrum_engine.h"
#include "dsp/device_capabilities.h"
#include "dsp/frequency_calibrator.h"   // calibrateFromCapture / savePpmSetting
#include "dsp/fcch_detector.h"          // kFcchToneHz
#include "ui/bookmark_manager.h"   // ui::BookmarkManager / ui::Bookmark (real Agent-layer store)

#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QStringList>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSettings>
#include <cmath>

namespace mbdsdr {
namespace ai {

QString gatedToolResult(const QString& toolName) {
    QJsonObject o;
    o["ok"] = false;
    o["gated"] = true;
    o["error"] = QString("手动模式：未执行 %1").arg(toolName);
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

// Honest source snapshot shared by every tool result, so the model always knows
// whether it is acting on real hardware or the offline test signal (A4 P1/P2:
// the source layer already knows this; the tool result must TELL the model).
namespace {
struct SourceInfo {
    bool connected = false;
    bool testSignal = false;
    QString sourceName;   // human label: real device / 测试信号 / 未连接
};

SourceInfo readSourceInfo(dsp::SpectrumEngine* engine) {
    SourceInfo s;
    const dsp::DeviceCapabilities caps = engine->sourceCapabilities();
    s.connected = caps.connected;
    s.testSignal = engine->isTestSignalActive();
    if (s.testSignal) {
        s.sourceName = QString::fromUtf8("测试信号（合成 IQ，非真实硬件）");
    } else if (s.connected) {
        s.sourceName = caps.deviceName.isEmpty()
            ? QString::fromUtf8("已连接设备") : caps.deviceName;
    } else {
        s.sourceName = QString::fromUtf8("未连接");
    }
    return s;
}

void addSourceFields(QJsonObject& o, const SourceInfo& s) {
    o["connected"] = s.connected;
    o["test_signal"] = s.testSignal;
    o["source"] = s.sourceName;
}

QString compact(const QJsonObject& o) {
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}

// Forward declaration: the honest-error helper is defined in the Phase26 block
// below (~line 570); the early executors (tune_frequency / set_mode) reuse it.
namespace {
QString errResult(const QString& msg);
}

// ---- Built-in tool executors ------------------------------------------------
// Each was one branch of the old if-else chain in executeTool(). They are now
// plain functions bound into the registry table below; the bodies are verbatim
// (same JSON keys, same order, same messages) so on-wire output never drifts.
// `src` is the honest source snapshot read once per call and injected here.

QString execTuneFrequency(const QJsonObject& args, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double f = args["freq_hz"].toDouble();
    // Honest guard (parity with the engine guard spectrum_engine.cpp:214 and CH
    // needDbl): a non-positive / non-finite frequency must be REJECTED here with
    // ok:false -- otherwise the engine drops it silently while we echo f back as
    // success and the next get_status still reports the old value (fake success).
    if (!(f > 0.0) || !std::isfinite(f))
        return errResult(QString::fromUtf8("频率必须为正的有限数"));
    engine->onSetCenterFreq(f);
    QJsonObject o;
    o["ok"] = true;
    o["frequency_hz"] = f;
    o["message"] = QString("已调谐到 %1 MHz").arg(f / 1e6, 0, 'f', 3);
    addSourceFields(o, src);
    return compact(o);
}

QString execSetMode(const QJsonObject& args, dsp::SpectrumEngine* engine,
                    const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    // Whitelist (parity with CH needMode and execSetVfoMode): the engine's
    // VfoManager accepts ANY mode string, so without this gate an unknown mode
    // would be echoed as applied while the demod never really changes -- reject
    // it honestly. Echo the canonical upper-case name like the other channels.
    const QString up = args["mode"].toString().toUpper();
    bool known = false;
    for (int k = 0; k < tokens::kControlHubModesCount; ++k)
        if (up == QLatin1String(tokens::kControlHubModes[k])) { known = true; break; }
    if (!known)
        return errResult(QString::fromUtf8("未知解调模式: %1")
                             .arg(args["mode"].toString()));
    engine->setDemodMode(up);
    QJsonObject o;
    o["ok"] = true;
    o["mode"] = up;
    o["message"] = QString("解调模式切换为 %1").arg(up);
    addSourceFields(o, src);
    return compact(o);
}

QString execStartRecording(const QJsonObject& /*args*/, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    // Propagate the engine's real bool: a failed recorder must NOT be
    // reported as "开始录制" (A4 P1-4). On the offline test source the
    // recorder really does write a SigMF file (sidecar labels it
    // "Test Signal"); that success is honest, but a failure is now honest too.
    bool ok = engine->startRecording();
    QJsonObject o;
    o["ok"] = ok;
    if (ok) {
        o["message"] = QString("开始录制");
        o["path"] = engine->recordingPath();
        addSourceFields(o, src);
    } else {
        o["error"] = QString("录制启动失败");
    }
    return compact(o);
}

QString execStopRecording(const QJsonObject& /*args*/, dsp::SpectrumEngine* engine,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    engine->stopRecording();
    QJsonObject o;
    o["ok"] = true;
    o["message"] = QString("停止录制");
    addSourceFields(o, src);
    return compact(o);
}

// WRITE action: one-shot IQ export to a cf32_le SigMF file on disk (gated in
// manual mode). Distinct from start_recording: it dumps a bounded, on-demand
// window NOW and returns, rather than recording continuously until stopped.
// No source data -> honest ok:false (never a fabricated empty file).
QString execExportIqSegment(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    int sampleCount = static_cast<int>(args["sample_count"].toDouble(65536.0));
    double tuneHz = -1.0;   // default: keep the current centre
    if (args.contains("tune_hz") && !args["tune_hz"].isNull()) {
        if (!args["tune_hz"].isDouble()) {
            QJsonObject o;
            o["ok"] = false;
            o["error"] = QString::fromUtf8("参数 tune_hz 必须是数字（Hz，缺省=保持当前中心）");
            return compact(o);
        }
        tuneHz = args["tune_hz"].toDouble();
    }

    QString path; double sr = 0.0, center = 0.0;
    qint64 samples = 0, bytes = 0; QString err;
    const bool ok = engine->exportIqSegment(sampleCount, tuneHz, path,
                                            sr, center, samples, bytes, err);
    QJsonObject o;
    o["ok"] = ok;
    if (ok) {
        o["message"] = QString::fromUtf8("已导出 IQ 段：%1 个复样本（%2 字节）-> %3")
                           .arg(samples).arg(bytes).arg(path);
        o["path"] = path;
        o["sample_rate_hz"] = sr;
        o["center_hz"] = center;
        o["samples"] = samples;
        o["bytes"] = bytes;
        addSourceFields(o, src);
    } else {
        o["error"] = err;
    }
    return compact(o);
}

QString execScanBand(const QJsonObject& args, dsp::SpectrumEngine* engine,
                     const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double low = args["low_hz"].toDouble();
    double high = args["high_hz"].toDouble();
    double step = args["step_hz"].toDouble(200000);
    double peakFreq = 0.0;
    double peak = engine->scanBand(low, high, step, &peakFreq);
    // Structured result so downstream task steps can reference the real hit
    // frequency via JSON path (hits[0].frequencyHz).
    QJsonObject hit;
    hit["frequencyHz"] = peakFreq;
    hit["dbfs"] = peak;
    QJsonObject o;
    o["ok"] = true;
    o["lowHz"] = low;
    o["highHz"] = high;
    o["stepHz"] = step;
    o["peakDbfs"] = peak;
    o["hits"] = QJsonArray{hit};
    addSourceFields(o, src);
    // On the offline test signal the synth tone sits at a fixed baseband
    // offset independent of the swept center frequency, so the "peak" has no
    // physical meaning as an RF station hit. Label it honestly (A4 P1-3).
    if (src.testSignal) {
        o["synthetic"] = true;
        o["note"] = QString::fromUtf8(
            "合成测试信号扫描：峰值为固定基带偏音，与扫频中心无关，非真实电台命中");
    }
    return compact(o);
}

QString execSetBandwidth(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double bw = args["bandwidth_hz"].toDouble();
    engine->setBandwidth(bw);
    QJsonObject o;
    o["ok"] = true;
    o["bandwidth_hz"] = bw;
    o["message"] = QString("带宽设为 %1 Hz").arg(bw, 0, 'f', 0);
    addSourceFields(o, src);
    return compact(o);
}

QString execGetStatus(const QJsonObject& /*args*/, dsp::SpectrumEngine* engine,
                      const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    // Aligned with Flutter ai_tools.dart get_status: connected + source, plus
    // the read-back values. We do NOT fabricate gain/sample-rate the engine
    // does not expose. A human-readable summary is kept so existing UI copy
    // and callers still find "频率"/mode text.
    QJsonObject o;
    o["ok"] = true;
    o["connected"] = src.connected;
    o["test_signal"] = src.testSignal;
    o["source"] = src.sourceName;
    o["frequency_hz"] = engine->centerFreq();
    o["mode"] = engine->demodMode();
    o["bandwidth_hz"] = engine->bandwidth();
    // Phase55 block2: Costas carrier-lock snapshot of the selected VFO. On an
    // analog (non-BPSK/QPSK) channel the engine returns an honest all-false
    // lock status -- we never fabricate a lock.
    const dsp::DigitalLockStatus lock = engine->digitalLockStatus();
    o["carrier_locked"] = lock.carrierLocked;
    o["symbol_locked"] = lock.symbolLocked;
    o["evm_percent"] = lock.evmPercent;
    // Phase55 block3: live Doppler compensation state via the UI control surface.
    auto* surf = engine->dopplerControlSurface();
    o["doppler_available"] = surf && surf->isDopplerCompensationAvailable();
    o["doppler_enabled"] = surf && surf->isDopplerCompensationEnabled();
    o["summary"] = QString("频率=%1MHz 模式=%2 带宽=%3Hz 连接=%4")
        .arg(engine->centerFreq() / 1e6, 0, 'f', 3)
        .arg(engine->demodMode())
        .arg(engine->bandwidth(), 0, 'f', 0)
        .arg(src.connected ? QString::fromUtf8("已连接")
                           : QString::fromUtf8("未连接"));
    return compact(o);
}

QString execPredictPasses(const QJsonObject& args, dsp::SpectrumEngine* /*engine*/,
                           const SourceInfo& /*src*/,
                        ui::BookmarkManager* /*bookmarks*/) {
    // Read-only: does NOT touch the radio. Uses the FRESH on-disk TLE cache
    // via the planner; no builtin/demo TLE is reported as a real pass.
    QString sat = args["satellite_name"].toString();
    int hours = static_cast<int>(args["hours_ahead"].toDouble(24.0));
    if (hours < 1) hours = 24;
    bool hasLat = args.contains("station_lat_deg") && args["station_lat_deg"].isDouble();
    bool hasLon = args.contains("station_lon_deg") && args["station_lon_deg"].isDouble();
    double lat = hasLat ? args["station_lat_deg"].toDouble() : qQNaN();
    double lon = hasLon ? args["station_lon_deg"].toDouble() : qQNaN();

    SatPassListResult r = predictSatellitePasses(
        sat, lat, lon, QDateTime::currentDateTimeUtc(), hours);
    QJsonObject o;
    if (!r.ok) {
        o["ok"] = false;
        o["error"] = r.error;
        o["source"] = r.source.isEmpty()
            ? QString::fromUtf8("无新鲜 TLE 缓存") : r.source;
        return compact(o);
    }
    o["ok"] = true;
    o["source"] = r.source;   // "cached_tle" -- honest about the data provenance
    QJsonArray arr;
    for (const SatPassEntry& e : r.passes) {
        QJsonObject p;
        p["name"] = e.name;
        p["catalog_number"] = e.catalogNumber;
        p["rise_time"] = e.aosUtc.toUTC().toString(Qt::ISODate);
        p["rise_az"] = e.azAos;
        p["set_time"] = e.losUtc.toUTC().toString(Qt::ISODate);
        p["set_az"] = e.azLos;
        p["max_el"] = e.maxEl;
        arr.append(p);
    }
    o["passes"] = arr;
    return compact(o);
}

// Default capture length for a calibration measurement (complex samples).
// 32768 splits into 4 independent 8192-point FFT blocks; at the common 2.048e6
// rate that gives ~250 Hz bins refined by parabolic interpolation to well under
// 1 ppm at VHF/UHF, and it also holds up on narrower offline rates. Named so
// the call site never invents a magic count.
constexpr int kCalibrationDefaultSamples = 32768;
// Minimum useful capture: below this even one 512-point FFT cannot be split.
constexpr int kCalibrationMinSamples = 4096;

// READ-ONLY measurement. Tunes the source to the reference, pulls one capture,
// and estimates the crystal ppm against the expected baseband position. It does
// NOT persist anything; the gated apply_frequency_correction does that. On a
// test / offline source the data is honestly-labelled generated IQ (never
// presented as a real antenna capture).
QString execCalibrateFrequency(const QJsonObject& args, dsp::SpectrumEngine* engine,
                               const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const double refFreq = args["reference_freq_hz"].toDouble();
    const QString type = args["reference_type"].toString();
    int sampleCount = static_cast<int>(args["sample_count"].toDouble(kCalibrationDefaultSamples));
    if (sampleCount < kCalibrationMinSamples) sampleCount = kCalibrationDefaultSamples;

    // Map the reference kind to the expected baseband position. handheld/manual
    // expect the reference at baseband DC (0); GSM FCCH sits exactly one symbol-
    // rate/4 tone above the tuned ARFCN centre.
    dsp::CalibrationReference ref;
    double expectedBaseband = 0.0;
    if (type == QStringLiteral("gsm_fcch")) {
        ref = dsp::CalibrationReference::GsmFcch;
        expectedBaseband = dsp::kFcchToneHz;
    } else if (type == QStringLiteral("handheld")) {
        ref = dsp::CalibrationReference::HandheldGuided;
        expectedBaseband = 0.0;
    } else {
        ref = dsp::CalibrationReference::Manual;
        expectedBaseband = 0.0;
    }

    std::vector<std::complex<float>> iq;
    double sr = 0.0, centre = 0.0;
    const std::size_t got = engine->captureForCalibration(
        refFreq, sampleCount, iq, sr, centre);

    QJsonObject o;
    o["ok"] = true;
    o["reference_type"] = type;
    o["reference_freq_hz"] = refFreq;
    o["applied"] = false;
    addSourceFields(o, src);

    if (got < static_cast<std::size_t>(kCalibrationMinSamples) || iq.empty()) {
        // Honest empty state: the source delivered too little to measure.
        o["detected"] = false;
        o["error"] = QString::fromUtf8("采集样本不足，无法测量（源无数据）");
        o["hint"] = QString::fromUtf8("请确认已连接设备或打开离线文件，并让参考信号处于该频点");
        return compact(o);
    }

    dsp::CalibratorConfig cfg;
    cfg.centreFreqHz = refFreq;
    cfg.expectedBasebandHz = expectedBaseband;
    const dsp::CalibrationResult res =
        dsp::calibrateFromCapture(iq, sr, ref, cfg, /*segments=*/4);

    o["detected"] = res.detected;
    if (res.detected) {
        o["measured_ppm"] = res.ppm;
        o["confidence"] = res.confidence;
        o["delta_hz"] = res.meanOffsetHz;
        o["spread_ppm"] = res.spreadPpm;
        o["snr_db"] = res.worstSnrDb;
        o["measurements_used"] = res.measurementsUsed;
        o["hint"] = QString::fromUtf8("如需应用，请调用 apply_frequency_correction");
    } else {
        // Never fabricate a ppm: carry the honest "no reference carrier" status.
        o["hint"] = res.status;
    }
    return compact(o);
}

// WRITE action: persist + apply a measured ppm correction. Gated in manual
// mode (see llm_worker::dispatchToolCall). Reads the previous stored value so
// the before/after comparison is real.
QString execApplyFrequencyCorrection(const QJsonObject& args,
                                     dsp::SpectrumEngine* engine,
                                     const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const double ppm = args["ppm"].toDouble();
    const double previous = dsp::currentPpmSetting();
    dsp::savePpmSetting(ppm);
    engine->setPpm(ppm);

    QJsonObject o;
    o["ok"] = true;
    o["applied"] = true;
    o["previous_ppm"] = previous;
    o["applied_ppm"] = ppm;
    // After applying the measured correction the residual baseband offset
    // should collapse to ~0; with no live re-measurement here we report the
    // design prediction (the next calibrate_frequency call would confirm).
    o["predicted_residual_hz"] = 0.0;
    o["message"] = QString::fromUtf8("已应用频率校正：%1 ppm（原 %2 ppm）")
                       .arg(ppm, 0, 'f', 3)
                       .arg(previous, 0, 'f', 3);
    addSourceFields(o, src);
    return compact(o);
}

// ---------------------------------------------------------------------------
// Wave2 read-only digital decode snapshot tools (POCSAG / m17 / VOR). They pull
// the accumulated decode output of a channel straight off the engine's read-only
// snapshot slots (which take sourceMutex_ so the copy is stable off the engine
// thread). They NEVER tune / gate / mutate the receiver, so they are registered
// write=false (predict_passes-style: never blocked by the manual gate). Unknown
// channel / non-matching mode / nothing decoded yet -> an HONEST empty state:
// an empty array, or locked=false for VOR. No fabricated message / call /
// bearing, and no pre-stored station.
//
// channel_id defaults to the currently selected VFO when omitted.
namespace {
int resolveChannelId(const QJsonObject& args, dsp::SpectrumEngine* engine) {
    const QJsonValue v = args.value(QStringLiteral("channel_id"));
    if (v.isDouble()) return static_cast<int>(v.toDouble());
    const QJsonValue c = args.value(QStringLiteral("channel"));
    if (c.isDouble()) return static_cast<int>(c.toDouble());
    return engine->selectedVfoId();
}

// Phase63 D1 bilateral alias: resolve a VFO write target from EITHER
//   - "index" (marker ordinal in vfoMarkers(), the Agent LLM schema key), or
//   - "id"    (direct VFO id, the ControlHub/HTTP key).
// On success fills outIndex/outId and returns true; on missing/bad input
// returns false with an honest error message. When `id` is given directly we
// do NOT bounds-check it against the marker list (the engine will reject an
// unknown id) -- but we DO fill outIndex by searching the markers so the
// response echo stays informative.
bool resolveVfoTarget(const QJsonObject& args, dsp::SpectrumEngine* engine,
                      int& outIndex, int& outId, QString& err) {
    const QJsonValue idxV = args.value(QStringLiteral("index"));
    const QJsonValue idV  = args.value(QStringLiteral("id"));
    const auto markers = engine->vfoMarkers();
    if (idxV.isDouble()) {
        const int i = static_cast<int>(idxV.toDouble());
        if (i < 0 || i >= markers.size()) {
            err = QString::fromUtf8("VFO index %1 越界（共 %2 个）").arg(i).arg(markers.size());
            return false;
        }
        outIndex = i;
        outId = markers[i].id;
        return true;
    }
    if (idV.isDouble()) {
        const int id = static_cast<int>(idV.toDouble());
        // Locate the marker ordinal for a useful echo; -1 if not found.
        outIndex = -1;
        for (int k = 0; k < markers.size(); ++k)
            if (markers[k].id == id) { outIndex = k; break; }
        outId = id;
        return true;
    }
    err = QString::fromUtf8("参数 index 或 id 必须提供一个（VFO 序号或 VFO id）");
    return false;
}
} // namespace

QString execGetPocsagMessages(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const int channelId = resolveChannelId(args, engine);
    const std::vector<dsp::PocsagMessage> msgs = engine->pocsagMessages(channelId);
    QJsonArray arr;
    for (const dsp::PocsagMessage& m : msgs) {
        QJsonObject o;
        o["address"] = static_cast<qint64>(m.address);   // RIC 0..2097151
        o["function"] = m.function;
        const char* typeStr = "unknown";
        switch (m.type) {
            case dsp::PocsagMessage::Type::Numeric: typeStr = "numeric"; break;
            case dsp::PocsagMessage::Type::Alpha:   typeStr = "alpha";   break;
            case dsp::PocsagMessage::Type::Unknown:
            default:                                typeStr = "unknown"; break;
        }
        o["type"] = QString::fromLatin1(typeStr);
        o["text"] = QString::fromStdString(m.text);
        arr.append(o);
    }
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["count"] = static_cast<int>(arr.size());
    out["messages"] = arr;   // empty array = honest empty state
    addSourceFields(out, src);
    return compact(out);
}

QString execGetM17Calls(const QJsonObject& args, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const int channelId = resolveChannelId(args, engine);
    const std::vector<dsp::M17Call> calls = engine->m17Calls(channelId);
    QJsonArray arr;
    for (const dsp::M17Call& c : calls) {
        QJsonObject o;
        o["src"] = QString::fromStdString(c.src);
        o["dst"] = QString::fromStdString(c.dst);
        o["type"] = static_cast<int>(c.type);          // raw LSF TYPE word
        o["is_stream"] = c.isStream;
        o["payload_class"] = c.payloadClass;
        o["frame_kind"] = c.frameKind;
        o["crc_ok"] = c.crcOk;
        o["voice_undecoded"] = c.voiceUndecoded;        // Codec2 not decoded: honest
        o["meta_size"] = static_cast<int>(c.meta.size());
        o["payload_size"] = static_cast<int>(c.payload.size());
        arr.append(o);
    }
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["count"] = static_cast<int>(arr.size());
    out["calls"] = arr;      // empty array = honest empty state
    addSourceFields(out, src);
    return compact(out);
}

QString execGetAcarsPackets(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const int channelId = resolveChannelId(args, engine);
    const std::vector<dsp::AcarsPacket> pkts = engine->acarsPackets(channelId);
    QJsonArray arr;
    for (const dsp::AcarsPacket& p : pkts) {
        QJsonObject o;
        const char* dir = "unknown";
        switch (p.direction) {
            case dsp::AcarsPacket::Direction::Air:    dir = "air";    break;
            case dsp::AcarsPacket::Direction::Ground: dir = "ground"; break;
            default:                                  dir = "unknown"; break;
        }
        o["direction"] = QString::fromLatin1(dir);
        o["mode"]     = QString::fromStdString(p.mode);
        o["label"]    = QString::fromStdString(p.label);
        o["block_id"] = QString::fromStdString(p.blockId);
        o["ack"]      = QString::fromStdString(p.ack);
        o["text"]     = QString::fromStdString(p.text);
        o["crc_ok"]   = p.crcOk;   // honest block-check result
        arr.append(o);
    }
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["count"] = static_cast<int>(arr.size());
    out["packets"] = arr;   // empty array = honest empty state
    addSourceFields(out, src);
    return compact(out);
}

QString execGetNavtexMessages(const QJsonObject& args, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const int channelId = resolveChannelId(args, engine);
    const std::vector<dsp::NavtexMessage> msgs = engine->navtexMessages(channelId);
    QJsonArray arr;
    for (const dsp::NavtexMessage& m : msgs) {
        QJsonObject o;
        o["station"] = QString::fromStdString(m.stationB1);
        o["type"]    = QString::fromStdString(m.typeB2);
        o["number"]  = QString::fromStdString(m.numberB3B4);
        o["text"]    = QString::fromStdString(m.text);
        o["diversity_ok"] = m.diversityOk;
        o["phasing_ok"]   = m.phasingOk;
        o["diversity_errors"] = m.diversityErrors;
        arr.append(o);
    }
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["count"] = static_cast<int>(arr.size());
    out["messages"] = arr;
    addSourceFields(out, src);
    return compact(out);
}

QString execGetVorRadial(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const int channelId = resolveChannelId(args, engine);
    const dsp::VorResult v = engine->vorResult(channelId);
    QJsonObject out;
    out["ok"] = true;
    out["channel_id"] = channelId;
    out["locked"] = v.locked;
    // Honest empty state: radial/quality are only meaningful when locked. When
    // unlocked we refuse to invent a bearing and say so explicitly.
    if (v.locked) {
        out["radial_deg"] = v.radialDeg;
        out["quality"] = v.quality;
    } else {
        out["radial_deg"] = QJsonValue(QJsonValue::Null);
        out["quality"] = QJsonValue(QJsonValue::Null);
        out["note"] = QString::fromUtf8("未锁定 VOR 台，方位不可信（不编造方位）");
    }
    out["morse_id"] = v.morseId;
    addSourceFields(out, src);
    return compact(out);
}

// ---------------------------------------------------------------------------
// Phase26: capability-everything-as-tools (21 tools, three-channel parity).
// Write tools are gated upstream by llm_worker::isWriteTool -- same semantics as
// the existing 14. Where the engine ALREADY exposes the backing (squelch / VFO /
// FFT / recDir_ / QSettings) we drive it for real; where the back-end lands on
// the parallel control/ A block (network-audio tap, ScanActivityLink, the
// BookmarkManager wiring, VFO rename) we return an honest routed/pending result
// rather than fabricate an effect. Bad/missing args are honest errors.
namespace {
QString errResult(const QString& msg) {
    QJsonObject o; o["ok"] = false; o["error"] = msg; return compact(o);
}
bool needNum(const QJsonObject& a, const char* key, double& out) {
    if (!a.contains(QLatin1String(key)) || !a.value(QLatin1String(key)).isDouble())
        return false;
    out = a.value(QLatin1String(key)).toDouble();
    return true;
}
bool needStr(const QJsonObject& a, const char* key, QString& out) {
    if (!a.contains(QLatin1String(key)) || !a.value(QLatin1String(key)).isString())
        return false;
    out = a.value(QLatin1String(key)).toString();
    return true;
}
// Accepted by the AI layer; the real effect is the ControlHub command of the
// same name on the three-channel side. Echo the args honestly + source fields.
// The note wording is unified HERE (single point) so every routed tool reports
// the identical contract: routed, not locally executed, must be confirmed on
// the UI / ControlHub channel.
QString routedOk(const char* command, const QJsonObject& echoed, const SourceInfo& src) {
    QJsonObject o = echoed;
    o["ok"] = true;
    o["routed_command"] = QString::fromLatin1(command);
    o["note"] = QString::fromUtf8("命令 %1 已路由，实际效果由 ControlHub 通道落地，需在 UI 或 ControlHub 确认执行")
                    .arg(QString::fromLatin1(command));
    addSourceFields(o, src);
    return compact(o);
}
} // namespace

// 1. set_network_audio_sink (write): enable/port/format. The engine network tap
//    is wired on the control side; here we validate + route by contract name.
//    Phase63 D5: echo host/stereo so the routed stub does not silently drop
//    them (CH cmdSetNetworkAudioSink already consumes them).
QString execSetNetworkAudioSink(const QJsonObject& args, dsp::SpectrumEngine*,
                                const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double port = 0.0;
    if (!args.contains("enable") || !args.value("enable").isBool())
        return errResult(QString::fromUtf8("参数 enable 缺失或不是布尔值"));
    if (!needNum(args, "port", port))
        return errResult(QString::fromUtf8("参数 port 缺失或不是数字"));
    QJsonObject echo;
    echo["enable"] = args.value("enable").toBool();
    echo["port"] = port;
    if (args.contains("format") && args.value("format").isString())
        echo["format"] = args.value("format").toString();
    if (args.contains("host") && args.value("host").isString())
        echo["host"] = args.value("host").toString();
    if (args.contains("stereo") && args.value("stereo").isBool())
        echo["stereo"] = args.value("stereo").toBool();
    return routedOk("set_network_audio_sink", echo, src);
}

// 2. get_network_audio_status (read): no engine status slot yet -> honest off.
QString execGetNetworkAudioStatus(const QJsonObject&, dsp::SpectrumEngine*,
                                  const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = false;
    o["note"] = QString::fromUtf8("无网络音频流状态读数（后端由 ControlHub 提供）");
    addSourceFields(o, src);
    return compact(o);
}

// 3. start_scan_link (write): target_freq_hz. ScanActivityLink lives on control.
QString execStartScanLink(const QJsonObject& args, dsp::SpectrumEngine*,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double tgt = 0.0;
    if (!needNum(args, "target_freq_hz", tgt))
        return errResult(QString::fromUtf8("参数 target_freq_hz 缺失或不是数字"));
    QJsonObject echo; echo["target_freq_hz"] = tgt;
    return routedOk("start_scan_link", echo, src);
}

// 4. stop_scan_link (write).
QString execStopScanLink(const QJsonObject&, dsp::SpectrumEngine*,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    return routedOk("stop_scan_link", QJsonObject{}, src);
}

// 5. get_scan_link_status (read): no link instance on the engine -> honest idle.
QString execGetScanLinkStatus(const QJsonObject&, dsp::SpectrumEngine*,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonObject o;
    o["ok"] = true;
    o["scanning"] = false;
    o["dwelling"] = false;
    o["hit"] = QJsonValue(QJsonValue::Null);
    o["note"] = QString::fromUtf8("扫描活动链路未运行（后端由 ControlHub 提供）");
    addSourceFields(o, src);
    return compact(o);
}

// 6. set_squelch (write): the engine exposes setSquelchEnabled / Threshold AND
//    setSquelchAuto -- every field drives its real setter (parity with CH
//    cmdSetSquelch), so get_squelch_status reads the real value back.
QString execSetSquelch(const QJsonObject& args, dsp::SpectrumEngine* engine,
                       const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonObject o;
    o["ok"] = true;
    if (args.contains("enabled") && args.value("enabled").isBool()) {
        engine->setSquelchEnabled(args.value("enabled").toBool());
        o["enabled"] = args.value("enabled").toBool();
    }
    if (args.contains("threshold_db") && args.value("threshold_db").isDouble()) {
        engine->setSquelchThreshold(static_cast<float>(args.value("threshold_db").toDouble()));
        o["threshold_db"] = args.value("threshold_db").toDouble();
    }
    if (args.contains("auto") && args.value("auto").isBool()) {
        engine->setSquelchAuto(args.value("auto").toBool());
        o["auto"] = args.value("auto").toBool();
    }
    o["message"] = QString::fromUtf8("静噪参数已下发（门限/使能）");
    addSourceFields(o, src);
    return compact(o);
}

// 7. get_squelch_status (read): the engine DOES expose public readback
// (squelchEnabled/ThresholdDb/Auto/Open -- the same getters ControlHub's
// cmdGetSquelchStatus uses), so we return the REAL values rather than hardcoded
// nulls. On a fresh/no-device engine these read back honest defaults
// (off / -50 dB / auto off / closed), identical to the CH/HTTP wire.
QString execGetSquelchStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                             const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = engine->squelchEnabled();
    o["threshold_db"] = engine->squelchThresholdDb();
    o["auto"] = engine->squelchAuto();
    o["open"] = engine->squelchOpen();
    addSourceFields(o, src);
    return compact(o);
}

// 7b. set_noise_blanker (write): the engine exposes setNoiseBlanker(bool) and the
//     UI checkbox already drives it; wire the real setter. `on` is required and
//     must be a boolean -- missing/non-bool is an honest error, never a default.
QString execSetNoiseBlanker(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    if (!args.contains("on") || !args.value("on").isBool())
        return errResult(QString::fromUtf8("参数 on 缺失或不是布尔值"));
    const bool on = args.value("on").toBool();
    engine->setNoiseBlanker(on);
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = on;
    o["message"] = QString::fromUtf8("噪声抑制开关已下发");
    addSourceFields(o, src);
    return compact(o);
}

// 7c. get_noise_blanker_status (read): the engine DOES expose noiseBlankerEnabled()
//     (unlike squelch), so we return the real switch rather than a null.
QString execGetNoiseBlankerStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                                 const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonObject o;
    o["ok"] = true;
    o["enabled"] = engine->noiseBlankerEnabled();
    addSourceFields(o, src);
    return compact(o);
}

// 8. list_bookmarks (read): BookmarkManager wiring lands on control -> honest empty.
QString execListBookmarks(const QJsonObject&, dsp::SpectrumEngine*,
                          const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonObject o;
    o["ok"] = true;
    o["bookmarks"] = QJsonArray{};
    o["count"] = 0;
    o["note"] = QString::fromUtf8("书签后端由 BookmarkManager（control 层）提供");
    addSourceFields(o, src);
    return compact(o);
}

// 9. add_bookmark (write): REAL execution against the injected BookmarkManager.
//    freq_hz required; mode / bandwidth_hz / group / name optional. A null store
//    is an honest error (never a routed stub); add() itself rejects freq<=0.
QString execAddBookmark(const QJsonObject& args, dsp::SpectrumEngine*,
                        const SourceInfo& src,
                        ui::BookmarkManager* bookmarks) {
    double f = 0.0;
    if (!needNum(args, "freq_hz", f))
        return errResult(QString::fromUtf8("参数 freq_hz 缺失或不是数字"));
    if (!bookmarks)
        return errResult(QString::fromUtf8("书签管理器未注入（Agent 层无书签存储，未执行）"));
    ui::Bookmark b;
    b.frequencyHz = f;
    if (args.contains("mode") && args.value("mode").isString())
        b.mode = args.value("mode").toString();
    if (args.contains("bandwidth_hz") && args.value("bandwidth_hz").isDouble())
        b.bandwidthHz = args.value("bandwidth_hz").toDouble();
    if (args.contains("group") && args.value("group").isString())
        b.group = args.value("group").toString();
    if (args.contains("name") && args.value("name").isString())
        b.name = args.value("name").toString();
    const int idx = bookmarks->add(b);   // auto-sorts by (group, freq) + saves
    QJsonObject o;
    if (idx < 0) {
        o["ok"] = false;
        o["error"] = QString::fromUtf8("频率非法：freq_hz 必须大于 0（书签已拒绝入册）");
        addSourceFields(o, src);
        return compact(o);
    }
    o["ok"] = true;
    o["index"] = idx;
    o["count"] = bookmarks->count();
    o["freq_hz"] = f;
    o["message"] = QString::fromUtf8("书签已写入（排序后落位 %1 / 共 %2 条）")
                       .arg(idx).arg(bookmarks->count());
    addSourceFields(o, src);
    return compact(o);
}

// 10. tune_to_bookmark (write): index required; out-of-range is an honest error.
//     On a hit the SELECTED VFO is retuned to the bookmark's frequency through
//     the same in-band IF-offset path the band-box drag uses (vfoSetOffset).
QString execTuneToBookmark(const QJsonObject& args, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                           ui::BookmarkManager* bookmarks) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    if (!bookmarks)
        return errResult(QString::fromUtf8("书签管理器未注入（Agent 层无书签存储，未执行）"));
    const int i = static_cast<int>(idx);
    if (i < 0 || i >= bookmarks->count())
        return errResult(QString::fromUtf8("书签 index %1 越界（共 %2 条）")
                         .arg(i).arg(bookmarks->count()));
    const ui::Bookmark b = bookmarks->list().at(i);
    const int vfoId = engine->selectedVfoId();
    engine->vfoSetOffset(vfoId, b.frequencyHz);
    QJsonObject o;
    o["ok"] = true;
    o["freq_hz"] = b.frequencyHz;
    o["vfo_index"] = vfoId;
    o["message"] = QString::fromUtf8("已按书签 %1 调谐至 %2 Hz").arg(i).arg(b.frequencyHz);
    addSourceFields(o, src);
    return compact(o);
}

// 11. delete_bookmark (write): index required; out-of-range is an honest error
//     (removeAt itself is silently out-of-bounds, so we check before calling).
QString execDeleteBookmark(const QJsonObject& args, dsp::SpectrumEngine*,
                           const SourceInfo& src,
                           ui::BookmarkManager* bookmarks) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    if (!bookmarks)
        return errResult(QString::fromUtf8("书签管理器未注入（Agent 层无书签存储，未执行）"));
    const int i = static_cast<int>(idx);
    if (i < 0 || i >= bookmarks->count())
        return errResult(QString::fromUtf8("书签 index %1 越界（共 %2 条）")
                         .arg(i).arg(bookmarks->count()));
    bookmarks->removeAt(i);
    QJsonObject o;
    o["ok"] = true;
    o["remaining"] = bookmarks->count();
    o["message"] = QString::fromUtf8("书签 %1 已删除（剩余 %2 条）").arg(i).arg(bookmarks->count());
    addSourceFields(o, src);
    return compact(o);
}

// 12. list_vfos (read): engine exposes vfoMarkers() for real.
QString execListVfos(const QJsonObject&, dsp::SpectrumEngine* engine,
                     const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonArray arr;
    for (const dsp::VfoMarker& m : engine->vfoMarkers()) {
        QJsonObject v;
        v["id"] = m.id;
        v["freq_hz"] = m.freqHz;
        v["bandwidth_hz"] = m.bandwidthHz;
        v["mode"] = m.mode;
        v["name"] = m.name;
        v["selected"] = m.selected;
        v["armed"] = m.armed;
        arr.append(v);
    }
    QJsonObject o;
    o["ok"] = true;
    o["count"] = static_cast<int>(arr.size());
    o["selected_vfo_id"] = engine->selectedVfoId();
    o["vfos"] = arr;
    addSourceFields(o, src);
    return compact(o);
}

// 13. add_vfo (write): engine vfoAdd() is real.
QString execAddVfo(const QJsonObject&, dsp::SpectrumEngine* engine,
                   const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    engine->vfoAdd();
    QJsonObject o;
    o["ok"] = true;
    o["selected_vfo_id"] = engine->selectedVfoId();
    o["message"] = QString::fromUtf8("已新增 VFO 信道");
    addSourceFields(o, src);
    return compact(o);
}

// 14. switch_vfo (write): index required; engine vfoSelect() is real.
QString execSwitchVfo(const QJsonObject& args, dsp::SpectrumEngine* engine,
                      const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    engine->vfoSelect(static_cast<int>(idx));
    QJsonObject o;
    o["ok"] = true;
    o["index"] = static_cast<int>(idx);
    o["selected_vfo_id"] = engine->selectedVfoId();
    o["message"] = QString::fromUtf8("已切换到 VFO %1").arg(static_cast<int>(idx));
    addSourceFields(o, src);
    return compact(o);
}

// 15. rename_vfo (write): index/name required; the vfo_manager rename interface
//     lands on the control side -> validated + routed.
QString execRenameVfo(const QJsonObject& args, dsp::SpectrumEngine*,
                      const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    QString name;
    if (!needStr(args, "name", name))
        return errResult(QString::fromUtf8("参数 name 缺失或不是字符串"));
    QJsonObject echo; echo["index"] = idx; echo["name"] = name;
    return routedOk("rename_vfo", echo, src);
}

// 16. set_vfo_armed (write): index + enabled required. Keeps a VFO demodulated in
//     the background (parallel monitoring) after another VFO is selected. Engine
//     vfoSetArmed() is real (forwarder to VfoManager::setArmed).
QString execSetVfoArmed(const QJsonObject& args, dsp::SpectrumEngine* engine,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double idx = 0.0;
    if (!needNum(args, "index", idx))
        return errResult(QString::fromUtf8("参数 index 缺失或不是数字"));
    bool enabled = false;
    if (!args.contains("enabled"))
        return errResult(QString::fromUtf8("参数 enabled 缺失"));
    const QJsonValue v = args.value("enabled");
    if (v.isBool()) enabled = v.toBool();
    else if (v.isDouble()) enabled = v.toDouble() != 0.0;
    else return errResult(QString::fromUtf8("参数 enabled 必须是布尔值"));
    const auto markers = engine->vfoMarkers();
    const int i = static_cast<int>(idx);
    if (i < 0 || i >= markers.size())
        return errResult(QString::fromUtf8("VFO index %1 越界（共 %2 个）")
                         .arg(i).arg(markers.size()));
    const int id = markers[i].id;
    engine->vfoSetArmed(id, enabled);
    QJsonObject o;
    o["ok"] = true;
    o["index"] = i;
    o["vfo_id"] = id;
    o["armed"] = enabled;
    o["message"] = enabled
        ? QString::fromUtf8("VFO %1 将在后台并行监听").arg(i)
        : QString::fromUtf8("已取消 VFO %1 的后台并行监听").arg(i);
    addSourceFields(o, src);
    return compact(o);
}

// 17. set_vfo_frequency (write): index|id + freq_hz. Phase63 D1: accepts EITHER
//     "index" (marker ordinal, Agent schema) OR "id" (direct VFO id, CH/HTTP).
QString execSetVfoFrequency(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double hz = 0.0;
    if (!needNum(args, "freq_hz", hz))
        return errResult(QString::fromUtf8("参数 freq_hz 缺失或不是数字"));
    int i = -1, id = -1; QString err;
    if (!resolveVfoTarget(args, engine, i, id, err)) return errResult(err);
    engine->vfoSetFreq(id, hz);
    QJsonObject o;
    o["ok"] = true;
    o["index"] = i;
    o["vfo_id"] = id;
    o["freq_hz"] = hz;
    o["message"] = QString::fromUtf8("VFO %1 已调谐至 %2 Hz").arg(i).arg(hz);
    addSourceFields(o, src);
    return compact(o);
}

// 18. set_vfo_mode (write): index|id + mode, mode validated against the shared
//     ControlHub mode table. Phase63 D1: accepts "index" OR "id".
QString execSetVfoMode(const QJsonObject& args, dsp::SpectrumEngine* engine,
                       const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QString mode;
    if (!needStr(args, "mode", mode))
        return errResult(QString::fromUtf8("参数 mode 缺失或不是字符串"));
    const QString up = mode.toUpper();
    bool known = false;
    for (int k = 0; k < tokens::kControlHubModesCount; ++k)
        if (up == QLatin1String(tokens::kControlHubModes[k])) { known = true; break; }
    if (!known)
        return errResult(QString::fromUtf8("未知解调模式: %1").arg(mode));
    int i = -1, id = -1; QString err;
    if (!resolveVfoTarget(args, engine, i, id, err)) return errResult(err);
    engine->vfoSetMode(id, up);
    QJsonObject o;
    o["ok"] = true;
    o["index"] = i;
    o["vfo_id"] = id;
    o["mode"] = up;
    o["message"] = QString::fromUtf8("VFO %1 解调模式已切换为 %2").arg(i).arg(up);
    addSourceFields(o, src);
    return compact(o);
}

// 19. set_vfo_bandwidth (write): index|id + bandwidth_hz. Phase63 D1: accepts
//     "index" OR "id"; positive-bandwidth guard preserved.
QString execSetVfoBandwidth(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double bw = 0.0;
    if (!needNum(args, "bandwidth_hz", bw))
        return errResult(QString::fromUtf8("参数 bandwidth_hz 缺失或不是数字"));
    if (bw <= 0.0)
        return errResult(QString::fromUtf8("参数 bandwidth_hz 必须为正数"));
    int i = -1, id = -1; QString err;
    if (!resolveVfoTarget(args, engine, i, id, err)) return errResult(err);
    engine->vfoSetBandwidth(id, bw);
    QJsonObject o;
    o["ok"] = true;
    o["index"] = i;
    o["vfo_id"] = id;
    o["bandwidth_hz"] = bw;
    o["message"] = QString::fromUtf8("VFO %1 带宽已设为 %2 Hz").arg(i).arg(bw);
    addSourceFields(o, src);
    return compact(o);
}

// 20. list_recordings (read): scan the engine recDir_ honestly (empty if absent).
QString execListRecordings(const QJsonObject&, dsp::SpectrumEngine* engine,
                           const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const QString dir = engine->recordingDir();
    QDir d(dir);
    QJsonArray arr;
    if (d.exists()) {
        const QFileInfoList fis = d.entryInfoList(
            QDir::Files | QDir::NoSymLinks, QDir::Name);
        for (const QFileInfo& fi : fis) {
            QJsonObject f;
            f["name"] = fi.fileName();
            f["bytes"] = fi.size();
            f["modified"] = fi.lastModified().toString(Qt::ISODate);
            arr.append(f);
        }
    }
    QJsonObject o;
    o["ok"] = true;
    o["dir"] = dir;
    o["count"] = static_cast<int>(arr.size());   // honest empty state
    o["recordings"] = arr;
    addSourceFields(o, src);
    return compact(o);
}

// 17. delete_recording (write): name required; delete ONLY inside recDir_.
QString execDeleteRecording(const QJsonObject& args, dsp::SpectrumEngine* engine,
                             const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QString name;
    if (!needStr(args, "name", name))
        return errResult(QString::fromUtf8("参数 name 缺失或不是字符串"));
    // Safety: only a bare filename inside recDir_ -- never a path traversal.
    if (name.contains('/') || name.contains('\\') || name == "." || name == "..")
        return errResult(QString::fromUtf8("name 必须是录制目录内的纯文件名"));
    const QString path = QDir(engine->recordingDir()).filePath(name);
    if (!QFileInfo::exists(path))
        return errResult(QString::fromUtf8("录制文件不存在：%1").arg(name));
    QJsonObject o;
    o["ok"] = QFile::remove(path);
    o["name"] = name;
    o["path"] = path;
    if (!o["ok"].toBool()) o["error"] = QString::fromUtf8("删除失败");
    addSourceFields(o, src);
    return compact(o);
}

// 18. export_recording (write): copy a recDir_ file out to out_path (honest).
QString execExportRecording(const QJsonObject& args, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QString name, outPath;
    if (!needStr(args, "name", name))
        return errResult(QString::fromUtf8("参数 name 缺失或不是字符串"));
    if (!needStr(args, "out_path", outPath))
        return errResult(QString::fromUtf8("参数 out_path 缺失或不是字符串"));
    if (name.contains('/') || name.contains('\\'))
        return errResult(QString::fromUtf8("name 必须是录制目录内的纯文件名"));
    const QString srcPath = QDir(engine->recordingDir()).filePath(name);
    if (!QFileInfo::exists(srcPath))
        return errResult(QString::fromUtf8("录制文件不存在：%1").arg(name));
    QJsonObject o;
    o["ok"] = QFile::copy(srcPath, outPath);
    o["name"] = name;
    o["out_path"] = outPath;
    if (!o["ok"].toBool())
        o["error"] = QString::fromUtf8("导出失败（目标已存在或不可写）");
    addSourceFields(o, src);
    return compact(o);
}

// 19. set_fft_params (write): engine setFftSize/setWindowType/setAverageMode real.
//     Phase63 D3 bilateral dual-type: window/average accept BOTH the string
//     enum ("Hann"/"Flattop"/"Blackman", "Off"/"Slow"/"Fast") that the LLM
//     schema advertises AND the raw int (0/1/2) that the ControlHub/HTTP
//     channel has always used. Either type lands on the same engine setter.
namespace {
int mapWindowString(const QString& w) {
    if (w == QLatin1String("Flattop")) return 1;
    if (w == QLatin1String("Blackman")) return 2;
    return 0;   // "Hann" (default / unrecognised falls back to Hann)
}
int mapAverageString(const QString& av) {
    if (av == QLatin1String("Slow")) return 1;
    if (av == QLatin1String("Fast")) return 2;
    return 0;   // "Off"
}
} // namespace
QString execSetFftParams(const QJsonObject& args, dsp::SpectrumEngine* engine,
                         const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    double sz = 0.0;
    if (!needNum(args, "fft_size", sz))
        return errResult(QString::fromUtf8("参数 fft_size 缺失或不是数字"));
    engine->setFftSize(static_cast<int>(sz));
    QJsonObject o;
    o["ok"] = true;
    o["fft_size"] = engine->fftSize();
    // window: accept string enum OR raw int (0=Hann 1=Flattop 2=Blackman).
    const QJsonValue wv = args.value(QStringLiteral("window"));
    if (wv.isString()) {
        const QString w = wv.toString();
        engine->setWindowType(mapWindowString(w));
        o["window"] = w;
    } else if (wv.isDouble()) {
        const int wi = wv.toInt();
        engine->setWindowType(wi);
        o["window"] = wi;
    }
    // average: accept string enum OR raw int (0=Off 1=Slow 2=Fast).
    const QJsonValue av = args.value(QStringLiteral("average"));
    if (av.isString()) {
        const QString avs = av.toString();
        engine->setAverageMode(mapAverageString(avs));
        o["average"] = avs;
    } else if (av.isDouble()) {
        const int ai = av.toInt();
        engine->setAverageMode(ai);
        o["average"] = ai;
    }
    o["message"] = QString::fromUtf8("FFT 参数已设置");
    addSourceFields(o, src);
    return compact(o);
}

// 20. set_color_map (write): persist the colormap file path to QSettings.
QString execSetColorMap(const QJsonObject& args, dsp::SpectrumEngine*,
                        const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QString p;
    if (!needStr(args, "file_path", p))
        return errResult(QString::fromUtf8("参数 file_path 缺失或不是字符串"));
    QSettings().setValue(QStringLiteral("view/wfColormapFile"), p);
    QJsonObject o;
    o["ok"] = true;
    o["file_path"] = p;
    o["message"] = QString::fromUtf8("色板路径已保存到设置（重绘由 UI 持有）");
    addSourceFields(o, src);
    return compact(o);
}

// 21. get_spectrum_status (read): engine fftSize()/windowType()/averageMode() real.
QString execGetSpectrumStatus(const QJsonObject&, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonObject o;
    o["ok"] = true;
    o["fft_size"] = engine->fftSize();
    o["window_type"] = engine->windowType();
    o["average_mode"] = engine->averageMode();
    addSourceFields(o, src);
    return compact(o);
}

// Phase55 block3: toggle live satellite-pass Doppler auto-compensation through
// the abstract surface MainWindow registers on the engine. No surface
// (headless/test) -> honest "not available". The implementation re-checks the
// station/capture preconditions, so a toggle without them stays off.
QString execSetDopplerCompensation(const QJsonObject& args, dsp::SpectrumEngine* engine,
                                  const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonObject o;
    auto* surf = engine->dopplerControlSurface();
    if (!surf) {
        o["ok"] = false;
        o["available"] = false;
        o["error"] = QString::fromUtf8("多普勒补偿不可用（无 UI 控制面；需在桌面端设置本站并捕获过境）");
        addSourceFields(o, src);
        return compact(o);
    }
    const bool on = args.value("enable").toBool(false);
    surf->setDopplerCompensationEnabled(on);
    o["ok"] = true;
    o["available"] = surf->isDopplerCompensationAvailable();
    o["enabled"] = surf->isDopplerCompensationEnabled();
    o["requested_enable"] = on;
    addSourceFields(o, src);
    return compact(o);
}

// Phase58 block2: connect to an rtl_tcp network source. Real TCP handshake +
// RTL0 header; the engine reports the honest socket reason on failure.
QString execConnectNetworkSource(const QJsonObject& args, dsp::SpectrumEngine* engine,
                                 const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    QJsonObject o;
    const QString host = args.value("host").toString();
    if (host.isEmpty()) {
        o["ok"] = false;
        o["error"] = QString::fromUtf8("缺少 host 参数");
        addSourceFields(o, src);
        return compact(o);
    }
    const int port = static_cast<int>(args.value("port").toDouble(1234.0));
    const bool ok = engine->connectRtlTcp(host, static_cast<quint16>(port));
    o["ok"] = ok;
    o["host"] = host;
    o["port"] = port;
    o["source"] = ok ? QString("rtl_tcp %1:%2").arg(host).arg(port)
                     : QString::fromUtf8("连接失败（真实 socket 错误已回传）");
    addSourceFields(o, src);
    return compact(o);
}

// READ-ONLY: real source capability read-back (device name, tunable / sample-rate
// range, discrete gain steps) straight off the ACTIVE source. Honest empty state:
// an offline / test / unconnected source reports connected=false, an EMPTY gains
// array, and an honest provenance note -- we never fabricate a tunable range or a
// gain step table (the engine itself leaves those at 0 / empty for non-real sources).
QString execGetCapabilities(const QJsonObject&, dsp::SpectrumEngine* engine,
                            const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const dsp::DeviceCapabilities c = engine->sourceCapabilities();
    QJsonArray gains;
    for (double g : engine->availableGainsDb()) gains.append(g);   // empty when no real source
    // The struct carries the real source note, but a synthetic test source must
    // be explicitly labelled: the empty-state provenance does not distinguish
    // "synthetic" from "truly unplugged", so say so honestly. A fresh engine whose
    // caps snapshot has not been filled yet leaves provenance empty -> honest
    // "未连接" rather than a blank.
    QString provenance = c.provenance;
    if (src.testSignal)
        provenance = QString::fromUtf8("测试信号源（非硬件，能力表为空）");
    else if (!c.connected && provenance.isEmpty())
        provenance = QString::fromUtf8("未连接");
    QJsonObject o;
    o["ok"] = true;
    o["connected"] = c.connected;
    o["device_name"] = c.deviceName;
    o["tunable_min_hz"] = c.tunableMinHz;
    o["tunable_max_hz"] = c.tunableMaxHz;
    o["sample_rate_min_hz"] = c.sampleRateMinHz;
    o["sample_rate_max_hz"] = c.sampleRateMaxHz;
    o["gains_db"] = gains;
    o["provenance"] = provenance;
    addSourceFields(o, src);
    return compact(o);
}

// READ-ONLY: recording state straight off the engine. `recording` is derived from
// the live recording path (empty = not recording), which is deterministic and
// does not wait for a state-changed signal. watch/dir are real engine getters.
QString execGetRecordingState(const QJsonObject&, dsp::SpectrumEngine* engine,
                              const SourceInfo& src,
                        ui::BookmarkManager* /*bookmarks*/) {
    const QString path = engine->recordingPath();
    QJsonObject o;
    o["ok"] = true;
    o["recording"] = !path.isEmpty();
    o["recording_path"] = path;
    o["watch_enabled"] = engine->watchEnabled();
    o["recording_dir"] = engine->recordingDir();
    addSourceFields(o, src);
    return compact(o);
}

// The built-in tool registry: name -> executor. Learned (mechanism only) from
// SDR++'s registerSource(name, handler) table pattern -- a name-keyed lookup
// instead of an if-else chain. Clean-room reimplementation; no GPL code copied.
// Every name here MUST equal a ToolSchemaSpec name (see test_tool_registry);
// adding a tool = add the declarative spec in tool_schema.cpp + one row here.
struct ToolDispatch {
    const char* name;
    // The 4th arg is the optional injected bookmark store; only the three
    // bookmark tools consume it, every other executor ignores it.
    QString (*exec)(const QJsonObject&, dsp::SpectrumEngine*, const SourceInfo&,
                    ui::BookmarkManager*);
};

const QList<ToolDispatch>& dispatchTable() {
    static const QList<ToolDispatch> kTable = {
        {"tune_frequency", &execTuneFrequency},
        {"set_mode", &execSetMode},
        {"start_recording", &execStartRecording},
        {"stop_recording", &execStopRecording},
        {"export_iq_segment", &execExportIqSegment},
        {"scan_band", &execScanBand},
        {"set_bandwidth", &execSetBandwidth},
        {"get_status", &execGetStatus},
        {"predict_passes", &execPredictPasses},
        {"calibrate_frequency", &execCalibrateFrequency},
        {"apply_frequency_correction", &execApplyFrequencyCorrection},
        {"get_pocsag_messages", &execGetPocsagMessages},
        {"get_m17_calls", &execGetM17Calls},
        {"get_vor_radial", &execGetVorRadial},
        // Phase60 packet-text snapshot tools (read-only, append-only order).
        {"get_acars_packets", &execGetAcarsPackets},
        {"get_navtex_messages", &execGetNavtexMessages},
        // Phase26: 21 capability tools (append-only, on-wire order kept).
        {"set_network_audio_sink", &execSetNetworkAudioSink},
        {"get_network_audio_status", &execGetNetworkAudioStatus},
        {"start_scan_link", &execStartScanLink},
        {"stop_scan_link", &execStopScanLink},
        {"get_scan_link_status", &execGetScanLinkStatus},
        {"set_squelch", &execSetSquelch},
        {"get_squelch_status", &execGetSquelchStatus},
        {"set_noise_blanker", &execSetNoiseBlanker},
        {"get_noise_blanker_status", &execGetNoiseBlankerStatus},
        {"list_bookmarks", &execListBookmarks},
        {"add_bookmark", &execAddBookmark},
        {"tune_to_bookmark", &execTuneToBookmark},
        {"delete_bookmark", &execDeleteBookmark},
        {"list_vfos", &execListVfos},
        {"add_vfo", &execAddVfo},
        {"switch_vfo", &execSwitchVfo},
        {"rename_vfo", &execRenameVfo},
        {"set_vfo_armed", &execSetVfoArmed},
        {"set_vfo_frequency", &execSetVfoFrequency},
        {"set_vfo_mode", &execSetVfoMode},
        {"set_vfo_bandwidth", &execSetVfoBandwidth},
        {"list_recordings", &execListRecordings},
        {"delete_recording", &execDeleteRecording},
        {"export_recording", &execExportRecording},
        {"set_fft_params", &execSetFftParams},
        {"set_color_map", &execSetColorMap},
        {"get_spectrum_status", &execGetSpectrumStatus},
        {"set_doppler_compensation", &execSetDopplerCompensation},
        {"connect_network_source", &execConnectNetworkSource},
        // Read-only source-capability + recording-state snapshots (honest empty).
        {"get_capabilities", &execGetCapabilities},
        {"get_recording_state", &execGetRecordingState},
    };
    return kTable;
}
} // namespace

bool isWriteTool(const QString& name) {
    // The write/read gate is read straight from the declarative spec table
    // (ToolSchemaSpec::write) -- no parallel hardcoded write set to drift.
    for (const ToolSchemaSpec& s : registeredToolSpecs())
        if (s.name == name) return s.write;
    return false;   // unknown / read-only is never gated
}

QStringList executorToolNames() {
    QStringList out;
    out.reserve(dispatchTable().size());
    for (const ToolDispatch& d : dispatchTable())
        out.append(QString::fromUtf8(d.name));
    return out;
}

QList<ToolDef> toolDefs() {
    // Single source of truth for the SDR tools: the declarative registry
    // (M1) renders the JSON Schema WITH numeric bounds (min/max from tokens.h)
    // and enum members. Name/description pass through verbatim, so UI copy and
    // existing golden expectations never drift. Dispatch is now a name lookup
    // into dispatchTable() (see executeTool); arguments validation happens
    // upstream in llm_worker before dispatch.
    return toolDefsFromSpecs(registeredToolSpecs());
}

QString executeTool(const QString& name, const QJsonObject& args,
                    dsp::SpectrumEngine* engine, ui::BookmarkManager* bookmarks) {
    // Honest error envelope aligned with ControlHub::execute() (control_hub.cpp:354):
    // never a plain string, never {ok:true} with fabricated values. The executor
    // bodies below all dereference engine->... without their own null guard, so
    // this line is the single crash-seam for the null-engine state.
    if (!engine) {
        QJsonObject o;
        o["ok"] = false;
        o["error"] = QString::fromUtf8("无引擎连接（Agent 未 attach SpectrumEngine）");
        return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
    }

    const SourceInfo src = readSourceInfo(engine);

    for (const ToolDispatch& d : dispatchTable()) {
        if (name == QLatin1String(d.name))
            return d.exec(args, engine, src, bookmarks);
    }
    return "未知工具: " + name;
}

} // namespace ai
} // namespace mbdsdr
