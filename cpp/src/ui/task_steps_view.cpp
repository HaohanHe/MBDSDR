// SPDX-License-Identifier: MIT
#include "task_steps_view.h"
#include "core/tokens.h"

#include <QFrame>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QJsonDocument>

namespace mbdsdr {
namespace ui {

namespace {
QString stateText(ai::StepState s) {
    switch (s) {
        case ai::StepState::Pending:   return QString::fromUtf8("等待");
        case ai::StepState::Running:   return QString::fromUtf8("执行中");
        case ai::StepState::Succeeded: return QString::fromUtf8("成功");
        case ai::StepState::Failed:    return QString::fromUtf8("失败");
        case ai::StepState::Gated:     return QString::fromUtf8("已手动拦截");
        case ai::StepState::Aborted:   return QString::fromUtf8("已中止");
    }
    return QString();
}

QString stateColor(ai::StepState s) {
    switch (s) {
        case ai::StepState::Succeeded: return QString::fromUtf8(tokens::kSuccess);
        case ai::StepState::Failed:    return QString::fromUtf8(tokens::kDanger);
        case ai::StepState::Gated:     return QString::fromUtf8(tokens::kWarning);
        case ai::StepState::Running:   return QString::fromUtf8(tokens::kAccent);
        default:                       return QString::fromUtf8(tokens::kTextSecondary);
    }
}
} // namespace

TaskStepsView::TaskStepsView(QWidget* parent) : QWidget(parent) {
    root_ = new QVBoxLayout(this);
    root_->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                              tokens::scaled(tokens::kSpacingM),
                              tokens::scaled(tokens::kSpacingM),
                              tokens::scaled(tokens::kSpacingM));
    root_->setSpacing(tokens::scaled(tokens::kSpacingS));

    listHost_ = new QWidget(this);
    listLay_ = new QVBoxLayout(listHost_);
    listLay_->setContentsMargins(0, 0, 0, 0);
    listLay_->setSpacing(tokens::scaled(tokens::kSpacingS));
    root_->addWidget(listHost_);

    reportLabel_ = new QLabel(this);
    reportLabel_->setObjectName("statusHint");
    reportLabel_->setWordWrap(true);
    reportLabel_->setVisible(false);
    root_->addWidget(reportLabel_);
}

void TaskStepsView::clearList() {
    QLayoutItem* it;
    while ((it = listLay_->takeAt(0)) != nullptr) {
        if (it->widget()) it->widget()->deleteLater();
        delete it;
    }
}

void TaskStepsView::clear() {
    clearList();
    reportLabel_->clear();
    reportLabel_->setVisible(false);
}

void TaskStepsView::renderCards(const QList<ai::StepResult>& steps) {
    int idx = 0;
    for (const ai::StepResult& r : steps) {
        ++idx;
        auto* card = new QFrame(listHost_);
        card->setObjectName("panelCard");
        auto* cl = new QVBoxLayout(card);
        cl->setContentsMargins(tokens::scaled(tokens::kSpacingM),
                               tokens::scaled(tokens::kSpacingS),
                               tokens::scaled(tokens::kSpacingM),
                               tokens::scaled(tokens::kSpacingS));
        cl->setSpacing(tokens::scaled(tokens::kSpacingS));

        auto* head = new QHBoxLayout;
        auto* dot = new QLabel(card);
        dot->setFixedSize(tokens::scaled(tokens::kSpacingM), tokens::scaled(tokens::kSpacingM));
        dot->setStyleSheet(QString("background:%1; border-radius:%2px;")
                               .arg(stateColor(r.state))
                               .arg(tokens::scaled(tokens::kSpacingM) / 2));
        auto* title = new QLabel(
            QString::fromUtf8("%1. %2　[%3]　%4 ms")
                .arg(idx).arg(r.tool, stateText(r.state)).arg(r.elapsedMs),
            card);
        title->setObjectName("panelTitle");
        head->addWidget(dot);
        head->addWidget(title, 1);
        cl->addLayout(head);

        if (!r.description.isEmpty()) {
            auto* desc = new QLabel(r.description, card);
            desc->setObjectName("dockHint");
            desc->setWordWrap(true);
            cl->addWidget(desc);
        }
        if (!r.argsResolved.isEmpty()) {
            auto* args = new QLabel(
                QString::fromUtf8("参数: ") +
                QString::fromUtf8(QJsonDocument(r.argsResolved).toJson(QJsonDocument::Compact)),
                card);
            args->setObjectName("monoInfo");
            args->setWordWrap(true);
            cl->addWidget(args);
        }
        auto* sum = new QLabel(r.summary.isEmpty() ? r.error : r.summary, card);
        sum->setObjectName("dockHint");
        sum->setWordWrap(true);
        if (r.gated) sum->setStyleSheet(QString("color:%1;").arg(tokens::kWarning));
        cl->addWidget(sum);

        listLay_->addWidget(card);
    }
    listLay_->addStretch();
}

void TaskStepsView::setRun(const QList<ai::StepResult>& steps, const QString& report) {
    clearList();
    renderCards(steps);
    if (!report.isEmpty()) {
        reportLabel_->setText(report);
        reportLabel_->setVisible(true);
    } else {
        reportLabel_->setVisible(false);
    }
}

void TaskStepsView::setLiveSteps(const QList<ai::StepResult>& steps) {
    clearList();
    renderCards(steps);
}

} // namespace ui
} // namespace mbdsdr
