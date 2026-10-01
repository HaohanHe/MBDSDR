// SPDX-License-Identifier: MIT
#include "task_orchestrator.h"
#include "llm_worker.h"
#include "agent_tools.h"
#include "dsp/spectrum_engine.h"
#include "ui/bookmark_manager.h"
#include "core/tokens.h"

#include <QJsonDocument>
#include <QJsonArray>
#include <QElapsedTimer>
#include <QStringList>

namespace mbdsdr {
namespace ai {

TaskOrchestrator::TaskOrchestrator(dsp::SpectrumEngine* engine)
    : engine_(engine) {}

int TaskOrchestrator::maxSteps() {
    return tokens::kTaskMaxSteps;
}

QJsonValue TaskOrchestrator::resolveRef(const QJsonValue& v,
                                         const QList<StepResult>& prior,
                                         bool& ok, QString& err) {
    ok = true;
    err.clear();
    if (!v.isObject()) return v;  // literal scalar / array
    const QJsonObject o = v.toObject();
    // Only an object carrying BOTH fromStep + path is a reference. Anything
    // else (e.g. a plain nested object arg) is passed through verbatim.
    if (!o.contains("fromStep") || !o.contains("path")) return v;

    const int from = o.value("fromStep").toInt(-1);
    const QString path = o.value("path").toString();
    if (from < 0 || from >= prior.size()) {
        ok = false;
        err = QString("引用步骤 %1 超出范围（已完成 %2 步）").arg(from).arg(prior.size());
        return {};
    }
    const StepResult& pr = prior.at(from);
    if (pr.resultJson.isEmpty()) {
        ok = false;
        err = QString("步骤 %1（%2）无 JSON 结果可引用").arg(from).arg(pr.tool);
        return {};
    }

    QJsonValue cur = pr.resultJson;
    for (const QString& part : path.split('.')) {
        if (part.isEmpty()) continue;
        QString name = part;
        QList<int> idxs;
        int br = part.indexOf('[');
        if (br >= 0) {
            name = part.left(br);
            int p = br;
            while (p < part.size() && part[p] == '[') {
                int close = part.indexOf(']', p);
                if (close < 0) break;
                bool conv = false;
                int ix = part.mid(p + 1, close - p - 1).toInt(&conv);
                if (!conv) {
                    ok = false;
                    err = QString("路径 %1 数组下标非法").arg(path);
                    return {};
                }
                idxs.append(ix);
                p = close + 1;
            }
        }
        if (!name.isEmpty()) {
            if (!cur.isObject()) {
                ok = false;
                err = QString("路径 %1 在「%2」处不是对象").arg(path, name);
                return {};
            }
            cur = cur.toObject().value(name);
        }
        for (int ix : idxs) {
            if (!cur.isArray()) {
                ok = false;
                err = QString("路径 %1 处不是数组").arg(path);
                return {};
            }
            const QJsonArray arr = cur.toArray();
            if (ix < 0 || ix >= arr.size()) {
                ok = false;
                err = QString("路径 %1 数组下标 %2 越界（长度 %3）").arg(path).arg(ix).arg(arr.size());
                return {};
            }
            cur = arr.at(ix);
        }
    }
    if (cur.isUndefined()) {
        ok = false;
        err = QString("路径 %1 未命中任何字段").arg(path);
        return {};
    }
    return cur;
}

namespace {
// Resolve every arg value (refs extracted, literals passed through).
QJsonObject resolveArgs(const QJsonObject& in, const QList<StepResult>& prior,
                         bool& ok, QString& err) {
    QJsonObject out;
    for (auto it = in.begin(); it != in.end(); ++it) {
        out.insert(it.key(), TaskOrchestrator::resolveRef(it.value(), prior, ok, err));
        if (!ok) return {};
    }
    return out;
}
} // namespace

QString TaskOrchestrator::run(const TaskPlan& plan, StepCallback onStep) {
    results_.clear();
    stop_.store(false);
    const int cap = qMin(plan.steps.size(), static_cast<qsizetype>(maxSteps()));
    bool stopped = false;

    for (int i = 0; i < cap; ++i) {
        if (stop_.load()) { stopped = true; break; }
        const TaskStep& st = plan.steps.at(i);

        StepResult r;
        r.tool = st.tool;
        r.description = st.description;
        r.state = StepState::Running;
        QElapsedTimer t;
        t.start();

        bool rok = true;
        QString rerr;
        QJsonObject resolved = resolveArgs(st.args, results_, rok, rerr);
        if (!rok) {
            r.elapsedMs = t.elapsed();
            r.state = StepState::Failed;
            r.error = rerr;
            r.summary = QString::fromUtf8("参数解析失败：%1").arg(rerr);
            results_.append(r);
            if (onStep) onStep(results_.constLast());
            if (plan.abortOnFail) break;
            continue;
        }
        r.argsResolved = resolved;

        QString out;
        if (st.tool == "add_bookmark") {
            // Bookmark persistence is a UI/data concern (not an engine action),
            // so it goes through the injected BookmarkManager rather than the
            // engine execution point. Engine tools below reuse dispatchToolCall.
            if (!bookmarks_) {
                out = "error: no bookmark manager";
            } else {
                ui::Bookmark b;
                b.frequencyHz = resolved.value("freq_hz").toDouble();
                b.name = resolved.value("name").toString();
                b.mode = resolved.value("mode").toString();
                const int idx = bookmarks_->add(b);
                QJsonObject o;
                o["ok"] = (idx >= 0);
                o["index"] = idx;
                o["freq_hz"] = b.frequencyHz;
                out = QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
            }
        } else {
            // THE single engine execution point: manual-mode gate applies here.
            out = LLMWorker::dispatchToolCall(st.tool, resolved, engine_, manualMode_);
        }
        r.resultText = out;
        QJsonDocument doc = QJsonDocument::fromJson(out.toUtf8());
        if (doc.isObject()) r.resultJson = doc.object();
        r.elapsedMs = t.elapsed();

        const bool gated = doc.isObject() && doc.object().value("gated").toBool();
        const bool isErr = out.startsWith("error") || out.startsWith("未知工具");
        if (gated) {
            r.state = StepState::Gated;
            r.gated = true;
            r.error = QString::fromUtf8("手动模式拦截：未执行 %1").arg(st.tool);
        } else if (isErr) {
            r.state = StepState::Failed;
            r.error = out;
        } else {
            r.state = StepState::Succeeded;
        }
        r.summary = r.resultText.left(tokens::kTaskSummaryMaxChars);
        results_.append(r);
        if (onStep) onStep(results_.constLast());

        if (isErr && plan.abortOnFail) break;
    }

    int okCount = 0, failCount = 0, gateCount = 0;
    for (const StepResult& r : results_) {
        if (r.state == StepState::Succeeded) ++okCount;
        else if (r.state == StepState::Failed) ++failCount;
        else if (r.state == StepState::Gated) ++gateCount;
    }
    QStringList lines;
    lines << QString::fromUtf8("任务「%1」：完成 %2/%3 步")
                 .arg(plan.name).arg(okCount).arg(plan.steps.size());
    if (gateCount > 0)
        lines << QString::fromUtf8("%1 步被手动模式拦截，未真正动作").arg(gateCount);
    if (failCount > 0)
        lines << QString::fromUtf8("%1 步失败，已按策略处理").arg(failCount);
    if (stopped)
        lines << QString::fromUtf8("已被用户中断，%1 步后停止").arg(results_.size());
    else if (results_.size() < plan.steps.size())
        lines << QString::fromUtf8("达到最大步数 %1，剩余 %2 步未执行")
                     .arg(maxSteps()).arg(plan.steps.size() - results_.size());
    return lines.join("\n");
}

// ---- Deterministic templates ---------------------------------------------

TaskPlan planSweepFindAndRecord(double lowHz, double highHz, double stepHz,
                                 const QString& mode, const QString& bookName) {
    TaskPlan p;
    p.name = QString::fromUtf8("扫频找信号并存档");
    p.abortOnFail = true;

    TaskStep scan;
    scan.tool = "scan_band";
    scan.description = QString::fromUtf8("扫描频段，定位峰值信号");
    scan.args = QJsonObject{{"low_hz", lowHz}, {"high_hz", highHz}, {"step_hz", stepHz}};

    TaskStep bm;
    bm.tool = "add_bookmark";
    bm.description = QString::fromUtf8("把命中频率存为书签");
    bm.args = QJsonObject{
        {"freq_hz", QJsonObject{{"fromStep", 0}, {"path", "hits[0].frequencyHz"}}},
        {"name", bookName},
        {"mode", mode}};

    TaskStep tune;
    tune.tool = "tune_frequency";
    tune.description = QString::fromUtf8("接收机转到命中频率");
    tune.args = QJsonObject{
        {"freq_hz", QJsonObject{{"fromStep", 0}, {"path", "hits[0].frequencyHz"}}}};

    TaskStep md;
    md.tool = "set_mode";
    md.description = QString::fromUtf8("设置解调模式");
    md.args = QJsonObject{{"mode", mode}};

    TaskStep rec;
    rec.tool = "start_recording";
    rec.description = QString::fromUtf8("开始录制");
    rec.args = QJsonObject{};

    TaskStep stp;
    stp.tool = "stop_recording";
    stp.description = QString::fromUtf8("停止录制");
    stp.args = QJsonObject{};

    p.steps = {scan, bm, tune, md, rec, stp};
    return p;
}

TaskPlan planTargetCapture(double targetHz, const QString& mode) {
    TaskPlan p;
    p.name = QString::fromUtf8("目标频率捕获录制");
    p.abortOnFail = true;

    TaskStep tune;
    tune.tool = "tune_frequency";
    tune.description = QString::fromUtf8("调到目标频率");
    tune.args = QJsonObject{{"freq_hz", targetHz}};

    TaskStep md;
    md.tool = "set_mode";
    md.description = QString::fromUtf8("设置解调模式");
    md.args = QJsonObject{{"mode", mode}};

    TaskStep rec;
    rec.tool = "start_recording";
    rec.description = QString::fromUtf8("开始录制");
    rec.args = QJsonObject{};

    TaskStep stp;
    stp.tool = "stop_recording";
    stp.description = QString::fromUtf8("停止录制");
    stp.args = QJsonObject{};

    p.steps = {tune, md, rec, stp};
    return p;
}

TaskPlan planFixedFrequencyRecord(double freqHz, const QString& mode,
                                  double bandwidthHz) {
    TaskPlan p;
    p.name = QString::fromUtf8("固定频率录制解码");
    p.abortOnFail = true;

    TaskStep tune;
    tune.tool = "tune_frequency";
    tune.description = QString::fromUtf8("调到指定频率");
    tune.args = QJsonObject{{"freq_hz", freqHz}};

    TaskStep bw;
    bw.tool = "set_bandwidth";
    bw.description = QString::fromUtf8("设置信道带宽");
    bw.args = QJsonObject{{"bandwidth_hz", bandwidthHz}};

    TaskStep md;
    md.tool = "set_mode";
    md.description = QString::fromUtf8("设置解调模式");
    md.args = QJsonObject{{"mode", mode}};

    TaskStep rec;
    rec.tool = "start_recording";
    rec.description = QString::fromUtf8("开始录制");
    rec.args = QJsonObject{};

    TaskStep stp;
    stp.tool = "stop_recording";
    stp.description = QString::fromUtf8("停止录制");
    stp.args = QJsonObject{};

    p.steps = {tune, bw, md, rec, stp};
    return p;
}

} // namespace ai
} // namespace mbdsdr
