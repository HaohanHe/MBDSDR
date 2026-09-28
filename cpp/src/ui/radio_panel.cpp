// SPDX-License-Identifier: GPL-3.0-or-later
#include "ui/radio_panel.h"

#include "core/tokens.h"
#include "radio/ax25.h"
#include "radio/cat_client.h"
#include "radio/cw_keyer.h"
#include "radio/kiss.h"
#include "radio/radio_link.h"
#include "tx/soapy_tx_backend.h"

#include <QComboBox>
#include <QDoubleSpinBox>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QSpinBox>
#include <QVBoxLayout>

#include <complex>
#include <vector>

namespace mbdsdr {
namespace ui {

using namespace mbdsdr::radio;
using mbdsdr::tx::SoapyTxBackend;

RadioPanel::RadioPanel(QWidget* parent) : QWidget(parent) {
    const int m = tokens::scaled(tokens::kSpacingL);
    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(m, m, m, m);
    root->setSpacing(tokens::scaled(tokens::kSpacingM));

    // ---- Serial / CAT ----
    auto* catBox = new QGroupBox(QStringLiteral("串口电台 · CAT"), this);
    auto* catForm = new QFormLayout(catBox);
    deviceEdit_ = new QLineEdit(catBox);
    deviceEdit_->setPlaceholderText(QStringLiteral("/dev/ttyUSB0"));
    catForm->addRow(QStringLiteral("串口"), deviceEdit_);

    baudCombo_ = new QComboBox(catBox);
    baudCombo_->addItems({"9600", "19200", "57600", "115200"});
    baudCombo_->setCurrentText("19200");
    protoCombo_ = new QComboBox(catBox);
    protoCombo_->addItems({"CI-V", "Kenwood"});
    auto* bp = new QHBoxLayout;
    bp->addWidget(baudCombo_);
    bp->addWidget(protoCombo_);
    catForm->addRow(QStringLiteral("波特率 / 协议"), bp);

    auto* btnRow = new QHBoxLayout;
    connectBtn_ = new QPushButton(QStringLiteral("连接"), catBox);
    readBtn_ = new QPushButton(QStringLiteral("读取"), catBox);
    catPttBtn_ = new QPushButton(QStringLiteral("按住 PTT"), catBox);
    btnRow->addWidget(connectBtn_);
    btnRow->addWidget(readBtn_);
    btnRow->addWidget(catPttBtn_);
    catForm->addRow(btnRow);
    catStatus_ = new QLabel(QStringLiteral("未连接"), catBox);
    catStatus_->setObjectName("monoInfo");
    catForm->addRow(catStatus_);
    root->addWidget(catBox);

    // ---- CW ----
    auto* cwBox = new QGroupBox(QStringLiteral("CW 电报（RTS 键控）"), this);
    auto* cwForm = new QFormLayout(cwBox);
    cwWpm_ = new QSpinBox(cwBox);
    cwWpm_->setRange(5, 60);
    cwWpm_->setValue(20);
    cwForm->addRow(QStringLiteral("WPM"), cwWpm_);
    cwText_ = new QLineEdit(cwBox);
    cwText_->setPlaceholderText(QStringLiteral("CQ CQ DE ..."));
    cwForm->addRow(QStringLiteral("文本"), cwText_);
    cwSendBtn_ = new QPushButton(QStringLiteral("发送 CW"), cwBox);
    cwForm->addRow(cwSendBtn_);
    root->addWidget(cwBox);

    // ---- AX.25 / KISS ----
    auto* axBox = new QGroupBox(QStringLiteral("AX.25 / KISS 信标"), this);
    auto* axForm = new QFormLayout(axBox);
    callEdit_ = new QLineEdit(axBox);
    callEdit_->setPlaceholderText(QStringLiteral("N0CALL"));
    axForm->addRow(QStringLiteral("本站呼号"), callEdit_);
    pathEdit_ = new QLineEdit(axBox);
    pathEdit_->setPlaceholderText(QStringLiteral("WIDE1-1,WIDE2-2"));
    axForm->addRow(QStringLiteral("路径"), pathEdit_);
    msgEdit_ = new QLineEdit(axBox);
    msgEdit_->setPlaceholderText(QStringLiteral("Hello"));
    axForm->addRow(QStringLiteral("消息"), msgEdit_);
    beaconBtn_ = new QPushButton(QStringLiteral("发送信标"), axBox);
    axForm->addRow(beaconBtn_);
    root->addWidget(axBox);

    // ---- SoapySDR TX ----
    auto* spBox = new QGroupBox(QStringLiteral("SoapySDR 发射（Pluto/HackRF）"), this);
    auto* spForm = new QFormLayout(spBox);
    driverEdit_ = new QLineEdit(spBox);
    driverEdit_->setPlaceholderText(QStringLiteral("driver=plutosdr"));
    spForm->addRow(QStringLiteral("驱动参数"), driverEdit_);
    soapyFreq_ = new QDoubleSpinBox(spBox);
    soapyFreq_->setRange(0.0, 100000.0);
    soapyFreq_->setDecimals(3);
    soapyFreq_->setSuffix(" MHz");
    spForm->addRow(QStringLiteral("频率"), soapyFreq_);
    auto* spRow = new QHBoxLayout;
    soapyOpenBtn_ = new QPushButton(QStringLiteral("打开设备"), spBox);
    soapyPttBtn_ = new QPushButton(QStringLiteral("按住发射载波"), spBox);
    spRow->addWidget(soapyOpenBtn_);
    spRow->addWidget(soapyPttBtn_);
    spForm->addRow(spRow);
    root->addWidget(spBox);

    status_ = new QLabel(QStringLiteral("就绪"), this);
    status_->setObjectName("statusHint");
    root->addWidget(status_);
    root->addStretch();

    // ---- wiring ----
    connect(connectBtn_, &QPushButton::clicked, this, [this] {
        const QString dev = deviceEdit_->text().trimmed();
        if (dev.isEmpty()) { setStatus(QStringLiteral("请填写串口设备")); return; }
        link_ = createSerialRadioLink(dev, baudCombo_->currentText().toInt());
        if (!link_) { setStatus(QStringLiteral("当前平台不支持串口后端")); return; }
        const auto proto = protoCombo_->currentIndex() == 0
                               ? CatClient::Protocol::CIV
                               : CatClient::Protocol::Kenwood;
        cat_ = std::make_unique<CatClient>(*link_, proto);
        if (!cat_->open()) {
            setStatus(QStringLiteral("连接失败: ") + cat_->lastError());
            cat_.reset();
            link_.reset();
            return;
        }
        keyer_ = std::make_unique<CwKeyer>(20);
        catStatus_->setText(QStringLiteral("已连接"));
        setStatus(QStringLiteral("已连接电台"));
    });

    connect(readBtn_, &QPushButton::clicked, this, [this] {
        if (!ensureCat()) return;
        double f = 0;
        QString mode;
        const bool fok = cat_->getFrequencyHz(f);
        const bool mok = cat_->getMode(mode);
        catStatus_->setText(QStringLiteral("频率 %1 Hz · 模式 %2")
                                .arg(fok ? QString::number(f, 'f', 0) : QStringLiteral("--"),
                                     mok ? mode : QStringLiteral("--")));
        setStatus(fok ? QStringLiteral("读取成功") : QStringLiteral("读取失败"));
    });

    connect(catPttBtn_, &QPushButton::pressed, this, [this] {
        if (ensureCat() && !cat_->setPtt(true)) setStatus(cat_->lastError());
    });
    connect(catPttBtn_, &QPushButton::released, this, [this] {
        if (cat_) cat_->setPtt(false);
    });

    connect(cwSendBtn_, &QPushButton::clicked, this, [this] {
        if (!link_ || !keyer_) { setStatus(QStringLiteral("请先连接电台")); return; }
        const QString text = cwText_->text().trimmed();
        if (text.isEmpty()) { setStatus(QStringLiteral("请填写 CW 文本")); return; }
        keyer_->setWpm(cwWpm_->value());
        const auto sched = keyer_->buildSchedule(text);
        joinCw();
        IRadioLink* l = link_.get();
        CwKeyer* k = keyer_.get();
        cwThread_ = std::thread([l, k, sched] {
            LinkCwKey key(*l);
            k->play(key, sched);
        });
        setStatus(QStringLiteral("CW 发送中…"));
    });

    connect(beaconBtn_, &QPushButton::clicked, this, [this] {
        if (!link_) { setStatus(QStringLiteral("请先连接串口 TNC")); return; }
        Ax25Frame fr;
        if (!parseAx25Addr(callEdit_->text().trimmed(), fr.src)) {
            setStatus(QStringLiteral("本站呼号无效")); return;
        }
        if (!parseAx25Addr("APZMBD", fr.dst)) {
            setStatus(QStringLiteral("目的地址无效")); return;
        }
        for (const QString& part :
             pathEdit_->text().split(',', Qt::SkipEmptyParts)) {
            Ax25Addr a;
            if (parseAx25Addr(part.trimmed(), a)) fr.path.push_back(a);
        }
        fr.info = msgEdit_->text().toUtf8();
        const QByteArray raw = encodeAx25(fr);
        if (raw.isEmpty()) { setStatus(QStringLiteral("AX.25 编码失败")); return; }
        const QByteArray wrapped = kissWrap(raw);
        const int n = link_->write(wrapped);
        setStatus(QStringLiteral("已发送 KISS 信标: %1 字节").arg(n));
    });

    connect(soapyOpenBtn_, &QPushButton::clicked, this, [this] {
        const QString drv = driverEdit_->text().trimmed();
        if (drv.isEmpty()) { setStatus(QStringLiteral("请填写 Soapy 驱动参数")); return; }
        stopSoapyTone();
        soapy_ = std::make_unique<SoapyTxBackend>(drv);
        soapy_->setSampleRate(2000000.0);
        soapy_->setFrequencyHz(soapyFreq_->value() * 1e6);
        if (!soapy_->open()) {
            setStatus(QStringLiteral("Soapy 打开失败: ") + soapy_->errorString());
            soapy_.reset();
            return;
        }
        setStatus(QStringLiteral("Soapy 设备已就绪"));
    });

    connect(soapyPttBtn_, &QPushButton::pressed, this, [this] {
        if (!soapy_) { setStatus(QStringLiteral("请先打开 Soapy 设备")); return; }
        if (soapyFreq_->value() <= 0.0) { setStatus(QStringLiteral("请设置发射频率")); return; }
        soapy_->setFrequencyHz(soapyFreq_->value() * 1e6);
        if (!soapy_->setPtt(true)) { setStatus(soapy_->errorString()); return; }
        startSoapyTone();
    });
    connect(soapyPttBtn_, &QPushButton::released, this, [this] {
        stopSoapyTone();
        if (soapy_) soapy_->setPtt(false);
    });
}

RadioPanel::~RadioPanel() {
    stopSoapyTone();
    joinCw();
    if (soapy_) soapy_->setPtt(false);
    if (cat_) cat_->setPtt(false);
}

void RadioPanel::setStatus(const QString& s) {
    status_->setText(s);
}

bool RadioPanel::ensureCat() {
    if (cat_) return true;
    setStatus(QStringLiteral("请先连接电台"));
    return false;
}

void RadioPanel::joinCw() {
    if (cwThread_.joinable()) cwThread_.join();
}

void RadioPanel::startSoapyTone() {
    stopSoapyTone();
    toneRun_ = true;
    toneThread_ = std::thread([this] {
        const int n = 4096;
        const std::vector<std::complex<float>> carrier(
            n, std::complex<float>(0.7f, 0.0f));  // center carrier
        while (toneRun_) soapy_->writeComplex(carrier.data(), n);
    });
}

void RadioPanel::stopSoapyTone() {
    toneRun_ = false;
    if (toneThread_.joinable()) toneThread_.join();
}

} // namespace ui
} // namespace mbdsdr
