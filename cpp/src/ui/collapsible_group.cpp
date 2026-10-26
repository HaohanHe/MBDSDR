// SPDX-License-Identifier: MIT
#include "collapsible_group.h"

#include <QMouseEvent>
#include <QPainter>
#include <QStyleOptionGroupBox>

namespace mbdsdr {
namespace ui {

namespace {
// Chevron glyphs prepended to the visible title. The bare title (identity)
// is preserved in bareTitle_ and never sent to settings keys or maps.
const char* kChevronOpen   = "▼ ";   // expanded: content visible
const char* kChevronClosed = "▶ ";   // folded: title bar only
} // namespace

CollapsibleGroup::CollapsibleGroup(const QString& title, QWidget* parent)
    : QGroupBox(title, parent), bareTitle_(title) {
    setObjectName(QStringLiteral("collapsibleGroup"));
    // Visible title carries the chevron from the very first frame so an
    // always-expanded group still advertises its fold affordance.
    setTitle(QString::fromUtf8(kChevronOpen) + bareTitle_);
    // The header is a click target; show it without making the group checkable
    // (checked would mean "enabled", a different semantics entirely).
    setCursor(Qt::PointingHandCursor);
}

void CollapsibleGroup::setCollapsed(bool on) {
    if (collapsed_ == on) return;
    collapsed_ = on;

    // Pure presentation flip: children stay constructed and wired, only their
    // visibility changes. Qt layouts exclude hidden widgets from geometry, so
    // the group shrinks to its title bar on its own.
    const QObjectList kids = children();
    for (QObject* kid : kids) {
        if (auto* w = qobject_cast<QWidget*>(kid))
            w->setVisible(!on);
    }

    // Visible title carries the chevron; bareTitle_ (identity) is untouched.
    setTitle(QString::fromUtf8(on ? kChevronClosed : kChevronOpen) + bareTitle_);
    updateGeometry();
    update();
    emit collapsedChanged(on);
}

void CollapsibleGroup::mousePressEvent(QMouseEvent* e) {
    // Title-strip click (any button-free area above the content) toggles the
    // fold. The label rect is resolved through the style so the hit band
    // follows the theme's title metrics instead of a hard-coded pixel guess.
    if (e->button() == Qt::LeftButton) {
        QStyleOptionGroupBox opt;
        initStyleOption(&opt);
        const QRect label =
            style()->subControlRect(QStyle::CC_GroupBox, &opt, QStyle::SC_GroupBoxLabel);
        const QRect frame =
            style()->subControlRect(QStyle::CC_GroupBox, &opt, QStyle::SC_GroupBoxFrame);
        // Header band: from the top of the frame through the title label.
        // A click here folds/expands; clicks below reach the content widgets
        // and never land in this handler (children consume them first).
        const int headerBottom = qMax(label.bottom(), frame.top() + 4) + 4;
        if (e->pos().y() <= headerBottom) {
            toggle();
            e->accept();
            return;
        }
    }
    QGroupBox::mousePressEvent(e);
}

void CollapsibleGroup::paintEvent(QPaintEvent* e) {
    QGroupBox::paintEvent(e);
    // A quiet hover underline on the header signals clickability without
    // stealing the theme's flat instrument look (pressed feedback is the
    // fold/expand itself).
    if (underMouse()) {
        QStyleOptionGroupBox opt;
        initStyleOption(&opt);
        const QRect label =
            style()->subControlRect(QStyle::CC_GroupBox, &opt, QStyle::SC_GroupBoxLabel);
        if (label.isValid()) {
            QPainter p(this);
            p.setPen(QPen(palette().color(QPalette::Mid), 1));
            p.drawLine(label.left(), label.bottom() + 2,
                       label.left() + label.width() - 2, label.bottom() + 2);
        }
    }
}

} // namespace ui
} // namespace mbdsdr
