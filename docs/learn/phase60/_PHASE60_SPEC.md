# Phase 60 — ACARS + NAVTEX packet-text modes (cloud rebuild SPEC)

Baseline: origin/main = 33258d9. Clean-room MIT; no GPL copied; no hardcoded
frequencies; honest empty states; tool count 38 -> 40 (two read-only tools).

## Standards (mechanism learned from public specs, clean-room)
- ACARS over VHF airband: ARINC 618 character framing over MSK
  (2400 b/s, deviation ±600 Hz). Block check = CRC-16/CCITT-FALSE.
- NAVTEX: ITU-R M.540 message layout over SITOR-B (CCIR 476 FEC),
  ITA-2 / CCITT-2 alphabet, 100 baud 2-FSK, deviation ±85 Hz.

## Decoder contracts (FROZEN — integration code depends on these exact names)

### acars_decoder.h / .cpp  (namespace mbdsdr::dsp)
```
struct AcarsPacket {
    enum class Direction { Unknown, Air, Ground };
    Direction direction = Direction::Unknown;
    std::string mode;      // 2-char mode word
    std::string label;     // 2-char label
    std::string blockId;   // 1 char
    std::string ack;       // 1 char ('_' when no ack char)
    std::string text;
    bool crcOk = false;    // block check over BOT..ETX inclusive
};
class AcarsDecoder {
public:
    AcarsDecoder();
    explicit AcarsDecoder(double sampleRateHz);
    void setSampleRate(double hz);
    void feed(const std::vector<std::complex<float>>& baseband);
    std::vector<AcarsPacket> takeNewPackets();
    void reset();
    static uint16_t crc16(const uint8_t* data, int len);
};
```
Named constants: kSymbolRateBd=2400, kDeviationHz=600.0,
kPreambleChar=0x7F, kSynChar=0x01, kBotAir=0x8B, kBotGround=0x86,
kEtxChar=0x03, kEotChar=0x04, kMinPreambleBytes=3, kMaxFrameBytes=256,
kKeepTailBits=256. CRC-16/CCITT-FALSE poly 0x1021 init 0xFFFF, range
BOT...ETX inclusive. CRC-fail frames are EMITTED with crcOk=false (honest).

### navtex_decoder.h / .cpp  (namespace mbdsdr::dsp)
```
struct NavtexMessage {
    std::string stationB1;   // 1 char
    std::string typeB2;      // 1 char
    std::string numberB3B4;  // 2 chars
    std::string text;
    bool diversityOk = true;
    bool phasingOk = false;
    int diversityErrors = 0; // honest mismatch count
};
class NavtexDecoder {            // non-copyable (opaque FskDemod inside)
public:
    NavtexDecoder();
    ~NavtexDecoder();
    void setSampleRate(double hz);   // MUST be called before feed()
    void feed(const std::vector<std::complex<float>>& baseband);
    std::vector<NavtexMessage> takeNewMessages();
    void reset();
private:
    void* demod_ = nullptr;          // opaque FskDemod (see cpp)
};
char ita2Letters(int code); char ita2Figures(int code);
```
Named constants: kNavtexSymbolRateBd=100, kNavtexDeviationHz=85.0,
kNavtexBitsPerChar=5, kNavtexCharsPerSec=20, kIta2Null=0, kIta2Space=4,
kIta2Cr=8, kIta2Lf=2, kIta2Figs=27, kIta2Ltrs=31, kIta2Z=17, kIta2C=14,
kIta2N=12, kNavtexPhasingA=30, kNavtexPhasingB=15, kNavtexPhasingMinChars=20.
SITOR-B wire interleave: w = c0 c1 c2 c0' c3 c1' c4 c2' ... (copy lag 4
chars); message channel = odd wire positions, delayed copy = even, compared
for diversity. Frame = phasing XYXY... (>=20) + ZCZC + space + B1B2B3B4 +
message + NNNN.

## Reuse (do NOT reimplement)
- FskDemod (cpp/src/dsp/fsk_demod.h): feed(baseband) -> takeBits().
  Config struct FskDemodConfig{ sampleRateHz, symbolRateBd, deviationHz }.
- The ACARS decoder owns an FskDemod (MSK recovered via 2-FSK at
  symbolRate=2400, deviation=600). The NAVTEX decoder owns one too
  (symbolRate=100, deviation=85).

## Tests (new files, each with a plain main() OR QtTest — mirror
cpp/tests/test_m17_decoder.cpp style for standalone main, or
test_vor_receiver.cpp for QtTest; the CMake registration is done by the
integrator later — the test must compile standalone with g++ first)
- test_acars_decode.cpp: deterministic synthetic IQ (fixed seed),
  clean air frame -> fields exact + crcOk=true; ground frame; tampered byte
  -> crcOk=false; SNR gradient (40 frames/point, 20..0 dB) printing the
  honest 3-column table (emitted/crcOK/crcBAD) and the honest failure SNR;
  pure Gaussian noise (200k samples) -> 0 packets; idle zeros -> 0.
- test_navtex_decode.cpp: same shape; clean message -> station/type/number/
  text exact, phasingOk=1, diversityOk=1, errors=0; AWGN gradient
  (20 trials/point, 12..0 dB); noise/silence -> 0 messages.

## Build / toolchain (cloud)
- g++ 11.4 + Qt 6.8.2 gcc_64. LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib
- Standalone check compile (pattern):
  g++ -std=c++17 -D_GNU_SOURCE -I cpp -I cpp/src \
    tests/test_acars_decode.cpp src/dsp/acars_decoder.cpp \
    src/dsp/fsk_demod.cpp src/dsp/demod.cpp src/dsp/agc.cpp \
    -L /home/user/Qt/6.8.2/gcc_64/lib -lQt6Core -I /home/user/Qt/6.8.2/gcc_64/include ...
  (add Qt include dirs as needed; QtTest tests need -lQt6Test and AUTOMOC
  handled by the integrator's CMake — for standalone verify use the
  .moc pattern used by other QtTest files).
- Deliver real measured numbers in the report (exact tables).

## Red lines
- No hardcoded RF frequencies (ACARS 131.525 MHz / NAVTEX 518/490 kHz go to
  docs ONLY, not code); no callsign/TLE seeds; no fake packets — pure noise
  must yield 0 frames; MIT clean-room (reference public specs, not GPL code);
  no "competition" wording.
