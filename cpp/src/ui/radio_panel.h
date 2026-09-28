// SPDX-License-Identifier: GPL-3.0-or-later
// Radio / transmit panel: serial CAT control (CI-V, Kenwood), CW keying over the
// RTS line, AX.25/KISS beacons, and SoapySDR TX (PlutoSDR/HackRF/...). All
// transmission requires an explicit user action (press-and-hold PTT or Send).
#pragma once

#include <QWidget>
#include <QComboBox>
#include <QDoubleSpinBox>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QSpinBox>

#include <atomic>
#include <memory>
#include <thread>

namespace mbdsdr {
namespace radio {
class IRadioLink;
class CatClient;
class CwKeyer;
struct CwEvent;
} // namespace radio
namespace tx {
class SoapyTxBackend;
} // namespace tx

namespace ui {

class RadioPanel : public QWidget {
    Q_OBJECT
public:
    explicit RadioPanel(QWidget* parent = nullptr);
    ~RadioPanel() override;

private:
    void setStatus(const QString& s);
    bool ensureCat();
    void startSoapyTone();
    void stopSoapyTone();
    void joinCw();

    // --- serial / CAT widgets ---
    QLineEdit* deviceEdit_ = nullptr;
    QComboBox* baudCombo_ = nullptr;
    QComboBox* protoCombo_ = nullptr;
    QPushButton* connectBtn_ = nullptr;
    QPushButton* readBtn_ = nullptr;
    QPushButton* catPttBtn_ = nullptr;
    QLabel* catStatus_ = nullptr;

    // --- CW widgets ---
    QSpinBox* cwWpm_ = nullptr;
    QLineEdit* cwText_ = nullptr;
    QPushButton* cwSendBtn_ = nullptr;

    // --- AX.25 / KISS widgets ---
    QLineEdit* callEdit_ = nullptr;
    QLineEdit* pathEdit_ = nullptr;
    QLineEdit* msgEdit_ = nullptr;
    QPushButton* beaconBtn_ = nullptr;

    // --- SoapySDR TX widgets ---
    QLineEdit* driverEdit_ = nullptr;
    QDoubleSpinBox* soapyFreq_ = nullptr;
    QPushButton* soapyOpenBtn_ = nullptr;
    QPushButton* soapyPttBtn_ = nullptr;

    QLabel* status_ = nullptr;

    // --- runtime state ---
    std::unique_ptr<radio::IRadioLink> link_;
    std::unique_ptr<radio::CatClient> cat_;
    std::unique_ptr<radio::CwKeyer> keyer_;
    std::thread cwThread_;

    std::unique_ptr<tx::SoapyTxBackend> soapy_;
    std::thread toneThread_;
    std::atomic_bool toneRun_{false};
};

} // namespace ui
} // namespace mbdsdr
