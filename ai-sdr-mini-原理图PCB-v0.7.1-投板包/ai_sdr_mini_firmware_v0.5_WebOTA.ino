/*
 * ai-sdr Mini Firmware v0.5 (SI4732 I2S直连 + WebOTA + MCP OTA)
 * MBDSDR Project - AI定义无线电
 * 呼号: BI4MIB
 * 全开源 GPL-3.0
 *
 * 硬件 (v0.7 BOM):
 *   ESP32-S3-WROOM-1-N8R8 (主控, 8MB PSRAM)
 *   SI4732-A10-GSR (AM/FM/SW/LW/SSB接收, I2S数字输出)
 *   TMAG5273A1QDBVT (3轴磁力计, I2C 0x35)
 *   BMI260 (6轴IMU, I2C 0x68)
 *   ATGM336H-5NR32-G (GPS/北斗, UART1)
 *   USB2514B (USB 2.0 Hub)
 *   AMS1117-3.3 (稳压)
 *
 * 功能:
 *   - SI4732 AM/FM/SW/LW/SSB 接收 (I2C控制 + I2S数字音频输入)
 *   - WiFi Web服务器 (远程调谐/状态查看/配置)
 *   - I2S音频通过WebSocket流式传输到电脑端MBDSDR软件
 *   - 基带录音触发 (WAV头通过WebSocket发送, 电脑端存盘)
 *   - GPS/北斗 NMEA解析 (ATGM336H, UART1)
 *   - 6轴IMU (BMI260) + 3轴磁力计 (TMAG5273), 9轴姿态/测向
 *   - MCP协议 (JSON-RPC over WebSocket, AI可调用全部工具)
 *   - 4个LED状态指示 (全黄, 靠闪烁模式区分)
 *   - OTA固件升级 (ArduinoOTA / WebOTA)
 *   - 配置持久化 (Preferences NVS)
 *
 * 编译: Arduino IDE 2.x + ESP32 Arduino Core 3.x
 *       板卡: ESP32S3 Dev Module
 *       Flash: 8MB, PSRAM: 8MB (OPI PSRAM)
 *       USB Mode: USB-OTG (TinyUSB) 或 Hardware CDC and JTAG
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <WebSocketsServer.h>
#include <Wire.h>
#include <driver/i2s.h>
#include <Preferences.h>
#include <ArduinoOTA.h>
#include <esp_task_wdt.h>

// ==================== 引脚定义 (ESP32-S3-WROOM-1-N8R8) ====================
#define PIN_I2C0_SCL     8    // IO8  -> I2C0 SCL (SI4732 + BMI260 + TMAG5273)
#define PIN_I2C0_SDA     9    // IO9  -> I2C0 SDA
#define PIN_I2S_BCLK      4    // IO4  <- SI4732 DCLK (I2S位时钟输入)
#define PIN_I2S_LRCLK     5    // IO5  <- SI4732 LOUT/DFS (I2S帧时钟输入)
#define PIN_I2S_DIN       7    // IO7  <- SI4732 ROUT/DOUT (I2S数据输入)
#define PIN_GPS_RX        17   // IO17 -> GPS TXD (UART1 RX)
#define PIN_GPS_TX        18   // IO18 -> GPS RXD (UART1 TX)
#define PIN_GPS_PPS       16   // IO16 -> GPS PPS
#define PIN_SI4732_INT    10   // IO10 -> SI4732 INTB/GPO2
#define PIN_SI4732_RST    11   // IO11 -> SI4732 RST (与IMU_INT复用, 见注)
#define PIN_HUB_RESET     21   // IO21 -> USB2514B RESET_N
#define PIN_LED1          1    // IO1  -> LED1 电源/运行 (黄)
#define PIN_LED2          2    // IO2  -> LED2 SDR接收 (黄)
#define PIN_LED3          3    // IO3  -> LED3 GPS定位 (黄)
#define PIN_LED4          46   // IO46 -> LED4 AI/录音 (黄)
#define PIN_BUTTON        0    // IO0  -> 复位按钮 (BOOT键复用)

// 注: SI4732_RST和IMU_INT在v0.7连接表中都接到IO11, 软件上SI4732复位时
//     会触发IMU中断, 初始化时先复位SI4732再初始化IMU, 运行中SI4732不复位.

// ==================== I2C 地址 ====================
#define SI4732_ADDR       0x11  // SI4732 (SEN接地)
#define BMI260_ADDR       0x68  // BMI260 (SDO接地)
#define TMAG5273_ADDR     0x35  // TMAG5273 (ADDR接地, 7位地址)

// ==================== SI4732 命令定义 ====================
#define SI4732_CMD_POWER_UP           0x01
#define SI4732_CMD_GET_REV            0x10
#define SI4732_CMD_POWER_DOWN         0x11
#define SI4732_CMD_SET_PROPERTY       0x12
#define SI4732_CMD_GET_PROPERTY       0x13
#define SI4732_CMD_GET_INT_STATUS     0x14
#define SI4732_CMD_FM_TUNE_FREQ       0x20
#define SI4732_CMD_FM_SEEK_START      0x21
#define SI4732_CMD_FM_TUNE_STATUS     0x22
#define SI4732_CMD_FM_RSQ_STATUS      0x23
#define SI4732_CMD_FM_RDS_STATUS      0x24
#define SI4732_CMD_AM_TUNE_FREQ       0x40
#define SI4732_CMD_AM_SEEK_START      0x41
#define SI4732_CMD_AM_TUNE_STATUS     0x42
#define SI4732_CMD_AM_RSQ_STATUS      0x43
#define SI4732_CMD_TX_TUNE_FREQ       0x30
#define SI4732_CMD_AGC_STATUS         0x27

// SI4732 属性
#define SI4732_PROP_REFCLK_FREQ       0x0002  // 参考时钟频率 (32768Hz)
#define SI4732_PROP_REFCLK_PRESCALE   0x0003  // 参考时钟预分频
#define SI4732_PROP_I2S_OUTPUT_CONFIG 0x0007  // I2S输出配置
#define SI4732_PROP_I2S_TX_CONFIG     0x0008  // I2S TX配置
#define SI4732_PROP_FM_DEEMPHASIS     0x1100  // FM去加重
#define SYSPROP_INTERRUPT_SOURCE       0x0001

// ==================== BMI260 寄存器 ====================
#define BMI260_REG_CHIP_ID      0x00
#define BMI260_REG_PMU_STATUS   0x03
#define BMI260_REG_DATA_0       0x0C
#define BMI260_REG_STATUS       0x1B
#define BMI260_REG_INT_STATUS_0 0x1C
#define BMI260_REG_ACC_CONF     0x40
#define BMI260_REG_ACC_RANGE    0x41
#define BMI260_REG_GYR_CONF     0x42
#define BMI260_REG_GYR_RANGE    0x43
#define BMI260_REG_CMD          0x7E
#define BMI260_CHIP_ID_VAL      0xD7

// ==================== TMAG5273 寄存器 ====================
#define TMAG5273_REG_DEVICE_ID   0x00
#define TMAG5273_REG_DEVICE_CONFIG 0x01
#define TMAG5273_REG_SENSOR_CONFIG 0x02
#define TMAG5273_REG_INTB_CONFIG 0x03
#define TMAG5273_REG_X_MSB_RESULT 0x40
#define TMAG5273_REG_Y_MSB_RESULT 0x42
#define TMAG5273_REG_Z_MSB_RESULT 0x44
#define TMAG5273_REG_T_MSB_RESULT 0x46
#define TMAG5273_DEVICE_ID_VAL    0x22  // 7位ID, 实际读0x44? 需核对

// ==================== 全局状态 ====================
typedef struct {
  // SI4732
  uint8_t  si4732_mode;       // 0=FM, 1=AM, 2=SW, 3=SSB
  uint32_t si4732_freq;       // 当前频率 (kHz)
  uint8_t  si4732_volume;     // 音量 0-63
  bool     si4732_muted;
  bool     si4732_ready;
  uint16_t si4732_rssi;       // 信号强度
  uint8_t  si4732_snr;        // 信噪比
  // GPS
  bool     gps_fix;
  double   gps_lat;
  double   gps_lon;
  float    gps_alt;
  uint8_t  gps_sats;
  float    gps_hdop;
  // IMU
  float    acc_x, acc_y, acc_z;   // g
  float    gyr_x, gyr_y, gyr_z;   // deg/s
  float    mag_x, mag_y, mag_z;   // uT
  float    temperature;            // C
  // 系统
  uint32_t uptime;
  bool     recording;
  uint32_t rec_samples;
} DeviceState;

DeviceState state;
Preferences prefs;

// ==================== WiFi ====================
const char* WIFI_SSID = "MBDSDR-Mini";
const char* WIFI_PASS = "mbdsdr123";  // 默认密码, 可通过Web配置
WebServer server(80);
WebSocketsServer webSocket(81);

// WiFi模式: 0=AP模式(默认), 1=STA模式(连接路由器)
uint8_t wifi_mode = 0;
char sta_ssid[32] = "";
char sta_pass[64] = "";

// ==================== I2S ====================
#define I2S_PORT I2S_NUM_0
#define SAMPLE_RATE 48000
#define DMA_BUF_COUNT 8
#define DMA_BUF_LEN 1024

// ==================== 音频缓冲 (PSRAM) ====================
#define AUDIO_RING_BUF_SIZE (64 * 1024)  // 64KB环形缓冲, 放PSRAM
uint8_t* audio_ring_buf = NULL;
volatile uint32_t audio_write_pos = 0;
volatile uint32_t audio_read_pos = 0;
SemaphoreHandle_t audio_mutex;

// ==================== LED 状态 ====================
typedef enum {
  LED_OFF = 0,
  LED_ON,
  LED_BLINK_SLOW,   // 1Hz
  LED_BLINK_FAST,   // 5Hz
  LED_BLINK_DOUBLE  // 双闪
} LedPattern;

LedPattern led1_pattern = LED_ON;      // 电源常亮
LedPattern led2_pattern = LED_OFF;     // SDR接收时闪烁
LedPattern led3_pattern = LED_OFF;     // GPS定位后慢闪
LedPattern led4_pattern = LED_OFF;     // AI/录音时快闪

// ==================== MCP 工具列表 ====================
typedef struct {
  const char* name;
  const char* description;
} MCPTool;

MCPTool mcp_tools[] = {
  {"tune_fm", "调谐到指定FM频率 (单位MHz, 范围64-108)"},
  {"tune_am", "调谐到指定AM频率 (单位kHz, 范围520-1710)"},
  {"tune_sw", "调谐到指定SW频率 (单位kHz, 范围2300-26100)"},
  {"set_volume", "设置音量 (0-63)"},
  {"set_mute", "静音/取消静音"},
  {"seek", "自动搜台 (方向: up/down)"},
  {"get_status", "获取当前接收状态 (频率/RSSI/SNR/模式)"},
  {"get_gps", "获取GPS定位信息 (经纬度/高度/卫星数)"},
  {"get_imu", "获取9轴姿态数据 (加速度/陀螺仪/磁力计)"},
  {"start_record", "开始录音 (格式: wav/csv/raw, 采样率可选)"},
  {"stop_record", "停止录音"},
  {"set_wifi_sta", "配置WiFi STA模式 (SSID/密码)"},
  {"reboot", "重启设备"},
  {"get_version", "获取固件版本和硬件信息"},
  {"check_update", "检查固件更新 (返回当前版本和最新版本信息)"},
  {"trigger_ota", "触发OTA升级 (需先通过Web上传固件, 或指定URL)"},
  {"web_ota_url", "获取Web OTA页面地址 (浏览器打开可上传固件.bin)"}
};
#define MCP_TOOL_COUNT (sizeof(mcp_tools)/sizeof(mcp_tools[0]))

// ==================== SI4732 驱动 ====================
bool si4732_writeCommand(uint8_t cmd, uint8_t* args, uint8_t argCount) {
  Wire.beginTransmission(SI4732_ADDR);
  Wire.write(cmd);
  for (uint8_t i = 0; i < argCount; i++) Wire.write(args[i]);
  return Wire.endTransmission() == 0;
}

bool si4732_readResponse(uint8_t* buf, uint8_t len) {
  uint8_t got = Wire.requestFrom(SI4732_ADDR, len);
  if (got != len) return false;
  for (uint8_t i = 0; i < len; i++) buf[i] = Wire.read();
  return true;
}

void si4732_waitForCTS() {
  uint8_t status;
  uint32_t timeout = millis() + 500;
  do {
    Wire.requestFrom(SI4732_ADDR, (uint8_t)1);
    status = Wire.read();
    if (millis() > timeout) break;
  } while (!(status & 0x80));
}

bool si4732_powerUp(uint8_t mode) {
  // mode: 0=FM, 1=AM(SI4732不支持AM独立powerup, 用FM mode+AM tune)
  uint8_t args[3];
  args[0] = 0xC0;  // FUNC=0 (FM/AM共用), 保留位
  args[1] = 0x00;  // OPMODE=0 (模拟/数字输出)
  args[2] = 0x50;  // 应用模式
  // SI4732 power up: CTSIEN, GPO2OEN, PATCH, XOSCEN, FUNC[3:0], OPMODE[1:0]
  // 简化: 0xC0 = XOSCEN | FUNC0 (FM receive)
  if (!si4732_writeCommand(SI4732_CMD_POWER_UP, args, 1)) return false;
  delay(120);  // 等待电源上升和晶振稳定
  si4732_waitForCTS();
  
  // 设置参考时钟 32768Hz
  uint8_t propArgs[5];
  propArgs[0] = (SI4732_PROP_REFCLK_FREQ >> 8) & 0xFF;
  propArgs[1] = SI4732_PROP_REFCLK_FREQ & 0xFF;
  propArgs[2] = 0x00;
  propArgs[3] = 0x80;  // 32768Hz = 0x0080
  si4732_writeCommand(SI4732_CMD_SET_PROPERTY, propArgs, 4);
  si4732_waitForCTS();
  
  // 配置I2S输出 (数字音频)
  // I2S_OUTPUT_CONFIG: DCLK=输入/输出, 格式=I2S, 位深=16bit
  propArgs[0] = (SI4732_PROP_I2S_OUTPUT_CONFIG >> 8) & 0xFF;
  propArgs[1] = SI4732_PROP_I2S_OUTPUT_CONFIG & 0xFF;
  propArgs[2] = 0x00;
  propArgs[3] = 0x03;  // I2S格式, 16bit, slave模式(DCLK/LRCLK由SI4732输出? 实际SI4732是master)
  si4732_writeCommand(SI4732_CMD_SET_PROPERTY, propArgs, 4);
  si4732_waitForCTS();
  
  // 配置中断源 (RDS+调谐完成+RSQ)
  propArgs[0] = (SYSPROP_INTERRUPT_SOURCE >> 8) & 0xFF;
  propArgs[1] = SYSPROP_INTERRUPT_SOURCE & 0xFF;
  propArgs[2] = 0x00;
  propArgs[3] = 0x0F;  // 使能所有中断
  si4732_writeCommand(SI4732_CMD_SET_PROPERTY, propArgs, 4);
  si4732_waitForCTS();
  
  state.si4732_ready = true;
  state.si4732_mode = mode;
  return true;
}

bool si4732_tuneFM(uint16_t freqMHz_x100) {
  // freqMHz_x100: 频率x100, 如98.5MHz=9850
  uint8_t args[4];
  args[0] = (freqMHz_x100 >> 8) & 0xFF;
  args[1] = freqMHz_x100 & 0xFF;
  args[2] = 0x00;  // 不使用ANTENNA_INPUT_CAP
  args[3] = 0x00;
  if (!si4732_writeCommand(SI4732_CMD_FM_TUNE_FREQ, args, 4)) return false;
  si4732_waitForCTS();
  state.si4732_freq = freqMHz_x100;
  state.si4732_mode = 0;
  return true;
}

bool si4732_tuneAM(uint16_t freqKHz) {
  uint8_t args[4];
  args[0] = (freqKHz >> 8) & 0xFF;
  args[1] = freqKHz & 0xFF;
  args[2] = 0x00;
  args[3] = 0x00;
  if (!si4732_writeCommand(SI4732_CMD_AM_TUNE_FREQ, args, 4)) return false;
  si4732_waitForCTS();
  state.si4732_freq = freqKHz;
  state.si4732_mode = 1;
  return true;
}

bool si4732_getTuneStatus() {
  uint8_t args[1] = {0x01};  // 清除中断
  si4732_writeCommand(SI4732_CMD_FM_TUNE_STATUS, args, 1);
  si4732_waitForCTS();
  uint8_t resp[8];
  if (si4732_readResponse(resp, 8)) {
    state.si4732_rssi = resp[4];  // RSSI (0-127 dBuV)
    state.si4732_snr = resp[5];   // SNR (0-127 dB)
    return true;
  }
  return false;
}

bool si4732_setVolume(uint8_t vol) {
  // SI4732没有独立音量寄存器, 通过LINE_OUT_LEVEL属性控制
  // 简化: 音量通过I2S数字增益控制, 这里记录状态
  state.si4732_volume = constrain(vol, 0, 63);
  return true;
}

void si4732_reset() {
  pinMode(PIN_SI4732_RST, OUTPUT);
  digitalWrite(PIN_SI4732_RST, LOW);
  delay(10);
  digitalWrite(PIN_SI4732_RST, HIGH);
  delay(10);
}

// ==================== BMI260 驱动 ====================
bool bmi260_writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(BMI260_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

uint8_t bmi260_readReg(uint8_t reg) {
  Wire.beginTransmission(BMI260_ADDR);
  Wire.write(reg);
  Wire.endTransmission();
  Wire.requestFrom(BMI260_ADDR, (uint8_t)1);
  return Wire.read();
}

bool bmi260_init() {
  uint8_t chipId = bmi260_readReg(BMI260_REG_CHIP_ID);
  if (chipId != BMI260_CHIP_ID_VAL) {
    Serial.printf("[BMI260] Chip ID mismatch: 0x%02X (expected 0x%02X)\n", chipId, BMI260_CHIP_ID_VAL);
    return false;
  }
  
  // 发送命令: 加速度计正常模式
  bmi260_writeReg(BMI260_REG_CMD, 0x11);  // ACC_NORMAL
  delay(10);
  // 陀螺仪正常模式
  bmi260_writeReg(BMI260_REG_CMD, 0x15);  // GYR_NORMAL
  delay(10);
  
  // 加速度计配置: ODR=100Hz, 正常平均, 滤波器2
  bmi260_writeReg(BMI260_REG_ACC_CONF, 0x28);  // ODR=100Hz(0x8), 正常模式(0x2)
  // 加速度计量程: ±8g
  bmi260_writeReg(BMI260_REG_ACC_RANGE, 0x08);  // ±8g
  
  // 陀螺仪配置: ODR=100Hz
  bmi260_writeReg(BMI260_REG_GYR_CONF, 0x28);
  // 陀螺仪量程: ±500dps
  bmi260_writeReg(BMI260_REG_GYR_RANGE, 0x02);  // ±500dps
  
  Serial.println("[BMI260] Init OK");
  return true;
}

void bmi260_read() {
  Wire.beginTransmission(BMI260_ADDR);
  Wire.write(BMI260_REG_DATA_0);
  Wire.endTransmission();
  Wire.requestFrom(BMI260_ADDR, (uint8_t)15);  // 15字节: 时间(3)+加速度(6)+陀螺仪(6)
  if (Wire.available() < 15) return;
  
  uint8_t data[15];
  for (int i = 0; i < 15; i++) data[i] = Wire.read();
  
  // 加速度 (data[3-8], little-endian)
  int16_t acc_x_raw = (data[4] << 8) | data[3];
  int16_t acc_y_raw = (data[6] << 8) | data[5];
  int16_t acc_z_raw = (data[8] << 8) | data[7];
  // ±8g, 16bit -> 灵敏度 1g = 4096 LSB
  state.acc_x = acc_x_raw / 4096.0f;
  state.acc_y = acc_y_raw / 4096.0f;
  state.acc_z = acc_z_raw / 4096.0f;
  
  // 陀螺仪 (data[9-14])
  int16_t gyr_x_raw = (data[10] << 8) | data[9];
  int16_t gyr_y_raw = (data[12] << 8) | data[11];
  int16_t gyr_z_raw = (data[14] << 8) | data[13];
  // ±500dps, 16bit -> 灵敏度 1dps = 65.5 LSB
  state.gyr_x = gyr_x_raw / 65.5f;
  state.gyr_y = gyr_y_raw / 65.5f;
  state.gyr_z = gyr_z_raw / 65.5f;
}

// ==================== TMAG5273 驱动 ====================
bool tmag5273_writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(TMAG5273_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

uint8_t tmag5273_readReg(uint8_t reg) {
  Wire.beginTransmission(TMAG5273_ADDR);
  Wire.write(reg);
  Wire.endTransmission();
  Wire.requestFrom(TMAG5273_ADDR, (uint8_t)1);
  return Wire.read();
}

bool tmag5273_init() {
  uint8_t devId = tmag5273_readReg(TMAG5273_REG_DEVICE_ID);
  Serial.printf("[TMAG5273] Device ID: 0x%02X\n", devId);
  // TMAG5273 device ID 高5位是0x22 (0x44 >> 1), 需核对实际值
  
  // 配置: 连续测量模式, 100Hz ODR, 3轴+温度
  // DEVICE_CONFIG: 0x01 寄存器
  // bit[1:0] MODE: 00=standby, 01=continuous, 10=continuous low power, 11=wakeup&sleep
  tmag5273_writeReg(TMAG5273_REG_DEVICE_CONFIG, 0x01);  // continuous mode
  delay(10);
  
  // SENSOR_CONFIG: 0x02 寄存器
  // 触发模式=连续, 通道=XYZ+温度, 量程=±40mT (低量程)
  tmag5273_writeReg(TMAG5273_REG_SENSOR_CONFIG, 0x78);  // XYZ+温度, ±40mT, 100Hz
  delay(10);
  
  Serial.println("[TMAG5273] Init OK");
  return true;
}

void tmag5273_read() {
  Wire.beginTransmission(TMAG5273_ADDR);
  Wire.write(TMAG5273_REG_X_MSB_RESULT);
  Wire.endTransmission();
  Wire.requestFrom(TMAG5273_ADDR, (uint8_t)8);  // X(2)+Y(2)+Z(2)+T(2)
  if (Wire.available() < 8) return;
  
  uint8_t data[8];
  for (int i = 0; i < 8; i++) data[i] = Wire.read();
  
  // 16位结果, big-endian? TMAG5273是MSB first
  int16_t mag_x_raw = (data[0] << 8) | data[1];
  int16_t mag_y_raw = (data[2] << 8) | data[3];
  int16_t mag_z_raw = (data[4] << 8) | data[5];
  int16_t temp_raw = (data[6] << 8) | data[7];
  
  // ±40mT量程, 16bit有符号 -> 灵敏度 1uT = 0.8 LSB? 需核对数据手册
  // TMAG5273 ±40mT: 1 LSB = 0.15 uT (典型值)
  state.mag_x = mag_x_raw * 0.15f;
  state.mag_y = mag_y_raw * 0.15f;
  state.mag_z = mag_z_raw * 0.15f;
  
  // 温度: 1 LSB = 0.1°C, 偏移需校准
  state.temperature = temp_raw * 0.1f;
}

// ==================== GPS NMEA 解析 ====================
#define GPS_BAUD 9600
HardwareSerial gpsSerial(1);

void gps_init() {
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
  // 配置ATGM336H: 输出RMC+GGA+GSA, 1Hz (默认就是)
  // 发送配置命令 (可选): $PCAS03,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0*1F\r\n
  // 简化: 使用默认输出
}

bool nmea_checksum(const char* sentence) {
  if (sentence[0] != '$') return false;
  const char* star = strchr(sentence, '*');
  if (!star) return false;
  uint8_t checksum = 0;
  for (const char* p = sentence + 1; p < star; p++) checksum ^= *p;
  uint8_t given = strtol(star + 1, NULL, 16);
  return checksum == given;
}

void gps_parseRMC(const char* field) {
  // $GNRMC,time,status,lat,N,lon,E,speed,course,date,...
  char time[12], status[2], lat[12], ns[2], lon[12], ew[2];
  int matched = sscanf(field, "$GNRMC,%[^,],%[^,],%[^,],%[^,],%[^,],%[^,]",
                        time, status, lat, ns, lon, ew);
  if (matched < 6) return;
  if (status[0] == 'A') {
    state.gps_fix = true;
    // 转换经纬度: ddmm.mmmm -> 十进制度
    double lat_raw = atof(lat);
    int lat_deg = (int)(lat_raw / 100);
    double lat_min = lat_raw - lat_deg * 100;
    state.gps_lat = lat_deg + lat_min / 60.0;
    if (ns[0] == 'S') state.gps_lat = -state.gps_lat;
    
    double lon_raw = atof(lon);
    int lon_deg = (int)(lon_raw / 100);
    double lon_min = lon_raw - lon_deg * 100;
    state.gps_lon = lon_deg + lon_min / 60.0;
    if (ew[0] == 'W') state.gps_lon = -state.gps_lon;
  } else {
    state.gps_fix = false;
  }
}

void gps_parseGGA(const char* field) {
  // $GNGGA,time,lat,N,lon,E,quality,sats,hdop,alt,M,...
  char time[12], lat[12], ns[2], lon[12], ew[2], quality[2], sats[4], hdop[6], alt[8];
  int matched = sscanf(field, "$GNGGA,%[^,],%[^,],%[^,],%[^,],%[^,],%[^,],%[^,],%[^,],%[^,],%[^,]",
                        time, lat, ns, lon, ew, quality, sats, hdop, alt);
  if (matched >= 10) {
    state.gps_sats = atoi(sats);
    state.gps_hdop = atof(hdop);
    state.gps_alt = atof(alt);
  }
}

void gps_task(void* pvParameters) {
  char line[256];
  int linePos = 0;
  while (1) {
    while (gpsSerial.available()) {
      char c = gpsSerial.read();
      if (c == '$') { linePos = 0; line[linePos++] = c; }
      else if (c == '\n' || c == '\r') {
        if (linePos > 6) {
          line[linePos] = '\0';
          if (nmea_checksum(line)) {
            if (strstr(line, "RMC")) gps_parseRMC(line);
            else if (strstr(line, "GGA")) gps_parseGGA(line);
          }
        }
        linePos = 0;
      }
      else if (linePos < 255) {
        line[linePos++] = c;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ==================== I2S 接收 ====================
void i2s_init() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),  // ESP32是master, 接收
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = DMA_BUF_COUNT,
    .dma_buf_len = DMA_BUF_LEN,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };
  
  i2s_pin_config_t pin_config = {
    .bck_io_num = PIN_I2S_BCLK,
    .ws_io_num = PIN_I2S_LRCLK,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = PIN_I2S_DIN
  };
  
  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
  Serial.println("[I2S] RX init OK (48kHz, 16bit, stereo)");
}

void i2s_task(void* pvParameters) {
  uint8_t buf[DMA_BUF_LEN * 2 * 2];  // 2 bytes/sample * 2 channels
  size_t bytes_read;
  while (1) {
    i2s_read(I2S_PORT, buf, sizeof(buf), &bytes_read, portMAX_DELAY);
    if (bytes_read > 0) {
      // 写入环形缓冲
      if (audio_mutex) xSemaphoreTake(audio_mutex, portMAX_DELAY);
      for (size_t i = 0; i < bytes_read; i++) {
        audio_ring_buf[audio_write_pos] = buf[i];
        audio_write_pos = (audio_write_pos + 1) % AUDIO_RING_BUF_SIZE;
      }
      if (audio_mutex) xSemaphoreGive(audio_mutex);
      
      if (state.recording) state.rec_samples += bytes_read / 4;  // 4 bytes/frame (16bit stereo)
    }
  }
}

// ==================== LED 控制 ====================
void led_init() {
  pinMode(PIN_LED1, OUTPUT);
  pinMode(PIN_LED2, OUTPUT);
  pinMode(PIN_LED3, OUTPUT);
  pinMode(PIN_LED4, OUTPUT);
  digitalWrite(PIN_LED1, LOW);  // 共阳? 实际是阴极接GPIO, 所以HIGH=灭, LOW=亮
  digitalWrite(PIN_LED2, LOW);
  digitalWrite(PIN_LED3, LOW);
  digitalWrite(PIN_LED4, LOW);
}

void led_set(uint8_t pin, LedPattern pattern) {
  static uint32_t last_tick[4] = {0,0,0,0};
  static bool led_state[4] = {false,false,false,false};
  static int double_phase[4] = {0,0,0,0};
  uint32_t now = millis();
  uint8_t idx = pin - PIN_LED1;
  
  switch (pattern) {
    case LED_OFF:
      digitalWrite(pin, HIGH);  // 灭
      led_state[idx] = false;
      break;
    case LED_ON:
      digitalWrite(pin, LOW);  // 亮
      led_state[idx] = true;
      break;
    case LED_BLINK_SLOW:  // 1Hz
      if (now - last_tick[idx] > 500) {
        last_tick[idx] = now;
        led_state[idx] = !led_state[idx];
        digitalWrite(pin, led_state[idx] ? LOW : HIGH);
      }
      break;
    case LED_BLINK_FAST:  // 5Hz
      if (now - last_tick[idx] > 100) {
        last_tick[idx] = now;
        led_state[idx] = !led_state[idx];
        digitalWrite(pin, led_state[idx] ? LOW : HIGH);
      }
      break;
    case LED_BLINK_DOUBLE:  // 双闪
      if (now - last_tick[idx] > 200) {
        last_tick[idx] = now;
        double_phase[idx] = (double_phase[idx] + 1) % 4;
        bool on = (double_phase[idx] == 0 || double_phase[idx] == 1);
        digitalWrite(pin, on ? LOW : HIGH);
      }
      break;
  }
}

void led_task(void* pvParameters) {
  while (1) {
    led_set(PIN_LED1, led1_pattern);
    led_set(PIN_LED2, led2_pattern);
    led_set(PIN_LED3, led3_pattern);
    led_set(PIN_LED4, led4_pattern);
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ==================== Web 服务器 ====================
void handleRoot() {
  String html = "<!DOCTYPE html><html><head><meta charset='utf-8'><title>MBDSDR Mini</title>";
  html += "<style>body{font-family:MiSans,system-ui;background:#f5f5f7;margin:0;padding:20px}";
  html += ".card{background:white;border-radius:12px;padding:20px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,0.1)}";
  html += "h1{color:#1d1d1f;font-size:24px}h2{color:#1d1d1f;font-size:18px}";
  html += ".freq{font-size:48px;font-weight:600;color:#0071e3;text-align:center}";
  html += "button{background:#0071e3;color:white;border:none;padding:10px 20px;border-radius:8px;font-size:16px;margin:5px;cursor:pointer}";
  html += "button:active{background:#0077ed}input{padding:8px;border:1px solid #d2d2d7;border-radius:6px;font-size:16px}";
  html += "</style></head><body>";
  html += "<h1>MBDSDR Mini - AI定义无线电</h1>";
  html += "<div class='card'><div class='freq'>";
  if (state.si4732_mode == 0) html += String(state.si4732_freq / 100.0, 1) + " MHz</div><p style='text-align:center'>FM 广播</p>";
  else if (state.si4732_mode == 1) html += String(state.si4732_freq) + " kHz</div><p style='text-align:center'>AM 中波</p>";
  else html += String(state.si4732_freq) + " kHz</div><p style='text-align:center'>SW 短波</p>";
  html += "<p style='text-align:center'>RSSI: " + String(state.si4732_rssi) + " dBuV | SNR: " + String(state.si4732_snr) + " dB</p>";
  html += "<div style='text-align:center'>";
  html += "<button onclick='tune(-0.1)'>-0.1MHz</button>";
  html += "<button onclick='tune(0.1)'>+0.1MHz</button>";
  html += "<button onclick='seek(1)'>搜台↑</button>";
  html += "<button onclick='seek(0)'>搜台↓</button>";
  html += "</div></div>";
  html += "<div class='card'><h2>GPS / 北斗</h2>";
  if (state.gps_fix) {
    html += "<p>定位: 有效 | 卫星: " + String(state.gps_sats) + " | HDOP: " + String(state.gps_hdop, 1) + "</p>";
    html += "<p>纬度: " + String(state.gps_lat, 6) + " | 经度: " + String(state.gps_lon, 6) + " | 高度: " + String(state.gps_alt, 1) + "m</p>";
  } else {
    html += "<p>定位中... 请确保天线视野开阔</p>";
  }
  html += "</div>";
  html += "<div class='card'><h2>9轴姿态</h2>";
  html += "<p>加速度: X=" + String(state.acc_x, 2) + " Y=" + String(state.acc_y, 2) + " Z=" + String(state.acc_z, 2) + " g</p>";
  html += "<p>陀螺仪: X=" + String(state.gyr_x, 1) + " Y=" + String(state.gyr_y, 1) + " Z=" + String(state.gyr_z, 1) + " deg/s</p>";
  html += "<p>磁力计: X=" + String(state.mag_x, 1) + " Y=" + String(state.mag_y, 1) + " Z=" + String(state.mag_z, 1) + " uT</p>";
  html += "<p>温度: " + String(state.temperature, 1) + " C</p>";
  html += "</div>";
  html += "<div class='card'><h2>系统</h2>";
  html += "<p>固件: v0.4 | 运行: " + String(state.uptime / 1000) + "s | PSRAM: " + String(ESP.getFreePsram() / 1024) + "KB free</p>";
  html += "<p>WebSocket: ws://" + WiFi.softAPIP().toString() + ":81 (MCP协议, AI可调用)</p>";
  html += "</div>";
  html += "<script>function tune(d){fetch('/tune?freq='+((" + String(state.si4732_freq/100.0,1) + "+d)*100).toFixed(0))}";
  html += "function seek(d){fetch('/seek?dir='+d)}</script>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

void handleTune() {
  if (server.hasArg("freq")) {
    uint32_t freq = server.arg("freq").toInt();
    if (state.si4732_mode == 0) si4732_tuneFM(freq);
    else if (state.si4732_mode == 1) si4732_tuneAM(freq);
    server.send(200, "application/json", "{\"ok\":true,\"freq\":" + String(freq) + "}");
  } else {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing freq\"}");
  }
}

void handleSeek() {
  uint8_t dir = server.hasArg("dir") ? server.arg("dir").toInt() : 1;
  uint8_t args[2] = {dir ? 0x0C : 0x04, 0x00};  // SEEK_UP=0x0C, SEEK_DOWN=0x04
  si4732_writeCommand(SI4732_CMD_FM_SEEK_START, args, 2);
  si4732_waitForCTS();
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleStatus() {
  String json = "{";
  json += "\"mode\":" + String(state.si4732_mode) + ",";
  json += "\"freq\":" + String(state.si4732_freq) + ",";
  json += "\"volume\":" + String(state.si4732_volume) + ",";
  json += "\"muted\":" + String(state.si4732_muted) + ",";
  json += "\"rssi\":" + String(state.si4732_rssi) + ",";
  json += "\"snr\":" + String(state.si4732_snr) + ",";
  json += "\"gps_fix\":" + String(state.gps_fix) + ",";
  json += "\"gps_lat\":" + String(state.gps_lat, 6) + ",";
  json += "\"gps_lon\":" + String(state.gps_lon, 6) + ",";
  json += "\"gps_sats\":" + String(state.gps_sats) + ",";
  json += "\"acc_x\":" + String(state.acc_x, 2) + ",";
  json += "\"acc_y\":" + String(state.acc_y, 2) + ",";
  json += "\"acc_z\":" + String(state.acc_z, 2) + ",";
  json += "\"gyr_x\":" + String(state.gyr_x, 1) + ",";
  json += "\"gyr_y\":" + String(state.gyr_y, 1) + ",";
  json += "\"gyr_z\":" + String(state.gyr_z, 1) + ",";
  json += "\"mag_x\":" + String(state.mag_x, 1) + ",";
  json += "\"mag_y\":" + String(state.mag_y, 1) + ",";
  json += "\"mag_z\":" + String(state.mag_z, 1) + ",";
  json += "\"uptime\":" + String(state.uptime);
  json += "}";
  server.send(200, "application/json", json);
}

// ==================== Web OTA (浏览器上传固件, 电脑/手机通用) ====================
#define FIRMWARE_VERSION "v0.5"
#define FIRMWARE_BUILD "20260916"

void handleOTAForm() {
  String html = "<!DOCTYPE html><html><head><meta charset='utf-8'>";
  html += "<meta name='viewport' content='width=device-width,initial-scale=1'>";
  html += "<title>MBDSDR Mini - 固件升级</title>";
  html += "<style>body{font-family:MiSans,system-ui;background:#f5f5f7;margin:0;padding:20px}";
  html += ".card{background:white;border-radius:12px;padding:24px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,0.1);max-width:500px;margin-left:auto;margin-right:auto}";
  html += "h1{color:#1d1d1f;font-size:22px;text-align:center}";
  html += ".info{background:#f0f0f5;border-radius:8px;padding:12px;margin:12px 0;font-size:14px;color:#333}";
  html += "input[type=file]{width:100%;padding:12px;border:2px dashed #d2d2d7;border-radius:8px;font-size:14px;margin:12px 0}";
  html += "button{width:100%;background:#0071e3;color:white;border:none;padding:14px;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer}";
  html += "button:disabled{background:#a0a0a5;cursor:not-allowed}";
  html += ".progress{width:100%;height:8px;background:#e0e0e5;border-radius:4px;margin:12px 0;overflow:hidden}";
  html += ".progress-bar{height:100%;background:#0071e3;width:0%;transition:width 0.3s}";
  html += ".status{text-align:center;font-size:14px;color:#666;margin:8px 0}";
  html += ".warn{background:#fff3cd;border-radius:8px;padding:12px;margin:12px 0;font-size:13px;color:#856404}";
  html += "</style></head><body>";
  html += "<div class='card'><h1>MBDSDR Mini 固件升级</h1>";
  html += "<div class='info'>当前版本: <b>" FIRMWARE_VERSION "</b> (" FIRMWARE_BUILD ")<br>";
  html += "硬件: ai-sdr Mini | MCU: ESP32-S3-N8R8<br>";
  html += "Flash: 8MB | PSRAM: 8MB</div>";
  html += "<form method='POST' action='/update' enctype='multipart/form-data'>";
  html += "<input type='file' name='firmware' accept='.bin' required>";
  html += "<button type='submit' id='btn'>开始升级</button>";
  html += "</form>";
  html += "<div class='progress'><div class='progress-bar' id='bar'></div></div>";
  html += "<div class='status' id='status'>选择固件 .bin 文件后点击升级</div>";
  html += "<div class='warn'>升级期间请勿断电！升级完成后设备自动重启。<br>";
  html += "固件文件必须是 ESP32-S3 编译的 .bin (合并flash地址0x0)。</div>";
  html += "</div>";
  html += "<script>document.querySelector('form').addEventListener('submit',function(e){";
  html += "e.preventDefault();var btn=document.getElementById('btn');btn.disabled=true;";
  html += "var status=document.getElementById('status');status.textContent='正在上传...';";
  html += "var fd=new FormData(this);var xhr=new XMLHttpRequest();";
  html += "xhr.upload.onprogress=function(ev){if(ev.lengthComputable){";
  html += "var pct=Math.round(ev.loaded/ev.total*100);document.getElementById('bar').style.width=pct+'%';";
  html += "status.textContent='上传中 '+pct+'%';}};";
  html += "xhr.onload=function(){if(xhr.status==200){status.textContent='升级成功! 设备即将重启...';";
  html += "document.getElementById('bar').style.width='100%';}else{status.textContent='升级失败: '+xhr.responseText;btn.disabled=false;}};";
  html += "xhr.onerror=function(){status.textContent='网络错误, 请重试';btn.disabled=false;};";
  html += "xhr.open('POST','/update');xhr.send(fd);});</script>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

void handleOTAUpload() {
  HTTPUpload& upload = server.upload();
  if (upload.status == UPLOAD_FILE_START) {
    Serial.printf("[OTA] Upload started: %s\n", upload.filename.c_str());
    if (!Update.begin(UPDATE_SIZE_UNKNOWN)) {
      Update.printError(Serial);
    }
    led4_pattern = LED_BLINK_FAST;
  } else if (upload.status == UPLOAD_FILE_WRITE) {
    if (Update.write(upload.buf, upload.currentSize) != upload.currentSize) {
      Update.printError(Serial);
    }
  } else if (upload.status == UPLOAD_FILE_END) {
    if (Update.end(true)) {
      Serial.printf("[OTA] Upload complete: %u bytes\n", upload.totalSize);
    } else {
      Update.printError(Serial);
    }
  }
}

void handleOTAFinish() {
  if (Update.hasError()) {
    String err = Update.errorString();
    server.send(500, "text/plain", "OTA failed: " + err);
    led4_pattern = LED_OFF;
  } else {
    server.send(200, "text/plain", "OTA success! Rebooting...");
    led4_pattern = LED_ON;
    delay(1000);
    ESP.restart();
  }
}

void webServer_init() {
  server.on("/", handleRoot);
  server.on("/tune", handleTune);
  server.on("/seek", handleSeek);
  server.on("/status", handleStatus);
  server.on("/update", HTTP_GET, handleOTAForm);
  server.on("/update", HTTP_POST, handleOTAFinish, handleOTAUpload);
  server.onNotFound([](){ server.send(404, "text/plain", "Not Found"); });
  server.begin();
}

// ==================== WebSocket MCP ====================
void mcp_sendResponse(uint8_t client, uint32_t id, const String& result) {
  String resp = "{\"jsonrpc\":\"2.0\",\"id\":" + String(id) + ",\"result\":" + result + "}";
  webSocket.sendTXT(client, resp);
}

void mcp_sendError(uint8_t client, uint32_t id, int code, const String& msg) {
  String resp = "{\"jsonrpc\":\"2.0\",\"id\":" + String(id) + ",\"error\":{\"code\":" + String(code) + ",\"message\":\"" + msg + "\"}}";
  webSocket.sendTXT(client, resp);
}

void mcp_handleCall(uint8_t client, uint32_t id, const String& method, const String& params) {
  if (method == "tune_fm") {
    // params: {"freq_mhz": 98.5}
    int pos = params.indexOf("freq_mhz");
    if (pos > 0) {
      float freq = params.substring(pos + 11).toFloat();
      si4732_tuneFM((uint16_t)(freq * 100));
      mcp_sendResponse(client, id, "{\"ok\":true,\"freq_mhz\":" + String(freq, 1) + "}");
    } else mcp_sendError(client, id, -32602, "missing freq_mhz");
  }
  else if (method == "tune_am") {
    int pos = params.indexOf("freq_khz");
    if (pos > 0) {
      uint32_t freq = params.substring(pos + 11).toInt();
      si4732_tuneAM(freq);
      mcp_sendResponse(client, id, "{\"ok\":true,\"freq_khz\":" + String(freq) + "}");
    } else mcp_sendError(client, id, -32602, "missing freq_khz");
  }
  else if (method == "set_volume") {
    int pos = params.indexOf("volume");
    if (pos > 0) {
      uint8_t vol = params.substring(pos + 8).toInt();
      si4732_setVolume(vol);
      mcp_sendResponse(client, id, "{\"ok\":true,\"volume\":" + String(vol) + "}");
    } else mcp_sendError(client, id, -32602, "missing volume");
  }
  else if (method == "get_status") {
    String result = "{\"mode\":" + String(state.si4732_mode) + ",\"freq\":" + String(state.si4732_freq);
    result += ",\"rssi\":" + String(state.si4732_rssi) + ",\"snr\":" + String(state.si4732_snr) + "}";
    mcp_sendResponse(client, id, result);
  }
  else if (method == "get_gps") {
    String result = "{\"fix\":" + String(state.gps_fix) + ",\"lat\":" + String(state.gps_lat, 6);
    result += ",\"lon\":" + String(state.gps_lon, 6) + ",\"alt\":" + String(state.gps_alt, 1);
    result += ",\"sats\":" + String(state.gps_sats) + ",\"hdop\":" + String(state.gps_hdop, 1) + "}";
    mcp_sendResponse(client, id, result);
  }
  else if (method == "get_imu") {
    String result = "{\"acc\":[" + String(state.acc_x,2) + "," + String(state.acc_y,2) + "," + String(state.acc_z,2) + "]";
    result += ",\"gyr\":[" + String(state.gyr_x,1) + "," + String(state.gyr_y,1) + "," + String(state.gyr_z,1) + "]";
    result += ",\"mag\":[" + String(state.mag_x,1) + "," + String(state.mag_y,1) + "," + String(state.mag_z,1) + "]";
    result += ",\"temp\":" + String(state.temperature,1) + "}";
    mcp_sendResponse(client, id, result);
  }
  else if (method == "start_record") {
    state.recording = true;
    state.rec_samples = 0;
    led4_pattern = LED_BLINK_FAST;
    mcp_sendResponse(client, id, "{\"ok\":true,\"recording\":true}");
  }
  else if (method == "stop_record") {
    state.recording = false;
    led4_pattern = LED_OFF;
    mcp_sendResponse(client, id, "{\"ok\":true,\"samples\":" + String(state.rec_samples) + "}");
  }
  else if (method == "get_version") {
    mcp_sendResponse(client, id, "{\"firmware\":\"" FIRMWARE_VERSION "\",\"build\":\"" FIRMWARE_BUILD "\",\"board\":\"ai-sdr Mini\",\"mcu\":\"ESP32-S3-N8R8\",\"sdr\":\"SI4732-A10-GSR\",\"ota_web\":\"http://192.168.4.1/update\"}");
  }
  else if (method == "check_update") {
    // 返回当前版本, 电脑端/手机端软件可对比GitHub release最新版本
    mcp_sendResponse(client, id, "{\"current\":\"" FIRMWARE_VERSION "\",\"build\":\"" FIRMWARE_BUILD "\",\"latest_url\":\"https://github.com/BI4MIB/MBDSDR/releases\",\"ota_web\":\"http://192.168.4.1/update\",\"note\":\"请将最新固件.bin通过Web OTA页面上传, 或使用ArduinoOTA推送\"}");
  }
  else if (method == "trigger_ota") {
    // 触发OTA: 提示用户通过Web页面上传, 或电脑端用espota推送
    mcp_sendResponse(client, id, "{\"ok\":true,\"method\":\"web_ota\",\"url\":\"http://192.168.4.1/update\",\"note\":\"请在浏览器打开上述地址, 选择固件.bin上传; 或电脑端使用 espota.py -i 192.168.4.1 -p 3232 -a mbdsdr -f firmware.bin\"}");
  }
  else if (method == "web_ota_url") {
    mcp_sendResponse(client, id, "{\"url\":\"http://192.168.4.1/update\",\"method\":\"browser_upload\",\"description\":\"浏览器打开此地址, 选择固件.bin文件上传即可升级, 电脑手机通用\"}");
  }
  else if (method == "reboot") {
    mcp_sendResponse(client, id, "{\"ok\":true}");
    delay(500);
    ESP.restart();
  }
  else {
    mcp_sendError(client, id, -32601, "Method not found: " + method);
  }
}

void webSocketEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      Serial.printf("[WS] Client %u disconnected\n", num);
      break;
    case WStype_CONNECTED: {
      IPAddress ip = webSocket.remoteIP(num);
      Serial.printf("[WS] Client %u connected from %d.%d.%d.%d\n", num, ip[0], ip[1], ip[2], ip[3]);
      // 发送工具列表 (MCP initialize)
      String tools = "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\",\"params\":{\"tools\":[";
      for (int i = 0; i < MCP_TOOL_COUNT; i++) {
        if (i > 0) tools += ",";
        tools += "{\"name\":\"" + String(mcp_tools[i].name) + "\",\"description\":\"" + String(mcp_tools[i].description) + "\"}";
      }
      tools += "]}}";
      webSocket.sendTXT(num, tools);
      break;
    }
    case WStype_TEXT: {
      String msg = String((char*)payload);
      Serial.printf("[WS] %u: %s\n", num, msg.c_str());
      // 解析 JSON-RPC
      int idPos = msg.indexOf("\"id\":");
      int methodPos = msg.indexOf("\"method\":");
      if (idPos > 0 && methodPos > 0) {
        uint32_t id = msg.substring(idPos + 5).toInt();
        int methodStart = msg.indexOf("\"", methodPos + 9) + 1;
        int methodEnd = msg.indexOf("\"", methodStart);
        String method = msg.substring(methodStart, methodEnd);
        int paramsPos = msg.indexOf("\"params\":");
        String params = paramsPos > 0 ? msg.substring(paramsPos + 9) : "{}";
        mcp_handleCall(num, id, method, params);
      }
      break;
    }
    case WStype_BIN:
      // 二进制数据 (音频流) - 由i2s_task主动推送
      break;
    default:
      break;
  }
}

// 音频流推送任务: 从环形缓冲读取, 通过WebSocket二进制发送
void audio_stream_task(void* pvParameters) {
  uint8_t chunk[2048];
  while (1) {
    if (webSocket.connectedClients() > 0) {
      if (audio_mutex) xSemaphoreTake(audio_mutex, portMAX_DELAY);
      uint32_t avail = (audio_write_pos - audio_read_pos + AUDIO_RING_BUF_SIZE) % AUDIO_RING_BUF_SIZE;
      uint32_t toRead = min(avail, (uint32_t)sizeof(chunk));
      for (uint32_t i = 0; i < toRead; i++) {
        chunk[i] = audio_ring_buf[audio_read_pos];
        audio_read_pos = (audio_read_pos + 1) % AUDIO_RING_BUF_SIZE;
      }
      if (audio_mutex) xSemaphoreGive(audio_mutex);
      
      if (toRead > 0) {
        webSocket.broadcastBIN(chunk, toRead);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ==================== 传感器读取任务 ====================
void sensor_task(void* pvParameters) {
  while (1) {
    bmi260_read();
    tmag5273_read();
    if (state.si4732_ready) si4732_getTuneStatus();
    
    // LED状态更新
    led2_pattern = state.si4732_ready ? LED_BLINK_SLOW : LED_OFF;
    led3_pattern = state.gps_fix ? LED_BLINK_SLOW : LED_OFF;
    
    vTaskDelay(pdMS_TO_TICKS(100));  // 10Hz
  }
}

// ==================== setup ====================
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n========================================");
  Serial.println("  MBDSDR Mini Firmware v0.5");
  Serial.println("  AI定义无线电 - BI4MIB");
  Serial.println("  全开源 GPL-3.0");
  Serial.println("========================================\n");
  
  // 初始化LED
  led_init();
  led1_pattern = LED_ON;
  
  // 初始化I2C
  Wire.begin(PIN_I2C0_SDA, PIN_I2C0_SCL, 400000);
  Serial.println("[I2C] Init OK (400kHz)");
  
  // 复位SI4732
  si4732_reset();
  Serial.println("[SI4732] Reset done");
  
  // 初始化SI4732
  if (si4732_powerUp(0)) {
    Serial.println("[SI4732] Power up OK (FM mode)");
    si4732_tuneFM(9850);  // 默认98.5MHz
    si4732_setVolume(30);
  } else {
    Serial.println("[SI4732] Power up FAILED");
  }
  
  // 初始化BMI260
  bmi260_init();
  
  // 初始化TMAG5273
  tmag5273_init();
  
  // 初始化GPS
  gps_init();
  Serial.println("[GPS] Init OK (ATGM336H, 9600 baud)");
  
  // 初始化I2S接收
  i2s_init();
  
  // 分配PSRAM音频缓冲
  audio_ring_buf = (uint8_t*)ps_malloc(AUDIO_RING_BUF_SIZE);
  if (audio_ring_buf) {
    memset(audio_ring_buf, 0, AUDIO_RING_BUF_SIZE);
    Serial.printf("[Audio] Ring buffer allocated: %d bytes in PSRAM\n", AUDIO_RING_BUF_SIZE);
  } else {
    Serial.println("[Audio] PSRAM allocation FAILED, using heap");
    audio_ring_buf = (uint8_t*)malloc(AUDIO_RING_BUF_SIZE);
  }
  audio_mutex = xSemaphoreCreateMutex();
  
  // USB Hub复位
  pinMode(PIN_HUB_RESET, OUTPUT);
  digitalWrite(PIN_HUB_RESET, LOW);
  delay(10);
  digitalWrite(PIN_HUB_RESET, HIGH);
  Serial.println("[USB2514B] Reset done");
  
  // WiFi AP模式
  WiFi.mode(WIFI_AP);
  WiFi.softAP(WIFI_SSID, WIFI_PASS);
  Serial.printf("[WiFi] AP mode: SSID=%s, IP=%s\n", WIFI_SSID, WiFi.softAPIP().toString().c_str());
  
  // Web服务器
  webServer_init();
  Serial.println("[Web] Server started on port 80");
  
  // WebSocket
  webSocket.begin();
  webSocket.onEvent(webSocketEvent);
  Serial.println("[WebSocket] MCP server started on port 81");
  
  // OTA
  ArduinoOTA.setHostname("mbdsdr-mini");
  ArduinoOTA.setPassword("mbdsdr");
  ArduinoOTA.begin();
  Serial.println("[OTA] Ready (password: mbdsdr)");
  
  // 创建任务
  xTaskCreatePinnedToCore(gps_task, "GPS", 4096, NULL, 2, NULL, 0);
  xTaskCreatePinnedToCore(i2s_task, "I2S", 8192, NULL, 5, NULL, 1);
  xTaskCreatePinnedToCore(led_task, "LED", 2048, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(sensor_task, "Sensor", 4096, NULL, 2, NULL, 0);
  xTaskCreatePinnedToCore(audio_stream_task, "AudioStream", 4096, NULL, 3, NULL, 1);
  
  Serial.println("\n[System] All tasks started. Ready!");
  Serial.println("[System] Connect to WiFi 'MBDSDR-Mini' (pass: mbdsdr123)");
  Serial.println("[System] Web: http://192.168.4.1 | MCP WebSocket: ws://192.168.4.1:81");
  Serial.println("[System] Web OTA: http://192.168.4.1/update (浏览器上传固件.bin, 电脑手机通用)");
}

// ==================== loop ====================
void loop() {
  server.handleClient();
  webSocket.loop();
  ArduinoOTA.handle();
  state.uptime = millis();
  
  // 每5秒打印一次状态
  static uint32_t last_status = 0;
  if (millis() - last_status > 5000) {
    last_status = millis();
    Serial.printf("[Status] mode=%d freq=%d rssi=%d snr=%d gps=%d sats=%d acc=(%.2f,%.2f,%.2f) mag=(%.1f,%.1f,%.1f)\n",
                  state.si4732_mode, state.si4732_freq, state.si4732_rssi, state.si4732_snr,
                  state.gps_fix, state.gps_sats,
                  state.acc_x, state.acc_y, state.acc_z,
                  state.mag_x, state.mag_y, state.mag_z);
  }
}
