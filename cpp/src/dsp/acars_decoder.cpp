// SPDX-License-Identifier: MIT
#include "acars_decoder.h"

#include <utility>

namespace mbdsdr {
namespace dsp {

// ---------------------------------------------------------------------------
// CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no final xor.
// Known-answer: crc16("123456789") == 0x29B1.
// ---------------------------------------------------------------------------
uint16_t AcarsDecoder::crc16(const uint8_t* data, int len) {
    uint16_t crc = kAcarsCrcInit;
    for (int i = 0; i < len; ++i) {
        crc ^= static_cast<uint16_t>(data[i]) << 8;
        for (int b = 0; b < 8; ++b) {
            if (crc & 0x8000u)
                crc = static_cast<uint16_t>((crc << 1) ^ kAcarsCrcPoly);
            else
                crc = static_cast<uint16_t>(crc << 1);
        }
    }
    return crc;
}

AcarsDecoder::AcarsDecoder() { rebuildDemod(); }
AcarsDecoder::AcarsDecoder(double sampleRateHz) : sampleRateHz_(sampleRateHz) {
    rebuildDemod();
}

void AcarsDecoder::rebuildDemod() {
    FskDemodConfig cfg;
    cfg.sampleRateHz = sampleRateHz_;
    cfg.symbolRateBd = kAcarsSymbolRateBd;   // 2400 Bd
    cfg.deviationHz  = kAcarsDeviationHz;   // +/-600 Hz  -> h=0.5 (MSK)
    demod_ = FskDemod(cfg);
}

void AcarsDecoder::setSampleRate(double hz) {
    sampleRateHz_ = hz;
    rebuildDemod();
}

void AcarsDecoder::reset() {
    demod_.reset();
    bits_.clear();
    pos_ = 0;
    phase_ = Phase::Hunt;
    frame_.clear();
    subPhase_ = 0;
    curByte_ = 0;
    bitsInCurByte_ = 0;
    crcHi_ = crcLo_ = 0;
    out_.clear();
}

// Assemble one LSB-first byte from bits_[bitIdx .. bitIdx+7].
uint8_t AcarsDecoder::byteAt(std::size_t bitIdx) const {
    uint8_t b = 0;
    for (int i = 0; i < 8; ++i)
        b |= static_cast<uint8_t>((bits_[bitIdx + i] & 1) << i);
    return b;
}

void AcarsDecoder::startCollect(uint8_t botByte) {
    phase_ = Phase::Collect;
    frame_.clear();
    frame_.push_back(botByte);
    subPhase_ = 0;
    curByte_ = 0;
    bitsInCurByte_ = 0;
    crcHi_ = crcLo_ = 0;
}

void AcarsDecoder::abortFrame() {
    phase_ = Phase::Hunt;
    frame_.clear();
    curByte_ = 0;
    bitsInCurByte_ = 0;
}

void AcarsDecoder::emitPacket() {
    AcarsPacket p;
    p.direction = (frame_[0] == kAcarsBotAir)     ? AcarsPacket::Direction::Air
                : (frame_[0] == kAcarsBotGround)  ? AcarsPacket::Direction::Ground
                                                  : AcarsPacket::Direction::Unknown;

    // Body layout after BOT (see header): mode(2) label(2) blockId(1) ack(1) text...
    // frame_ = [BOT, mode0, mode1, label0, label1, blockId, ack, text..., ETX]
    auto ch = [&](int i) -> char {
        return (i >= 0 && i < (int)frame_.size() - 1) ? static_cast<char>(frame_[i]) : ' ';
    };
    p.mode  = std::string(1, ch(1)) + std::string(1, ch(2));
    p.label = std::string(1, ch(3)) + std::string(1, ch(4));
    p.blockId = (frame_.size() > 5) ? std::string(1, static_cast<char>(frame_[5]))
                                    : std::string(1, kAcarsNoAckChar);
    p.ack = (frame_.size() > 6) ? std::string(1, static_cast<char>(frame_[6]))
                                : std::string(1, kAcarsNoAckChar);

    const int textStart = 7;
    const int textEnd = (int)frame_.size() - 1;   // exclude trailing ETX
    if (textEnd > textStart)
        p.text.assign(reinterpret_cast<const char*>(&frame_[textStart]),
                      textEnd - textStart);

    const uint16_t calc = crc16(frame_.data(), (int)frame_.size());
    const uint16_t recv = (static_cast<uint16_t>(crcHi_) << 8) | crcLo_;
    p.crcOk = (calc == recv);

    out_.push_back(std::move(p));
    abortFrame();   // back to Hunt for the next frame
}

void AcarsDecoder::processCollectedByte(uint8_t b) {
    switch (subPhase_) {
        case 0:  // message body up to and including ETX
            frame_.push_back(b);
            if (b == kAcarsEtxChar) {
                subPhase_ = 1;
            } else if ((int)frame_.size() > kAcarsMaxFrameBytes) {
                abortFrame();   // runaway garbage, give up on this frame
            }
            break;
        case 1: crcHi_ = b; subPhase_ = 2; break;
        case 2: crcLo_ = b; subPhase_ = 3; break;
        case 3:
            if (b == kAcarsEotChar) emitPacket();
            else                    abortFrame();   // malformed tail
            break;
    }
}

void AcarsDecoder::feed(const std::vector<std::complex<float>>& baseband) {
    if (baseband.empty()) return;
    demod_.process(baseband);
    std::vector<int> nb = demod_.takeBits();
    bits_.insert(bits_.end(), nb.begin(), nb.end());

    // Stream the recovered bits through the byte/frame state machine.
    bool progress = true;
    while (progress) {
        progress = false;

        if (phase_ == Phase::Hunt) {
            // Look for >=3 x 0x7F then SYN(0x01) then BOT, allowing any byte
            // alignment (we do not assume a fixed bit boundary at lock time).
            for (std::size_t q = pos_ + 24; q + 16 <= bits_.size(); ++q) {
                if (byteAt(q) != kAcarsSynChar) continue;
                if (byteAt(q - 24) != kAcarsPreambleChar) continue;
                if (byteAt(q - 16) != kAcarsPreambleChar) continue;
                if (byteAt(q -  8) != kAcarsPreambleChar) continue;
                const uint8_t bot = byteAt(q + 8);
                if (bot != kAcarsBotAir && bot != kAcarsBotGround) continue;

                pos_ = q + 16;          // step past the BOT byte we just read
                startCollect(bot);
                progress = true;
                break;
            }
        } else {
            // Collect: assemble LSB-first bytes from the known boundary.
            while (pos_ < bits_.size()) {
                curByte_ |= static_cast<uint8_t>((bits_[pos_] & 1) << bitsInCurByte_);
                ++bitsInCurByte_;
                ++pos_;
                if (bitsInCurByte_ == 8) {
                    const uint8_t b = curByte_;
                    curByte_ = 0;
                    bitsInCurByte_ = 0;
                    processCollectedByte(b);
                    progress = true;
                    if (phase_ == Phase::Hunt) break;   // frame done/aborted
                }
            }
        }
    }

    // Bound memory: keep only a short tail of unconsumed bits while hunting,
    // enough to still see the preamble preceding the next SYN.
    if (phase_ == Phase::Hunt && pos_ > (std::size_t)kAcarsKeepTailBits) {
        const std::size_t drop = pos_ - kAcarsKeepTailBits;
        bits_.erase(bits_.begin(), bits_.begin() + drop);
        pos_ -= drop;
    }
}

std::vector<AcarsPacket> AcarsDecoder::takeNewPackets() {
    std::vector<AcarsPacket> out;
    out.swap(out_);
    return out;
}

} // namespace dsp
} // namespace mbdsdr
