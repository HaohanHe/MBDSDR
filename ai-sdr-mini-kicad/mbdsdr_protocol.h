/*
 * ai-sdr Mini - ESP32-S3 固件通信协议定义
 * 项目: MBDSDR (AI-Defined Radio)
 * 版本: v0.3
 * 
 * 通信方式: USB CDC 串口 + WiFi TCP
 * 波特率: 921600 (USB CDC 不受波特率限制，此为默认值)
 */

#ifndef MBDSDR_PROTOCOL_H
#define MBDSDR_PROTOCOL_H

#include <stdint.h>

// ==================== 帧格式 ====================
// [0xAA][0x55][长度L(2B, 小端)][类型(1B)][数据(L字节)][校验(1B, XOR)]
// 校验 = 类型 XOR 所有数据字节

#define MBDSDR_FRAME_HEADER_1  0xAA
#define MBDSDR_FRAME_HEADER_2  0x55
#define MBDSFR_MAX_DATA_SIZE   512
#define MBDSDR_FRAME_OVERHEAD  6  // 2(header) + 2(length) + 1(type) + 1(checksum)

// ==================== 数据类型 ====================

// --- 设备 → 主机 (遥测数据) ---
#define MSG_AUDIO_PCM          0x01  // 音频PCM数据 (16-bit, 采样率可配置)
#define MSG_GPS_NMEA           0x02  // GPS NMEA语句 (文本)
#define MSG_IMU_DATA           0x03  // IMU数据 (加速度+陀螺+磁力计+姿态)
#define MSG_STATUS             0x04  // 状态包 (电池/存储/错误/温度)
#define MSG_SDR_IQ             0x05  // SDR IQ数据 (8-bit, 可选透传)
#define MSG_WIFI_INFO          0x06  // WiFi状态信息

// --- 主机 → 设备 (控制命令) ---
#define CMD_START_AUDIO        0x10  // 开始音频采集
#define CMD_STOP_AUDIO         0x11  // 停止音频采集
#define CMD_SET_SAMPLE_RATE    0x12  // 设置采样率 (数据: uint32_t Hz)
#define CMD_START_RECORD       0x13  // 开始SD卡录制
#define CMD_STOP_RECORD        0x14  // 停止SD卡录制
#define CMD_SET_LED            0x15  // 设置LED (数据: [led_index(1B)][state(1B)])
#define CMD_GET_STATUS         0x16  // 请求状态包
#define CMD_WIFI_START_AP      0x17  // 启动WiFi AP模式
#define CMD_WIFI_START_STA     0x18  // 启动WiFi STA模式 (数据: SSID+密码)
#define CMD_WIFI_STOP          0x19  // 停止WiFi
#define CMD_SET_GPS_RATE       0x1A  // 设置GPS更新率 (数据: uint8_t Hz)
#define CMD_SET_IMU_RATE       0x1B  // 设置IMU更新率 (数据: uint16_t Hz)
#define CMD_CALIBRATE_COMPASS  0x1C  // 罗盘校准开始
#define CMD_FACTORY_RESET      0x1F  // 恢复出厂设置

// --- 命令响应 ---
#define MSG_CMD_ACK            0x20  // 命令确认 (数据: [原命令类型(1B)][结果(1B)])
#define MSG_CMD_NAK            0x21  // 命令拒绝 (数据: [原命令类型(1B)][错误码(1B)])

// ==================== 结果/错误码 ====================
#define RESULT_OK              0x00
#define RESULT_FAIL            0x01
#define RESULT_BUSY            0x02
#define RESULT_INVALID_PARAM   0x03
#define RESULT_NOT_SUPPORTED   0x04

// ==================== IMU数据结构 ====================
typedef struct {
    float accel_x, accel_y, accel_z;  // 加速度 (g)
    float gyro_x, gyro_y, gyro_z;     // 陀螺仪 (deg/s)
    float mag_x, mag_y, mag_z;        // 磁力计 (uT)
    float roll, pitch, yaw;           // 姿态角 (度), yaw=方位角(0-360, 北=0)
    uint32_t timestamp_ms;             // 时间戳 (ms)
} ImuData_t;  // 32字节

// ==================== 状态包结构 ====================
typedef struct {
    uint8_t  battery_percent;    // 电池百分比 (0-100, USB供电时=100)
    float    battery_voltage;    // 电池电压 (V)
    uint8_t  sd_card_present;    // SD卡是否存在 (0/1)
    uint32_t sd_free_mb;         // SD卡剩余空间 (MB)
    uint8_t  audio_recording;    // 是否正在录音 (0/1)
    uint8_t  wifi_state;         // WiFi状态 (0=off,1=AP,2=STA)
    char     wifi_ip[16];        // WiFi IP地址
    uint8_t  gps_fix;            // GPS定位 (0=no fix,1=2D,2=3D)
    uint8_t  gps_satellites;     // GPS卫星数
    float    temperature_c;       // 板温 (°C)
    uint16_t error_flags;         // 错误标志位
    uint32_t uptime_s;            // 运行时间 (秒)
} StatusPacket_t;  // 约50字节

// ==================== LED索引 ====================
#define LED_PWR    0  // 红
#define LED_SDR    1  // 绿
#define LED_GPS    2  // 蓝
#define LED_REC    3  // 黄

// ==================== WiFi状态 ====================
#define WIFI_OFF   0
#define WIFI_AP    1
#define WIFI_STA   2

// ==================== 默认配置 ====================
#define DEFAULT_SAMPLE_RATE   16000  // 默认音频采样率 (Hz)
#define DEFAULT_GPS_RATE      10     // 默认GPS更新率 (Hz)
#define DEFAULT_IMU_RATE      100    // 默认IMU更新率 (Hz)
#define DEFAULT_WIFI_SSID     "ai-sdr"
#define DEFAULT_WIFI_PASSWORD "aisdr1234"
#define DEFAULT_WIFI_PORT     8888   // WiFi TCP数据端口

#endif // MBDSDR_PROTOCOL_H
