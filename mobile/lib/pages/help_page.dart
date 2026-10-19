// 应用内帮助手册（只读页）——sdroxide F1 手册模式的移动端落地。
//
// 定位：模型/用户对功能的全部疑问先在本页自足回答，再去问 AI 或翻文档。
// 诚实性规则：
//   * 只写本端真实存在的功能与真实设置项（设置页字段名逐一对应）；
//   * 不承诺未接线能力（原生端待验的功能明确标注）；
//   * 不内置任何电台频率/地理位置示例值——示例一律用「如 98.5」式通用写法。
library;

import 'package:flutter/material.dart';

import '../app/tokens.dart';

class HelpPage extends StatelessWidget {
  const HelpPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppTokens.bgMain,
      appBar: AppBar(
        backgroundColor: AppTokens.bgBar,
        title: const Text('帮助', style: AppTokens.appTitle),
      ),
      body: ListView(
        padding: const EdgeInsets.all(AppTokens.spacingL),
        children: const <Widget>[
          _Section(
            title: '快速开始',
            children: <Widget>[
              _Step(
                index: '1',
                text: '设置页 →「rtl_tcp 接收机」填入主机地址与端口。'
                    'RTL-SDR 棒由电脑/树莓派上的 rtl_tcp 服务器共享，手机与它同一局域网。',
              ),
              _Step(
                index: '2',
                text: '回频谱页，点连接。状态行显示已连接后，瀑布图开始实时绘制。',
              ),
              _Step(
                index: '3',
                text: '点瀑布图选频率，或拖动读数。选对解调模式即有解调音频 '
                    '（见下方「解调模式怎么选」）。',
              ),
            ],
          ),
          _Section(
            title: '每个页面做什么',
            children: <Widget>[
              _Row(term: '频谱', desc: '实时瀑布 + 调谐 + 书签 + 频段扫描。主战场。'),
              _Row(
                  term: '天空',
                  desc: '卫星过境预测与多普勒跟踪（需测站坐标；TLE 自动获取，过期会标注）。'),
              _Row(term: 'AI', desc: '对话式操作电台。需在设置页填 API key；可切手动模式只看不练。'),
              _Row(term: '录音', desc: '解调音频存为 wav，可回放。真实录制才生成文件。'),
              _Row(term: '活动', desc: '真实信号活动日志：静噪开门/扫描命中的时间与频点。'),
              _Row(term: '设置', desc: '电台地址、测站坐标、AI key、音频与外观选项。'),
            ],
          ),
          _Section(
            title: '解调模式怎么选',
            children: <Widget>[
              _Row(term: 'NFM', desc: '窄带调频：对讲机/业余中继（带宽约 12.5kHz）。'),
              _Row(term: 'WFM', desc: '宽带调频：调频广播（带宽约 150kHz）。'),
              _Row(term: 'AM', desc: '调幅：航空通话/短波广播（带宽约 6kHz）。'),
              _Row(term: 'CW / LSB / USB', desc: '等幅电报与单边带：弱信号远距通信。'),
            ],
          ),
          _Section(
            title: '常见问题',
            children: <Widget>[
              _Faq(
                q: '连不上电台？',
                a: '依次检查：手机与 rtl_tcp 服务器是否同一局域网；主机地址/端口是否填错；'
                    '服务器端 RTL-SDR 是否已被其他程序占用（同时只能一个）。',
              ),
              _Faq(
                q: '连接了但没声音？',
                a: '看静噪门限是否太高（信号低于门限被静音）；音量是否为 0；'
                    '模式是否与信号匹配（宽带信号用 NFM 听会只剩噪声）。',
              ),
              _Faq(
                q: 'AI 页不可用？',
                a: '设置页未填 API key。填好后 AI 对话与工具调用才可用；'
                    '「手动模式」下 AI 只回答、不真正改电台设置。',
              ),
              _Faq(
                q: '天空页没有卫星？',
                a: '先在设置页填测站坐标（纬度/经度/海拔）；TLE 需要联网获取，'
                    '过期条目会标注「过期」——刷新后恢复。',
              ),
            ],
          ),
          _Section(
            title: '术语速查',
            children: <Widget>[
              _Row(term: 'IQ', desc: '同相/正交两路采样，SDR 的原始数据形态。'),
              _Row(term: '采样率', desc: '每秒采样数，决定可见带宽上限（约为采样率八成）。'),
              _Row(term: '带宽', desc: '解调低通滤波器宽度，按信号类型选。'),
              _Row(term: '静噪', desc: '低于门限静音。门限为相对电平 dBFS，-100 为关闭。'),
              _Row(term: 'VFO', desc: '预存的频率+模式预设，可快速切换守听。'),
              _Row(term: '多普勒', desc: '卫星高速运动引起的频移，过境时需持续跟踪补偿。'),
              _Row(term: 'TLE', desc: '卫星轨道根数，过境预测的输入；随轨道摄动逐渐失准。'),
            ],
          ),
        ],
      ),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.spacingXL),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: AppTokens.sectionTitle),
          const SizedBox(height: AppTokens.spacingM),
          ...children,
        ],
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step({required this.index, required this.text});

  final String index;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.spacingM),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          CircleAvatar(
            radius: AppTokens.spacingM + 2,
            backgroundColor: AppTokens.selectedFill,
            foregroundColor: AppTokens.selectedText,
            child: Text(index, style: AppTokens.auxiliary),
          ),
          const SizedBox(width: AppTokens.spacingM),
          Expanded(child: Text(text, style: AppTokens.body)),
        ],
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.term, required this.desc});

  final String term;
  final String desc;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.spacingS),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SizedBox(
            width: 72,
            child: Text(term, style: AppTokens.mono),
          ),
          Expanded(child: Text(desc, style: AppTokens.body)),
        ],
      ),
    );
  }
}

class _Faq extends StatelessWidget {
  const _Faq({required this.q, required this.a});

  final String q;
  final String a;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppTokens.spacingM),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(q, style: AppTokens.body.copyWith(fontWeight: AppTokens.weightMedium)),
          const SizedBox(height: AppTokens.spacingS),
          Text(a, style: AppTokens.auxiliary),
        ],
      ),
    );
  }
}
