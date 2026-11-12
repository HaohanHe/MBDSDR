# 11 · control_panel 无效频率输入框 —— 草稿，等解冻单第 2 条

**状态：草稿 · 等 A2 拍板才应用；当前不得 `git apply`，本文件也不提供 patch。**
缺一个「危险底色」令牌，属配色决定，不是 A1 的换路径。

## 位置

`desktop/control_panel.py:999` 与 `:1007` —— 同一输入框的两条错误分支，按 1 个单元计
（棘轮里是两行 `control:invalid`）。

## 现状

两处都只写 `background-color: COLORS["danger"]`（`#B85C5C`），前景沿用主题
`text_primary`：

- default：主题正文字压在红上 **1.27:1**
- dark：2.88:1

## 为什么不能像其它 10 片那样直接换

`status_danger` 是为了「当字」提亮的，当底不够深（白字对比）：

| 主题 | `status_danger` | 白字 | `bright-text` |
|---|---|---|---|
| default | `#BD4848` | 5.02 | 1.46 |
| dark | `#CE7676` | 3.24 | 1.28 |
| dark_car | `#CF7B7B` | 3.10 | 1.12 |
| high_contrast | `#C45C5C` | 4.17 | 1.79 |
| figma | `#C96A6A` | 3.65 | 2.59 |

四套主题「白」与「bright-text」两者都不够 4.5；按指令不自行调红底、不新造色。

## 阻塞

等解冻单（`docs/pending-patches/2026-11-12-fz04-unfreeze-request.md`）第 2 条
「新增危险底色令牌」批准。批后这一片改成：

1. 底色用新令牌（名称以批复为准，暂称「危险底」），五套主题各自满足：白字 ≥4.5
   ⇒ 底色亮度 L ≤ 0.183（default 现 0.159 已够；dark 0.274、dark_car 0.289、
   high_contrast 0.202、figma 0.238 需压低）；
2. 前景显式写 `#FFFFFF`，不再继承 `text_primary`；
3. 新增反证 `test_invalid_freq_input_foreground_on_danger`：五套主题逐一断言
   前景/底 ≥4.5，掉下来即红；
4. 同一笔删掉两行 `control:invalid` 基线，`_BASELINE_CEILING` 11→10。

## 影响面

同病的还有 `desktop/main_window.py:10240`（录音「停止中」按钮，危险底 + 主题正文字），
在冻结文件里，解冻后一起接新令牌；本片不碰它。
