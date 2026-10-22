// SPDX-License-Identifier: MIT
//
// pending_plan_store.{h,cpp} -- 待执行任务计划槽（pending-plan slot）。
//
// 断环修复：run_template 的回执此前声称"计划已构建并登记"，但全仓没有任何
// 登记表/消费方，executed=false 之后无人执行（工具名过度承诺）。本模块就是
// 那个真实的登记表：
//   * 登记：run_template（Agent 工具层 / ControlHub 镜像）构建 TaskPlan 后
//     submit() 入槽，回执携带真实的 pending_plan_id 与 plan_status=pending；
//   * 消费：TaskRunner::runPendingPlan(id)（用户确认后经 AI 面板「执行待执行
//     计划」按钮 / 手动模式放行后异步执行）take() 取出并标记 running；
//   * 回写：计划执行结束后 finish() 把真实执行状态（succeeded/failed/
//     aborted/gated/partial）与诚实报告写回槽内；get_task_history 的
//     pending_plans 字段是它只读读面，get_task_history 的 runs 是完成史。
//
// 线程模型：工具线程（LLM worker）登记、UI/任务线程消费与回写，内部 QMutex
// 全保护；不发信号、不持定时器。诚实空态：槽里没有就是没有，绝不虚构计划。
#pragma once

#include "task_orchestrator.h"

#include <QJsonObject>
#include <QList>
#include <QMutex>
#include <QString>

namespace mbdsdr {
namespace ai {

// 计划槽状态词表，与 task_runs.state / StepState 对齐，方便跨表对账。
enum class PendingPlanState {
    Pending,    // 已登记、未被消费
    Running,    // 已被 TaskRunner 消费、执行中
    Succeeded,  // 执行结束：全部步骤成功
    Failed,     // 执行结束：有步骤失败
    Aborted,    // 执行结束：被 requestStop 中断
    Gated,      // 执行结束：手动模式拦截（未真正下发）
    Partial,    // 执行结束：部分成功（无失败但也没全部成功）
};
QString pendingPlanStateName(PendingPlanState s);
// name -> state 的诚实反查：词表外的名字归入 Pending（不会静默变成功）。
PendingPlanState pendingPlanStateFromName(const QString& name);

struct PendingPlanEntry {
    int id = 0;                 // 自增登记号（>=1），回执里的 pending_plan_id
    QString templateName;       // run_template 的 template_name；UI 直跑为空
    TaskPlan plan;
    PendingPlanState state = PendingPlanState::Pending;
    QString report;             // 执行结束回写的诚实报告；未执行为空
};

class PendingPlanStore {
public:
    // 全局槽：run_template、TaskRunner、get_task_history 三方共用同一实例。
    static PendingPlanStore& instance();

    // 登记一条待执行计划，返回自增 id（>=1）。登记即 Pending。
    int submit(const TaskPlan& plan, const QString& templateName);

    // 消费入口：取出 id 对应的待执行计划并标记 Running。
    // 只有 Pending 可取；重复消费 / 不存在 -> false（诚实空操作，不编造计划）。
    bool take(int id, PendingPlanEntry* out);

    // 执行状态真实回写（只有 Running 可回写；其他状态 -> false，不覆盖历史）。
    bool finish(int id, PendingPlanState state, const QString& report);

    // 最早一条仍待执行（Pending）计划的 id；没有 -> 0。
    int oldestPendingId() const;

    // 快照（登记序，新→旧）。
    QList<PendingPlanEntry> list() const;
    // {"plans":[{id,template_name,plan_name,step_count,state,report}, ...],
    //  "count": n}；空槽 plans=[]（诚实空态）。
    QJsonObject toJson() const;

    int pendingCount() const;

    // 测试隔离用：清空槽（不影响任何正在执行的计划对象）。
    void clear();

private:
    PendingPlanStore() = default;

    mutable QMutex mutex_;
    QList<PendingPlanEntry> entries_;
    int nextId_ = 1;
};

} // namespace ai
} // namespace mbdsdr
