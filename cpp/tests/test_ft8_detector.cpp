// SPDX-License-Identifier: MIT
// test_ft8_detector.cpp -- FT8 检测层 e2e：合成 Costas 帧 -> 检测 + 纯噪声空态。
#include <QtTest/QtTest>
#include <complex>
#include <cmath>
#include <random>
#include <vector>

#include "dsp/ft8_detector.h"

using namespace mbdsdr;

class TestFt8Detector : public QObject {
    Q_OBJECT
private slots:
    void detectsSyntheticCostasFrame();
    void pureNoiseHonestEmpty();
    void disabledReturnsEmpty();
};

static std::vector<std::complex<float>> synthFrame(double snrDb = 20.0) {
    constexpr double fs = 12000.0;
    constexpr int nsps = 1920;
    constexpr double spacing = 6.25;
    const int costas[7] = {3, 1, 4, 0, 6, 5, 2};
    std::vector<std::complex<float>> buf(15 * (int)fs, 0.0f);
    int at = 3840;   // 2 * NSPS，符号网格对齐
    // S7 D29 S7 D29 S7
    int layout[79];
    for (int i = 0; i < 7; ++i) layout[i] = costas[i];
    for (int i = 7; i < 36; ++i) layout[i] = 0;   // 占位数据
    for (int i = 36; i < 43; ++i) layout[i] = costas[i - 36];
    for (int i = 43; i < 72; ++i) layout[i] = 0;
    for (int i = 72; i < 79; ++i) layout[i] = costas[i - 72];
    for (int s = 0; s < 79; ++s) {
        double f = (layout[s] - 3.5) * spacing;
        for (int k = 0; k < nsps; ++k) {
            int idx = at + s * nsps + k;
            double ph = 2.0 * M_PI * f * idx / fs;
            buf[idx] = std::complex<float>(std::cos(ph), std::sin(ph));
        }
    }
    // AWGN
    std::mt19937 rng(42);
    std::normal_distribution<double> gauss(0.0, std::pow(10.0, -snrDb / 20.0));
    for (auto& x : buf)
        x += std::complex<float>(gauss(rng), gauss(rng));
    return buf;
}

void TestFt8Detector::detectsSyntheticCostasFrame() {
    Ft8Detector det;
    det.setEnabled(true);
    auto iq = synthFrame(20.0);
    Ft8Candidate c = det.processWindow(iq.data(), iq.size());
    QVERIFY2(c.valid, "合成 Costas 帧应被检出");
    QVERIFY(c.syncQuality > 0.5);   // 正确音能量占比
}

void TestFt8Detector::pureNoiseHonestEmpty() {
    Ft8Detector det;
    det.setEnabled(true);
    std::vector<std::complex<float>> iq(15 * 12000);
    std::mt19937 rng(7);
    std::normal_distribution<double> gauss(0.0, 1.0);
    for (auto& x : iq) x = std::complex<float>(gauss(rng), gauss(rng));
    Ft8Candidate c = det.processWindow(iq.data(), iq.size());
    QVERIFY2(!c.valid, "纯噪声应诚实空态（零候选）");
}

void TestFt8Detector::disabledReturnsEmpty() {
    Ft8Detector det;
    det.setEnabled(false);
    auto iq = synthFrame(20.0);
    Ft8Candidate c = det.processWindow(iq.data(), iq.size());
    QVERIFY(!c.valid);
}

QTEST_MAIN(TestFt8Detector)
#include "test_ft8_detector.moc"
