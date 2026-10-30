// SPDX-License-Identifier: MIT
// D1 reverse-audit block: abstract control surface for the UI-only settings the
// AI tool layer mirrors. Kept in its own dependency-minimal header (QString /
// QStringList only) so the engine holds a pointer to it without pulling in any
// widget header chain. MainWindow implements it and registers itself; the tool
// layer drives the REAL widgets through it (single source of truth). A headless
// / no-UI run leaves the pointer null -> callers report an honest
// "not available" instead of fabricating a widget write.
#pragma once

#include <QString>
#include <QStringList>

namespace mbdsdr {
namespace dsp {

class UiControlSurface {
public:
    virtual ~UiControlSurface() = default;
    // E: 频谱/瀑布显示面（SpectrumWidget）
    virtual void uiSetPersistence(int mode) = 0;              // 0=关 1=低 2=高
    virtual void uiSetWaterfallDepth(int rows) = 0;           // kWaterfallDepthChoices 精确值（128..4096 六档）
    virtual void uiSetWaterfallSpeed(int linesPerFrame) = 0;  // 1/2/4
    virtual void uiSetBuiltinPalette(int index) = 0;          // 0=经典 1=单色 2=Viridis
    virtual void uiSetDbRange(float minDb, float maxDb) = 0;
    virtual void uiSetZoomFactor(double z) = 0;
    virtual void uiSetDbGridStep(int stepDb) = 0;             // 10/20/40 精确值
    virtual void uiSetPeakThresholdDb(int db) = 0;            // 5..40（tokens::kPeakThreshold*）
    virtual void uiSetMaxHold(bool on) = 0;
    virtual void uiSetMinHold(bool on) = 0;
    virtual void uiSetHoldDecayDb(double dbPerFrame) = 0;     // 0.5/1.5/3.0（tokens::kMaxHoldDecay*）
    virtual void uiSetEdgeRailVisible(bool on) = 0;
    // F/G/H/D：源/扫描/GNSS/图层/步进面（MainWindow）
    virtual void uiSetSourceType(int index) = 0;              // 0=本地 1=rtl_tcp 2=测试信号
    virtual bool uiSetSpyServer(bool on, int port) = 0;       // true=终态符合请求（监听/已停）
    virtual bool uiConnectGnss(const QString& device, int baud) = 0;
    virtual void uiDisconnectGnss() = 0;
    virtual bool uiGnssRunning() const = 0;
    virtual void uiSetMapLayerGnss(bool on) = 0;
    virtual void uiSetMapLayerAdsb(bool on) = 0;
    virtual void uiSetMapLayerSat(bool on) = 0;
    virtual void uiSetScanDwellMs(int ms) = 0;
    virtual void uiSetScanThresholdDb(double db) = 0;
    virtual void uiSetScanDirection(int index) = 0;           // 0=up 1=down 2=pingpong
    virtual void uiSetScanHoldMode(int index) = 0;            // 0=信号消失即离 1=固定停留
    virtual void uiSetScanLingerMs(int ms) = 0;
    virtual void uiSetScanHoldMs(int ms) = 0;
    virtual void uiSetScanBookmarksOnly(bool on) = 0;
    virtual void uiSetTuneStepHz(int stepHz) = 0;             // 1/10/100/1000/10000/100000/1000000
    virtual void uiSetLeftGroupVisible(const QString& panelId, bool visible) = 0;
    // J：电台/TX 面（RadioPanel，裸路径不弹 QMessageBox；确认门在工具层）
    virtual bool uiCatConnect(const QString& device, int baud, const QString& protocol) = 0; // "civ"/"kenwood"
    virtual bool uiCatConnected() const = 0;
    virtual bool uiSendCw(int wpm, const QString& text) = 0;
    virtual bool uiSendAx25Beacon(const QString& call, const QString& path, const QString& msg) = 0;
    virtual bool uiOpenSoapy(const QString& driver, double freqMhz) = 0;
    virtual QString uiRadioStatus() const = 0;                // RadioPanel status_ 当前文案（错误回显用）
};

} // namespace dsp
} // namespace mbdsdr
