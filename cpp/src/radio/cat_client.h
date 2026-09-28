// SPDX-License-Identifier: GPL-3.0-or-later
// CAT control client: Icom CI-V and Kenwood (TS-2000-style) over an IRadioLink.
// Supports frequency / mode / PTT. Command formats follow the vendors' public
// interface manuals (cited in the .cpp). No hardware here.
#pragma once

#include "radio_link.h"

#include <QString>

namespace mbdsdr {
namespace radio {

class CatClient {
public:
    enum class Protocol { CIV, Kenwood };

    CatClient(IRadioLink& link, Protocol protocol);

    // CI-V addresses (default: transceiver 0xE0, controller 0x00).
    void setCivAddresses(unsigned char transceiver, unsigned char controller);

    bool open();
    bool isOpen() const;

    bool setFrequencyHz(double hz);
    bool getFrequencyHz(double& out);

    bool setMode(const QString& mode);   // LSB/USB/AM/CW/FM/NFM/WFM
    bool getMode(QString& out);

    bool setPtt(bool on);

    QString lastError() const { return err_; }

private:
    QByteArray transact(const QByteArray& request, char terminator);

    IRadioLink& link_;
    Protocol proto_;
    unsigned char civTo_ = 0xE0;
    unsigned char civFrom_ = 0x00;
    QString err_;
};

} // namespace radio
} // namespace mbdsdr
