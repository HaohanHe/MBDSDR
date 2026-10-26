// SPDX-License-Identifier: MIT
//
// CollapsibleGroup -- clickable-header QGroupBox (活动布局 A1: "折").
//
// Clicking the title header folds the group down to its title bar and expands
// it again on the next click. The bare title string is NEVER changed (it is
// the stable identity key used by leftPanelGroups_ / settings dialogs); the
// collapse indicator is a chevron drawn into the title gutter via QSS
// subcontrol offset + a custom paintEvent.
//
// Collapse is pure presentation: child widgets stay constructed and wired,
// only visibility flips. No data is discarded and nothing is rebuilt -- the
// same discipline the rest of the layout system follows (see
// workspace_layout.h: model is data, rendering is ephemeral).
#pragma once

#include <QGroupBox>

namespace mbdsdr {
namespace ui {

class CollapsibleGroup : public QGroupBox {
    Q_OBJECT
public:
    explicit CollapsibleGroup(const QString& title, QWidget* parent = nullptr);

    // Title text as registered (identity). The visual title adds a chevron
    // gutter but this string is what settings keys / maps match against.
    QString bareTitle() const { return bareTitle_; }

    bool collapsed() const { return collapsed_; }
    void setCollapsed(bool on);

signals:
    void collapsedChanged(bool collapsed);

protected:
    void mousePressEvent(QMouseEvent* e) override;
    void paintEvent(QPaintEvent* e) override;

private:
    void toggle() { setCollapsed(!collapsed_); }

    QString bareTitle_;
    bool collapsed_ = false;
};

} // namespace ui
} // namespace mbdsdr
