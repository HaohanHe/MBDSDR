/* MBDSDR 产品前端 · 20-i18n.js
 * 单一文案来源：zh-CN / ja-JP / en-US。
 * 机制：① 静态 HTML 用中文原文建反查表，运行时扫描文本节点与 title/placeholder；
 *       ② 动态内容一律 t(key, vars)；③ 切换语言保存原文可反复应用。
 * 术语纪律：定轨（軌道決定 / orbit determination）只出现在对话模板与关键词识别中，
 *          不进入任何按钮、能力位、工具入口——见 docs/01-product-spec.md 铁律 4。
 */
(function () {
  "use strict";
  const MB = window.MBDSDR, S = MB.state;

  const D = {
    /* ---- 顶栏 / 形态 ---- */
    "app.tagline": { zh: "AI 定义无线电 · 时空操作台", ja: "AI 定義無線機 · 時空コンソール", en: "AI-Defined Radio · Spacetime Console" },
    "mode.sky": { zh: "星象仪模式", ja: "プラネタリウムモード", en: "Planetarium Mode" },
    "mode.pro": { zh: "专业模式", ja: "プロフェッショナルモード", en: "Professional Mode" },
    "app.demo": { zh: "演示会话", ja: "デモセッション", en: "Demo Session" },
    "app.title": { zh: "MBDSDR · 时空操作台 — 能力，而非流程", ja: "MBDSDR · 時空コンソール — 機能ではなく能力", en: "MBDSDR · Spacetime Console — Capability, not workflow" },
    "app.twoForms": { zh: "一套系统 · 两副面孔", ja: "一つのシステム · 二つの顔", en: "One system · two forms" },
    "app.slogan": { zh: "MBDSDR · 能力而非流程", ja: "MBDSDR · 機能ではなく能力", en: "MBDSDR · Capability, not workflow" },
    "lang.current": { zh: "中文", ja: "日本語", en: "English" },
    "lang.zhLabel": { zh: "中文", ja: "中国語", en: "Chinese" },
    "lang.switchTitle": { zh: "Language / 语言 / 言語", ja: "言語切り替え / Language", en: "Switch language / 言語" },

    /* ---- 搜索 / 侧栏 ---- */
    "search.placeholder": { zh: "搜索卫星 / 天体…（试试 NOAA 或 ISS）", ja: "衛星・天体を検索…（NOAA / ISS を試してください）", en: "Search satellites / objects… (try NOAA or ISS)" },
    "sidebar.login": { zh: "登录", ja: "ログイン", en: "Sign in" },
    "sidebar.tonight": { zh: "今夜", ja: "今夜", en: "Tonight" },
    "sidebar.observations": { zh: "我的观测", ja: "観測記録", en: "My Observations" },
    "sidebar.calendar": { zh: "日历", ja: "カレンダー", en: "Calendar" },
    "sidebar.viewSettings": { zh: "视图设置", ja: "表示設定", en: "View Settings" },
    "sidebar.caps": { zh: "能力矩阵", ja: "能力マトリクス", en: "Capability Matrix" },
    "sidebar.trackReceive": { zh: "追踪 / 接收", ja: "追跡 / 受信", en: "Track / Receive" },
    "sidebar.docs": { zh: "文档 / Wiki", ja: "ドキュメント / Wiki", en: "Docs / Wiki" },
    "sidebar.about": { zh: "关于", ja: "概要", en: "About" },
    "sidebar.privacy": { zh: "隐私", ja: "プライバシー", en: "Privacy" },
    "sidebar.sources": { zh: "数据来源", ja: "データ出典", en: "Data Sources" },
    "sidebar.sourceNote1": { zh: "星表：Gaia / Hipparcos · TLE：NORAD", ja: "星表：Gaia / Hipparcos · TLE：NORAD", en: "Catalog: Gaia / Hipparcos · TLE: NORAD" },
    "sidebar.sourceNote2": { zh: "引擎形态参照 Stellarium Web · AGPL", ja: "UI 形態は Stellarium Web を参照 · AGPL コード不使用", en: "UI form after Stellarium Web · no AGPL code" },

    /* ---- 信息卡 ---- */
    "card.sub": { zh: "气象卫星 · METEOR-M2", ja: "気象衛星 · METEOR-M2", en: "Weather satellite · METEOR-M2" },
    "card.alias": { zh: "又称", ja: "別名", en: "Also known as" },
    "card.lock": { zh: "摄像机锁定 · 天动星静", ja: "カメラ固定 · 天が動き衛星は静止", en: "Camera lock · sky moves, satellite stays" },
    "card.track": { zh: "追踪", ja: "追跡", en: "Track" },
    "card.receive": { zh: "接收", ja: "受信", en: "Receive" },
    "card.point": { zh: "指向", ja: "指向", en: "Point" },
    "card.moreWiki": { zh: "more on wikipedia", ja: "Wikipedia で見る", en: "more on wikipedia" },
    "card.norad": { zh: "NORAD", ja: "NORAD", en: "NORAD" },

    /* ---- 字段 ---- */
    "field.magnitude": { zh: "星等", ja: "等級", en: "Magnitude" },
    "field.distance": { zh: "距离", ja: "距離", en: "Distance" },
    "field.raDec": { zh: "Ra/Dec", ja: "赤経/赤緯", en: "RA / Dec" },
    "field.azAlt": { zh: "Az/Alt", ja: "方位/高度", en: "Az / Alt" },
    "field.riseSet": { zh: "升 {rise} / 落 {set}", ja: "昇 {rise} / 没 {set}", en: "rise {rise} / set {set}" },
    "field.visible": { zh: "可见", ja: "可視", en: "Visible" },
    "field.downlink": { zh: "下行", ja: "ダウンリンク", en: "Downlink" },
    "field.scheme": { zh: "体制", ja: "変調方式", en: "Scheme" },
    "field.doppler": { zh: "多普勒", ja: "ドップラー", en: "Doppler" },
    "field.orbit": { zh: "轨道", ja: "軌道", en: "Orbit" },
    "field.continuous": { zh: "持续", ja: "連続", en: "Continuous" },

    /* ---- 卫星类型 / 描述 ---- */
    "sat.type.weather": { zh: "气象卫星", ja: "気象衛星", en: "Weather satellite" },
    "sat.type.station": { zh: "空间站", ja: "宇宙ステーション", en: "Space station" },
    "sat.type.comm": { zh: "通信卫星", ja: "通信衛星", en: "Communications satellite" },
    "sat.meteor.desc": { zh: "俄罗斯 Meteor-M 系列极轨气象卫星，下行 LRPT 数字图像（72 ksym/s QPSK）。是本仓真录金样与图像链的标的星。", ja: "ロシア Meteor-M 系の極軌道気象衛星。LRPT ディジタル画像（72 ksym/s QPSK）を下行伝送。本リポジトリの実録ゴールデンテストと画像チェーンの対象星。", en: "Russian Meteor-M polar-orbiting weather satellite downlinking LRPT digital imagery (72 ksym/s QPSK). Reference target for the repo's real-recording golden tests and image chain." },
    "sat.noaa.desc": { zh: "NOAA 系列极轨气象卫星，APT 模拟图像双副载波 AM 下行，业余接收经典标的。", ja: "NOAA 系の極軌道気象衛星。APT アナログ画像を 2 副搬送波 AM で送信する、アマチュア受信の古典的対象。", en: "NOAA polar-orbiting weather satellite transmitting APT analogue imagery over dual-subcarrier AM — a classic amateur reception target." },
    "sat.iss.desc": { zh: "国际空间站（ISS）是近地轨道载人空间站，由五家航天机构共同运营：NASA、Roscosmos、ESA、JAXA、CSA……", ja: "国際宇宙ステーション（ISS）は低軌道の有人宇宙ステーションで、NASA・Roscosmos・ESA・JAXA・CSA の 5 機関が共同運用しています……", en: "The International Space Station is a crewed low-Earth-orbit station jointly operated by NASA, Roscosmos, ESA, JAXA and CSA…" },
    "sat.iridium.desc": { zh: "Iridium 星座卫星，L 波段突发信号，多普勒观测数据集标的。", ja: "Iridium コンステレーションの衛星。L 帯バースト信号、ドップラー観測データセットの対象。", en: "Iridium constellation satellite with L-band burst signals; target of a Doppler observation dataset." },
    "sat.starlink.desc": { zh: "SpaceX Starlink 星座低轨通信卫星，Ku 波段下行。", ja: "SpaceX Starlink コンステレーションの低軌道通信衛星、Ku 帯ダウンリンク。", en: "SpaceX Starlink LEO communications satellite with Ku-band downlink." },

    /* ---- 工具栏 ---- */
    "toolbar.orbits": { zh: "轨道线", ja: "軌道線", en: "Orbit lines" },
    "toolbar.labels": { zh: "目标标签", ja: "ラベル", en: "Labels" },
    "toolbar.milkyway": { zh: "银河", ja: "銀河", en: "Milky Way" },
    "toolbar.atmosphere": { zh: "大气 / 地平辉光", ja: "大気 / 地平輝光", en: "Atmosphere / horizon glow" },
    "toolbar.landscape": { zh: "地景（固定在地平系，不随时间转）", ja: "地景（地平座標系に固定、時間で回転しない）", en: "Landscape (fixed to horizon frame; does not rotate with time)" },
    "toolbar.grid": { zh: "赤纬网格", ja: "赤緯グリッド", en: "Declination grid" },
    "toolbar.night": { zh: "夜视模式", ja: "夜間視モード", en: "Night vision" },
    "toolbar.sats": { zh: "卫星层", ja: "衛星レイヤ", en: "Satellite layer" },
    "toolbar.time": { zh: "时间流速：⏸ / ▶ / ×60 / ×300 / ×1800", ja: "時間倍率：⏸ / ▶ / ×60 / ×300 / ×1800", en: "Time rate: pause / 1x / 60x / 300x / 1800x" },
    "toolbar.fullscreen": { zh: "全屏", ja: "全画面", en: "Fullscreen" },
    "toolbar.paused": { zh: "停", ja: "停", en: "0×" },
    "time.utc": { zh: "UTC", ja: "UTC", en: "UTC" },
    "corner.location": { zh: "示例站点 · 0.0, 0.0", ja: "観測地点例 · 0.0, 0.0", en: "Example site · 0.0, 0.0" },
    "site.untrusted": { zh: "站址未校准 · 锁定与过境预报不可用", ja: "観測地点が未較正 · ロック・パス予報は利用不可", en: "Site not calibrated · lock and pass prediction unavailable" },

    /* ---- 能力矩阵 ---- */
    "caps.head": { zh: "◧ 能力矩阵（原仓库工具域）", ja: "◧ 能力マトリクス（リポジトリのツールドメイン）", en: "◧ Capability Matrix (repo tool domains)" },
    "caps.badge": { zh: "事实源自动生成", ja: "ファクトソースから自動生成", en: "Generated from source" },
    "caps.count": { zh: "能力矩阵 {n}/{total} 在位", ja: "能力マトリクス {n}/{total} 有効", en: "Capability matrix {n}/{total} active" },
    "cap.sdr.name": { zh: "SDR 接收机", ja: "SDR 受信機", en: "SDR Receiver" },
    "cap.sdr.desc": { zh: "RTL-SDR / Pluto / 收发信机", ja: "RTL-SDR / Pluto / トランシーバ", en: "RTL-SDR / Pluto / transceivers" },
    "cap.gimbal.name": { zh: "云台 / 转台", ja: "雲台 / 回転台", en: "Gimbal / Rotator" },
    "cap.gimbal.desc": { zh: "天线指向 · 自动跟踪", ja: "アンテナ指向 · 自動追跡", en: "Antenna pointing · auto tracking" },
    "cap.gnss.name": { zh: "GNSS / 授时", ja: "GNSS / 時刻同期", en: "GNSS / Timing" },
    "cap.gnss.desc": { zh: "位置时间基准 · 1PPS", ja: "位置・時刻基準 · 1PPS", en: "Position & time base · 1PPS" },
    "cap.vis6dof.name": { zh: "6DOF 视觉引导", ja: "6DOF 視覚誘導", en: "6DOF Visual Guidance" },
    "cap.vis6dof.desc": { zh: "位姿估计 · 视线校正", ja: "姿勢推定 · 視線補正", en: "Pose estimation · line-of-sight correction" },
    "cap.calib.name": { zh: "视觉校准", ja: "視覚キャリブレーション", en: "Visual Calibration" },
    "cap.calib.desc": { zh: "相机-天线轴系标定", ja: "カメラ・アンテナ軸較正", en: "Camera–antenna axis calibration" },
    "cap.vrxr.name": { zh: "VR / XR", ja: "VR / XR", en: "VR / XR" },
    "cap.vrxr.desc": { zh: "沉浸视场 · 后续接入", ja: "没入視野 · 後続対応", en: "Immersive view · planned" },
    "cap.spec.name": { zh: "频谱分析", ja: "スペクトラム解析", en: "Spectrum Analysis" },
    "cap.spec.desc": { zh: "FFT / 瀑布 / 信道化", ja: "FFT / ウォーターフォール / チャネライズ", en: "FFT / waterfall / channelisation" },
    "cap.stack.name": { zh: "解调协议栈", ja: "復調プロトコルスタック", en: "Demodulation Stack" },
    "cap.stack.desc": { zh: "LRPT/APT/FT8/ADS-B/SSTV", ja: "LRPT/APT/FT8/ADS-B/SSTV", en: "LRPT/APT/FT8/ADS-B/SSTV" },
    "cap.point.name": { zh: "天线指向", ja: "アンテナ指向", en: "Antenna Pointing" },
    "cap.point.desc": { zh: "az/el 预置 · 随动", ja: "az/el プリセット · 追従", en: "az/el presets · tracking" },

    /* ---- AI 抽屉 ---- */
    "ai.head": { zh: "MBDSDR 模型", ja: "MBDSDR モデル", en: "MBDSDR Model" },
    "ai.sub": { zh: "现场能力协商 · 动态规划", ja: "その場の能力確認 · 動的プランニング", en: "On-the-spot capability negotiation · dynamic planning" },
    "ai.placeholder": { zh: "直接跟模型说…（能力协商 / 下达任务）", ja: "モデルに話しかける…（能力確認 / タスク指示）", en: "Talk to the model… (capability check / task)" },
    "ai.send": { zh: "说", ja: "送る", en: "Say" },
    "ai.note": { zh: "以下对话由模型现场组织 · 不是预制向导", ja: "対話はモデルがその場で構成 · 固定ウィザードではありません", en: "This dialogue is composed live by the model · not a prebuilt wizard" },
    "ai.you": { zh: "你", ja: "あなた", en: "You" },
    "ai.toolScanned": { zh: "已扫描", ja: "スキャン済み", en: "Scanned" },
    "ai.toolCalled": { zh: "已调用", ja: "呼び出し済み", en: "Called" },
    "ai.available": { zh: "可用", ja: "利用可", en: "Available" },
    "ai.unavailable": { zh: "不可用", ja: "利用不可", en: "Unavailable" },
    "ai.generated": { zh: "已生成", ja: "生成済み", en: "Generated" },

    /* ---- 专业模式 ---- */
    "pro.devices": { zh: "接收设备", ja: "受信機器", en: "Receivers" },
    "pro.device.rtl": { zh: "RTL-SDR（未连接）", ja: "RTL-SDR（未接続）", en: "RTL-SDR (not connected)" },
    "pro.device.pluto": { zh: "ADALM-Pluto（未接）", ja: "ADALM-Pluto（未接続）", en: "ADALM-Pluto (not connected)" },
    "pro.device.gimbal": { zh: "云台转台（未接）", ja: "雲台回転台（未接続）", en: "Gimbal rotator (not connected)" },
    "pro.stack": { zh: "解调协议栈", ja: "復調プロトコルスタック", en: "Demodulation Stack" },
    "pro.toolbox": { zh: "AI 工具箱", ja: "AI ツールボックス", en: "AI Toolbox" },
    "pro.scenes": { zh: "任务场景", ja: "タスクシナリオ", en: "Scenarios" },
    "scene.edu.name": { zh: "科教教培", ja: "教育・普及", en: "Education & Outreach" },
    "scene.edu.desc": { zh: "课堂可复现的天球、频谱与解码演示", ja: "授業で再現できる天球・スペクトラム・復調デモ", en: "Reproducible sky, spectrum and decoding demos for classrooms" },
    "scene.res.name": { zh: "科学研究", ja: "科学研究", en: "Scientific Research" },
    "scene.res.desc": { zh: "可追溯数据链、金样对拍与误差留痕", ja: "追跡可能なデータチェーン、ゴールデン対比、誤差記録", en: "Traceable data chains, golden comparisons, honest error logs" },
    "scene.com.name": { zh: "通联 / 应急", ja: "アマチュア通信 / 緊急時", en: "Comms / Emergency" },
    "scene.com.desc": { zh: "过境窗口、指向闭环与现场降级方案", ja: "パスウィンドウ、指向閉ループ、現場での縮退運用", en: "Pass windows, pointing loops and on-site degraded modes" },
    "pro.centerFreq": { zh: "中心频率", ja: "中心周波数", en: "Center frequency" },
    "pro.mode": { zh: "模式", ja: "モード", en: "Mode" },
    "pro.bandwidth": { zh: "带宽", ja: "帯域幅", en: "Bandwidth" },
    "pro.gain": { zh: "增益", ja: "ゲイン", en: "Gain" },
    "pro.refLevel": { zh: "参考电平", ja: "基準レベル", en: "Reference level" },
    "pro.chain": { zh: "解调链", ja: "復調チェーン", en: "Demod chain" },
    "pro.bypass": { zh: "直通", ja: "スルー", en: "Bypass" },
    "pro.specTag": { zh: "频谱 · 瀑布", ja: "スペクトラム · ウォーターフォール", en: "Spectrum · Waterfall" },
    "pro.rms": { zh: "RMS", ja: "RMS", en: "RMS" },
    "pro.cfo": { zh: "CFO", ja: "CFO", en: "CFO" },
    "pro.symRate": { zh: "符号率", ja: "シンボルレート", en: "Symbol rate" },
    "pro.chatHead": { zh: "模型协作栏", ja: "モデル連携パネル", en: "Model Collaboration" },
    "pro.chatSub": { zh: "与星象仪模式同一能力核心", ja: "プラネタリウムモードと同じ能力コア", en: "Same capability core as Planetarium Mode" },
    "pro.placeholder": { zh: "对模型说话…（能力协商 / 下达任务）", ja: "モデルに話しかける…（能力確認 / タスク指示）", en: "Talk to the model… (capability check / task)" },
    "pro.send": { zh: "发送", ja: "送信", en: "Send" },
    "pro.ready": { zh: "专业模式就绪。ControlHub 未连接，频谱与解调均为诚实空态；接入后工具面由内核 tools/list 提供。写操作需人工确认（arm_manual）。你需要模型做什么？", ja: "プロフェッショナルモード準備完了。ControlHub 未接続のため、スペクトラム・復調は正直な空状態です。接続後は内核の tools/list がツール面を提供します。書き込みは人工承認（arm_manual）が必要です。何を依頼しますか？", en: "Professional Mode ready. ControlHub is not connected, so spectrum and demodulation show an honest empty state; once connected the kernel tools/list supplies the tool surface. Writes require human confirmation (arm_manual). What should the model do?" },
    "pro.noGimbal": { zh: "无云台", ja: "雲台なし", en: "No gimbal" },
    "pro.reply": { zh: "明白。这条能力我可以直接操作（频谱/调谐/解调等皆为工具调用，非固定流程）。需要我先确认现场设备清单，还是直接装配任务？", ja: "了解しました。この能力は直接操作できます（スペクトラム/同調/復調はいずれもツール呼び出しで、固定手順ではありません）。まず現場の機器一覧を確認しますか、それともタスクを組み立てますか？", en: "Understood. I can operate this capability directly (spectrum/tuning/demodulation are tool calls, not a fixed workflow). Shall I confirm the on-site equipment list first, or assemble the task now?" },
    "status.connected": { zh: "● ControlHub 已连接（回环）", ja: "● ControlHub 接続済み（ループバック）", en: "● ControlHub connected (loopback)" },
    "status.demod": { zh: "内核未连接 · 无实时解调", ja: "内核未接続 · リアルタイム復調なし", en: "Kernel offline · no live demodulation" },
    "status.rs": { zh: "真录金样口径见文档（非实时）", ja: "真録ゴールデンの口径はドキュメント参照（非リアルタイム）", en: "See docs for recorded golden results (not live)" },
    "status.cpu": { zh: "CPU", ja: "CPU", en: "CPU" },
    "status.caps": { zh: "能力矩阵（按工具域）", ja: "能力マトリクス（ツールドメイン別）", en: "Capability matrix (by tool domain)" },

    /* ---- 工具目录 ---- */
    "tool.pass": { zh: "过境预报", ja: "パス予報", en: "Pass prediction" },
    "tool.tune": { zh: "调谐与采样", ja: "同調・サンプリング", en: "Tune & capture" },
    "tool.point": { zh: "云台指向", ja: "雲台指向", en: "Gimbal pointing" },
    "tool.demod": { zh: "解调", ja: "復調", en: "Demodulation" },
    "tool.image": { zh: "图像解码", ja: "画像デコード", en: "Image decoding" },
    "tool.inspect": { zh: "能力盘点", ja: "能力スキャン", en: "Capability scan" },
    "tool.enabled": { zh: "启用", ja: "有効", en: "Enabled" },
    "tool.degraded": { zh: "降级", ja: "縮退", en: "Degraded" },
    "tool.bandLimited": { zh: "频段受限", ja: "帯域制限", en: "Band limited" },
    "tool.external": { zh: "需外置", ja: "外付け必要", en: "External required" },
    "tool.noDevice": { zh: "无设备", ja: "機器なし", en: "No device" },
    "tool.offline": { zh: "离线 · 未伪装成功", ja: "オフライン · 成功を偽装しない", en: "Offline · no fake success" },

    /* ---- 桥接状态（诚实回执） ---- */
    "bridge.offline": { zh: "离线 · 未连接 ControlHub（无真实 IQ）", ja: "オフライン · ControlHub 未接続（実 IQ なし）", en: "Offline · no ControlHub (no real IQ)" },
    "bridge.online": { zh: "ControlHub 回环通道已连接", ja: "ControlHub ループバック接続済み", en: "ControlHub loopback connected" },
    "bridge.offlineErr": { zh: "not_connected：工具暂不可用（不伪装成功）", ja: "not_connected：ツールは利用不可（成功を偽装しない）", en: "not_connected: tool unavailable (no fake success)" },
    "bridge.tleMissing": { zh: "无 TLE 数据，轨道与过境结果不可信", ja: "TLE データなし：軌道・パス結果は信頼できません", en: "No TLE data: orbit and pass results are not trustworthy" },

    /* ---- 文档中心 ---- */
    "docs.open": { zh: "? 文档", ja: "? ドキュメント", en: "? Docs" },
    "docs.openTitle": { zh: "文档 / Wiki", ja: "ドキュメント / Wiki", en: "Docs / Wiki" },
    "docs.hint": { zh: "产品文档 · 能力百科", ja: "製品ドキュメント · 能力百科", en: "Product docs · capability wiki" },
    "docs.crumb": { zh: "文档中心", ja: "ドキュメントセンター", en: "Documentation" },
    "docs.group.spec": { zh: "产品与规格", ja: "製品・仕様", en: "Product & Spec" },
    "docs.group.arch": { zh: "工程与架构", ja: "エンジニアリング", en: "Engineering" },
    "docs.group.wiki": { zh: "能力百科", ja: "能力百科", en: "Capability Wiki" },
    "docs.group.scene": { zh: "场景", ja: "シナリオ", en: "Scenarios" },
    "docs.group.ref": { zh: "参考与术语", ja: "リファレンス", en: "Reference" },
    "docs.empty": { zh: "从左侧选择一份文档", ja: "左からドキュメントを選択してください", en: "Pick a document on the left" },
    "docs.close": { zh: "关闭", ja: "閉じる", en: "Close" },
    "docs.langHint": { zh: "文档语言", ja: "ドキュメント言語", en: "Doc language" },
    "docs.noTranslation": { zh: "本文暂无该语言译本，显示中文原文", ja: "本言語版は未翻訳のため、中国語原文を表示", en: "No translation available; showing the Chinese original" },

    /* ---- 模型协商：开场提问（能力而非流程） ---- */
    "dialog.open.receive": { zh: "收到——对 {name} 启动「接收」。我不预设链路方案，先确认现场能力：① 有 SDR 吗？什么型号、覆盖到哪个频段？② 有云台（天线转台）吗？③ 有 GNSS/授时模块吗？", ja: "了解しました — {name} の「受信」を開始します。固定のリンク構成は決めず、まず現場の能力を確認します：① SDR はありますか？型番と対応周波数は？② 雲台（アンテナ回転台）は？③ GNSS/時刻同期モジュールは？", en: "Understood — starting 'Receive' for {name}. I won't assume a fixed link plan; first, what's on site: ① An SDR, which model and coverage? ② A gimbal / rotator? ③ GNSS or a timing module?" },
    "dialog.open.track": { zh: "收到——「追踪」{name}。这需要闭环：测角/测频反馈 + 指向执行。我先问现场：① 有 SDR 吗？② 有云台吗？③ 有 GNSS 吗？有没有视觉（摄像头）可用于 6DOF 引导？", ja: "了解しました — {name} の「追跡」です。測角・測周波のフィードバックと指向実行が必要です。まず確認します：① SDR は？② 雲台は？③ GNSS は？6DOF 誘導に使えるカメラはありますか？", en: "Understood — 'Track' {name}. That needs a closed loop: angle/frequency feedback plus pointing actuation. First: ① an SDR? ② a gimbal? ③ GNSS, and is there a camera usable for 6DOF guidance?" },
    "dialog.open.point": { zh: "「指向」{name}：需要执行机构。① 有云台/转台吗？控制协议是什么？② 有 GNSS 给出站址与真北吗？", ja: "「指向」{name}：実行機構が必要です。① 雲台・回転台はありますか？制御プロトコルは？② 観測地点と真北を与える GNSS は？", en: "'Point' {name}: an actuator is required. ① A gimbal/rotator, and what control protocol? ② GNSS for site coordinates and true north?" },
    "dialog.open.calib": { zh: "「视觉校准」：相机-天线轴系标定目前是规划能力（未验证）。① 有相机吗？② 有云台吗？③ 是否要把它列入后续验证计划？", ja: "「視覚キャリブレーション」：カメラ・アンテナ軸較正は現状「計画・未検証」です。① カメラは？② 雲台は？③ 今後の検証計画に含めますか？", en: "'Visual calibration': camera–antenna axis alignment is currently planned / unverified. ① A camera? ② A gimbal? ③ Should it go on the later verification plan?" },
    "dialog.q.rtl": { zh: "有 SDR：RTL-SDR", ja: "SDR あり：RTL-SDR", en: "SDR: RTL-SDR" },
    "dialog.q.pluto": { zh: "有 SDR：ADALM-Pluto", ja: "SDR あり：ADALM-Pluto", en: "SDR: ADALM-Pluto" },
    "dialog.q.noSdr": { zh: "没有 SDR", ja: "SDR なし", en: "No SDR" },
    "dialog.q.gimbalYes": { zh: "有云台", ja: "雲台あり", en: "Gimbal available" },
    "dialog.q.gimbalNo": { zh: "没有云台", ja: "雲台なし", en: "No gimbal" },
    "dialog.q.gnssYes": { zh: "有 GNSS", ja: "GNSS あり", en: "GNSS available" },
    "dialog.q.gnssNo": { zh: "没有 GNSS", ja: "GNSS なし", en: "No GNSS" },
    "dialog.q.findRtl": { zh: "我再找台 RTL-SDR", ja: "別の RTL-SDR を探す", en: "I'll find an RTL-SDR" },
    "dialog.r.rtl": { zh: "好，RTL-SDR 覆盖 24–1766 MHz，{band} 在范围内。继续：有云台吗？没有的话我用「固定天线 + 预报过境」的降级方案。", ja: "RTL-SDR は 24–1766 MHz をカバーし、{band} は範囲内です。次に：雲台はありますか？無ければ「固定アンテナ + パス予報」に縮退します。", en: "Good — RTL-SDR covers 24–1766 MHz, so {band} is in range. Next: a gimbal? If not, I'll degrade to a fixed antenna plus predicted passes." },
    "dialog.r.pluto": { zh: "注意——PlutoSDR 基带 325 MHz 起，137 MHz 需要上变频器/前置混频，或者 VHF 用另一台机器。方案我会按此调整。云台情况？", ja: "注意 — PlutoSDR のベースバンドは 325 MHz からです。137 MHz にはアップコンバータ/前置ミキサ、あるいは別の VHF 受信機が必要です。構成を調整します。雲台は？", en: "Note — PlutoSDR baseband starts at 325 MHz; 137 MHz needs an upconverter/front-end mixer or a separate VHF receiver. I'll adjust the plan accordingly. What about the gimbal?" },
    "dialog.r.noSdr": { zh: "明白，暂无 SDR。那我把任务降级为「过境预报 + 指向/记录计划」，把可执行清单留好——等设备接入，我随时能把全链路拉起来。云台与 GNSS 呢？", ja: "承知しました、SDR は無しです。タスクを「パス予報 + 指向・記録計画」に縮退し、実行可能な手順を残します。機器が接続されれば全チェーンを即時に起動できます。雲台と GNSS は？", en: "Understood — no SDR. I'll degrade the task to pass prediction plus a pointing/recording plan and keep the executable checklist; once hardware is attached I can bring the full chain up. Gimbal and GNSS?" },
    "dialog.r.gimbalYes": { zh: "云台在位，追踪可以做闭环（az/el 随动 + 多普勒引导）。GNSS/授时有吗？它决定时间戳精度与站址标定方式。", ja: "雲台があるので追跡は閉ループにできます（az/el 追従 + ドップラー誘導）。GNSS/時刻同期はありますか？タイムスタンプ精度と観測地点の較正方式を左右します。", en: "With a gimbal, tracking can run closed-loop (az/el servo plus Doppler lead). Any GNSS/timing? It determines timestamp accuracy and how we calibrate the site." },
    "dialog.r.gimbalNo": { zh: "没有云台——那就「固定天线 + 高仰角过境窗口」方案，追踪降级为预报跟随。GNSS/授时呢？", ja: "雲台が無い場合は「固定アンテナ + 高仰角パス窓」とし、追跡は予報追従に縮退します。GNSS/時刻同期は？", en: "No gimbal — then it's a fixed antenna plus high-elevation pass windows, with tracking degraded to predicted following. GNSS/timing?" },
    "dialog.finish": { zh: "能力盘点完成：{parts}。我现在把 {name} 的任务装配成你现场的样子——不套模板。{plan}VR/XR 与 6DOF 视觉引导仍属规划未验证，不会当作已上线能力调用。", ja: "能力の棚卸し完了：{parts}。{name} のタスクを現場の構成に合わせて組み立てます（テンプレートなし）。{plan}VR/XR と 6DOF 視覚誘導は計画・未検証のままです。", en: "Capability inventory complete: {parts}. I'll assemble the {name} task for what is actually on site — no template. {plan}VR/XR and 6DOF visual guidance remain planned / unverified and will not be invoked as shipped capabilities." },
    "dialog.plan.full": { zh: "追踪闭环已拉起：多普勒引导 + az/el 随动。", ja: "追跡の閉ループを起動：ドップラー誘導 + az/el 追従。", en: "Tracking loop is up: Doppler lead plus az/el servo." },
    "dialog.plan.sdr": { zh: "先按过境窗口预置频率计划，信号进来我就解调。", ja: "まずパス窓に合わせて周波数計画をプリセットし、信号が入り次第復調します。", en: "I'll preset a frequency plan for the pass window and demodulate as soon as signal arrives." },
    "dialog.plan.none": { zh: "设备就位后随时唤醒全链路。", ja: "機器が揃えばいつでも全チェーンを起動できます。", en: "The full chain can be woken up as soon as hardware is in place." },
    "dialog.part.noSdr": { zh: "无 SDR（仅预报）", ja: "SDR なし（予報のみ）", en: "No SDR (prediction only)" },
    "dialog.part.gimbal": { zh: "云台闭环", ja: "雲台閉ループ", en: "Gimbal closed loop" },
    "dialog.part.fixed": { zh: "固定天线", ja: "固定アンテナ", en: "Fixed antenna" },
    "dialog.part.gnss": { zh: "GNSS 授时", ja: "GNSS 時刻同期", en: "GNSS timing" },
    "dialog.part.freerun": { zh: "自由时钟", ja: "フリーランクロック", en: "Free-running clock" },
    "dialog.generic": { zh: "明白。我可以直接操作的能力：调谐、解调、过境规划、指向、追踪闭环……都是工具调用，不是固定流程。先确认现场设备清单，还是直接装配任务？", ja: "了解しました。直接操作できる能力：同調、復調、パス計画、指向、追跡閉ループ……いずれもツール呼び出しで、固定手順ではありません。まず現場の機器一覧を確認しますか、それともタスクを組み立てますか？", en: "Understood. Capabilities I can operate directly: tuning, demodulation, pass planning, pointing, tracking loops — all tool calls, not a fixed workflow. Shall I confirm the on-site equipment list, or assemble the task now?" },
    /* 定轨：唯一入口 = 用户开口。UI 无任何按钮/能力位/工具卡。 */
    "dialog.orbit": { zh: "「定轨」收到。走多普勒观测 → RLS 估计（参考 IRIDIUM 107 观测数据集）。我不预设方案，先确认设备：① 有 SDR 做频率测量吗？什么频稳水平？② 有 GNSS 授时打时间戳吗？", ja: "「軌道決定」を受け付けました。ドップラー観測 → RLS 推定（IRIDIUM 107 観測データセット参照）で進めます。構成を決め打ちせず、まず機器を確認します：① 周波数測定用の SDR は？周波数安定度は？② タイムスタンプ用の GNSS 時刻同期は？", en: "'Orbit determination' received. We'll follow Doppler observation → RLS estimation (cf. the IRIDIUM 107 observation dataset). No preset plan — first: ① an SDR for frequency measurement, and what frequency stability? ② GNSS timing for timestamps?" },
    "dialog.continue": { zh: "（继续）", ja: "（続ける）", en: "(continue)" },

    /* ---- wiki 元数据：模型会问什么 / 验收口径（三语） ---- */
    "ask.sdr.model": { zh: "有 SDR 吗？型号与接口？", ja: "SDR はありますか？型番とインタフェースは？", en: "An SDR? Model and interface?" },
    "ask.sdr.band": { zh: "覆盖到目标频段吗？", ja: "目的の周波数帯をカバーしていますか？", en: "Does it cover the target band?" },
    "ask.gimbal.protocol": { zh: "云台控制协议是什么？", ja: "雲台の制御プロトコルは？", en: "What is the gimbal control protocol?" },
    "ask.gimbal.range": { zh: "方位/俯仰行程与限位？", ja: "方位・俯仰の可動範囲とリミットは？", en: "Az/el range and limits?" },
    "ask.gnss.pps": { zh: "有 1PPS 授时吗？", ja: "1PPS 時刻信号はありますか？", en: "Is 1PPS timing available?" },
    "ask.gnss.site": { zh: "站址坐标是否已校准？", ja: "観測地点座標は較正済みですか？", en: "Are site coordinates calibrated?" },
    "ask.vis.camera": { zh: "有可用相机及内参吗？", ja: "利用可能なカメラと内部パラメータは？", en: "A camera with intrinsics available?" },
    "ask.vis.calib": { zh: "相机-天线轴系标定过吗？", ja: "カメラ・アンテナ軸は較正済みですか？", en: "Is the camera–antenna axis calibrated?" },
    "ask.calib.axis": { zh: "两轴的安装关系如何？", ja: "二軸の取り付け関係は？", en: "How are the two axes mounted?" },
    "ask.calib.reference": { zh: "有无可观测的标定参考？", ja: "較正用の観測可能な基準はありますか？", en: "Any observable calibration reference?" },
    "ask.vr.headset": { zh: "接入的头显与运行时？", ja: "接続する HMD とランタイムは？", en: "Which headset and runtime?" },
    "ask.vr.frame": { zh: "刷新率与坐标基准？", ja: "リフレッシュレートと座標基準は？", en: "Refresh rate and coordinate frame?" },
    "ask.spec.rbw": { zh: "RBW / 窗函数怎么设？", ja: "RBW / 窓関数の設定は？", en: "RBW / window function?" },
    "ask.spec.span": { zh: "扫宽与瀑布时间跨度？", ja: "スパンとウォーターフォール時間幅は？", en: "Span and waterfall time depth?" },
    "ask.stack.scheme": { zh: "目标体制与符号率？", ja: "対象の変調方式とシンボルレートは？", en: "Target scheme and symbol rate?" },
    "ask.stack.rate": { zh: "软判决还是硬判决？", ja: "軟判定・硬判定のどちら？", en: "Soft or hard decisions?" },
    "ask.point.north": { zh: "真北基准从哪来？", ja: "真北の基準はどこから得ますか？", en: "Where does true north come from?" },
    "ask.point.limits": { zh: "机械限位与电缆缠绕？", ja: "機械リミットとケーブル巻き付きは？", en: "Mechanical limits and cable wrap?" },
    "capture.verify": { zh: "采样文件可回放，且带完整时间戳与中心频率元数据", ja: "収録ファイルが再生可能で、タイムスタンプと中心周波数のメタデータが揃っていること", en: "Capture replayable with complete timestamps and center-frequency metadata" },
    "gimbal.verify": { zh: "给定 az/el 后实测回读误差在容差内", ja: "指定 az/el に対する実測読み戻し誤差が許容範囲内", en: "Commanded az/el matches readback within tolerance" },
    "gnss.verify": { zh: "1PPS 与采样时钟偏差可量化并留痕", ja: "1PPS とサンプリングクロックの偏差が定量化・記録されていること", en: "1PPS vs sample-clock offset quantified and logged" },
    "vis6dof.verify": { zh: "位姿估计残差可复现，并附输入帧", ja: "姿勢推定残差が再現可能で入力フレームが添付されていること", en: "Pose residuals reproducible with input frames attached" },
    "calib.verify": { zh: "标定参数可导出并在另一时段复验", ja: "較正パラメータをエクスポートでき、別時刻に再検証できること", en: "Calibration exportable and re-verifiable at another epoch" },
    "vrxr.verify": { zh: "帧率与坐标基准在头显端核对通过", ja: "HMD 側でフレームレートと座標基準を確認済み", en: "Frame rate and coordinate frame verified on the headset" },
    "spec.verify": { zh: "频谱与已知信号金样对拍无系统性偏差", ja: "既知信号のゴールデンに対しスペクトラムに系統誤差がないこと", en: "Spectrum matches known-signal golden without systematic bias" },
    "stack.verify": { zh: "解出的帧过 ASM/RS 校验并留金样", ja: "復号フレームが ASM/RS 検証を通りゴールデンとして保存されること", en: "Decoded frames pass ASM/RS checks and are kept as goldens" },
    /* ---- 原项目事实对齐（2026-11-04） ---- */
    "caps.empty": { zh: "该面无工具（诚实空态）", ja: "この面にはツールがありません（正直な空状態）", en: "No tools on this surface (honest empty state)" },
    "caps.notVerified": { zh: "规划 · 未验证", ja: "計画 · 未検証", en: "Planned · unverified" },
    "caps.pythonExt": { zh: "Python 扩展域", ja: "Python 拡張ドメイン", en: "Python extension" },
    "status.disconnected": { zh: "● 未连接 SDR（诚实空态）", ja: "● SDR 未接続（正直な空状態）", en: "● No SDR connected (honest empty state)" },
    "status.noDecode": { zh: "内核离线 · 无实时解调", ja: "内核オフライン · リアルタイム復調なし", en: "Kernel offline · no live demodulation" },
    "status.liveHint": { zh: "工具回执以 ControlHub 为准", ja: "ツール応答は ControlHub に準拠", en: "Tool receipts follow ControlHub" },
    "spec.empty": { zh: "未连接 SDR", ja: "SDR 未接続", en: "No SDR connected" },
    "spec.emptyHint": { zh: "无 IQ 输入时只显示网格 · 不合成数据", ja: "IQ 入力なしの場合はグリッドのみ · データを合成しません", en: "Grid only without IQ input · no synthetic data" },
    "spec.noIq": { zh: "内核无频谱回执", ja: "内核からスペクトラム応答なし", en: "No spectrum receipt from kernel" },
    "spec.noIqHint": { zh: "read_spectrum 未返回真实数据 · 显示空态", ja: "read_spectrum が実データを返していません · 空状態を表示", en: "read_spectrum returned no real data · showing empty state" },
    "bridge.gated": { zh: "人工门未确认（gated · 非失败）", ja: "人工ゲート未確認（gated · 失敗ではない）", en: "Human gate not armed (gated · not a failure)" },
    "tool.devices": { zh: "枚举设备", ja: "デバイス列挙", en: "List devices" },
    "tool.connect": { zh: "连接设备", ja: "デバイス接続", en: "Connect device" },
    "tool.spectrum": { zh: "读取频谱", ja: "スペクトラム読取", en: "Read spectrum" },
    "tool.record": { zh: "开始录制", ja: "録音開始", en: "Start recording" },
    "tool.status": { zh: "整体状态", ja: "全体ステータス", en: "System status" },
    "tool.satlink": { zh: "下行频率反查", ja: "ダウンリンク周波数照会", en: "Downlink lookup" },
    "pro.demoParams": { zh: "演示参数 · 非实时读数", ja: "デモパラメータ · 非リアルタイム", en: "Demo parameters · not live readings" },
    "field.tleEpoch": { zh: "TLE 历元", ja: "TLE 元期", en: "TLE epoch" },
    "field.dataPolicy": { zh: "数据口径", ja: "データ口径", en: "Data policy" },
    "field.demo": { zh: "演示目录 · 非实时测量", ja: "デモカタログ · 非リアルタイム", en: "Demo catalog · not live measurement" },
    "sat.catalog.desc": { zh: "内置离线演示目录条目。下行频率与 TLE 历元来自原仓库 sat_tracker.py；合成/凑整 TLE 禁止用于精确过境，站址未校准时不给可信结论。", ja: "オフラインデモ用の内蔵カタログ項目。ダウンリンク周波数と TLE 元期は元リポジトリ sat_tracker.py に由来。合成 TLE は精密なパス予報に使用禁止。", en: "Built-in offline demo catalog entry. Downlink and TLE epoch come from the repository sat_tracker.py; synthetic TLE is not usable for precise passes." },
    "point.verify": { zh: "预置角度与实测指向差在波束宽度内", ja: "プリセット角と実測指向の差がビーム幅内", en: "Preset vs measured pointing error within beamwidth" },
    /* ---- 星象仪（时空主导）：真实全天星表 + 球面投影 ---- */
    "sky.galaxyNone": { zh: "银河图层 · 无数据·不提供（不画装饰光带）", ja: "銀河レイヤ · データなし（装飾帯を描きません）", en: "Milky Way layer · no data · not provided" },
    "sky.dayHidden": { zh: "当前模拟时刻为白天 · 恒星整层消隐", ja: "模擬時刻は昼間 · 恒星レイヤ非表示", en: "Simulated daytime · star layer hidden" },
    "sky.dayHint": { zh: "点底栏 ⏱ 快转时间，或用搜索框选定观测时刻", ja: "⏱ で時刻を早送り、または観測時刻を設定", en: "Use the time control to advance to night" },
    "sky.landscapeSketch": { zh: "地景：示意轮廓（仓库无实时全景数据，不冒充实拍）", ja: "地景：概略輪郭（実写パノラマ無し・実写の代替にしない）", en: "Landscape: sketch outline (no real panorama data, not presented as imagery)" },
    "hud.demoStart": { zh: "演示起始时刻：站址当地夜间（观测条件，非观测数据）", ja: "デモ開始時刻：現地夜間（観測条件、観測データではありません）", en: "Demo start time: local night (observation condition, not observed data)" },
    "tb.milkyway": { zh: "银河（无数据·不提供）", ja: "銀河（データなし·非提供）", en: "Milky Way (no data)" },
    "tb.constellations": { zh: "星座连线", ja: "星座線", en: "Constellation lines" },
    "tb.dso": { zh: "DSO 类型符号", ja: "DSO 種別記号", en: "DSO type symbols" },
    "tb.azgrid": { zh: "方位网格", ja: "方位グリッド", en: "Alt-azimuth grid" },
    "tb.eqgrid": { zh: "赤道网格", ja: "赤道グリッド", en: "Equatorial grid" },
    "tb.hud": { zh: "时空读数", ja: "時空読数", en: "Spacetime readouts" },
    "hud.spacetime": { zh: "星象仪模式 · 时空主导", ja: "星象儀モード · 時空主導", en: "Planetarium · spacetime-first" },
    "hud.utc": { zh: "模拟时刻", ja: "模擬時刻", en: "Sim UTC" },
    "hud.jd": { zh: "儒略日", ja: "ユリウス日", en: "JD" },
    "hud.lst": { zh: "地方恒星时", ja: "地方恒星時", en: "LST" },
    "hud.fov": { zh: "视场角", ja: "視野角", en: "FOV" },
    "hud.view": { zh: "视轴", ja: "視軸", en: "View axis" },
    "hud.limit": { zh: "显示门限", ja: "表示限界", en: "Display limit" },
    "hud.visible": { zh: "视场内可见", ja: "視野内の可視星", en: "Visible in FOV" },
    "hud.mag": { zh: "等", ja: "等", en: "mag" },
    "hud.projection": { zh: "投影", ja: "投影", en: "Projection" },
    "hud.sunAlt": { zh: "太阳高度", ja: "太陽高度", en: "Sun altitude" },
    "hud.catalog": { zh: "全天星表", ja: "全天星表", en: "All-sky catalog" },
    "hud.magCap": { zh: "最深", ja: "最深度", en: "deepest" },
    "hud.timeSource": { zh: "时间源", ja: "時刻源", en: "Time source" },
    "hud.kernelLive": { zh: "已接内核 TimeEngine", ja: "TimeEngine 接続済み", en: "TimeEngine attached" },
    "hud.kernelOffline": { zh: "本地模拟时刻 · 未接内核 TimeEngine", ja: "ローカル模擬時刻 · TimeEngine 未接続", en: "Local sim time · TimeEngine not attached" },
    "hud.limitNote": { zh: "显示门限是视觉适应近似（设计项 · 未验证），非望远镜口径增益", ja: "表示限界は視覚適応の近似（設計·未検証）、望遠鏡の口径利得ではありません", en: "Display limit is a visual-adaptation approximation (design · unverified), not aperture gain" },
  };

  const LOCALES = ["zh-CN", "ja-JP", "en-US"];
  const FIELD = { "zh-CN": "zh", "ja-JP": "ja", "en-US": "en" };
  const norm = s => String(s).replace(/\s+/g, " ").trim();

  /* 反查表：静态 HTML 的中文原文 → key */
  const LOOKUP = Object.create(null);
  for (const key of Object.keys(D)) LOOKUP[norm(D[key].zh)] = key;

  const i18n = MB.i18n = {
    locales: LOCALES,
    locale: LOCALES.includes(S.locale) ? S.locale : "zh-CN",
    dict: D,
    lookup: LOOKUP,
    has: key => Object.prototype.hasOwnProperty.call(D, key),
    keys: () => Object.keys(D),
    t(key, vars) {
      const e = D[key];
      if (!e) { console.warn("[i18n] missing key:", key); return key; }
      let out = e[FIELD[this.locale]] || e.zh;
      if (vars) for (const k of Object.keys(vars)) out = out.replace(new RegExp("\\{" + k + "\\}", "g"), vars[k]);
      return out;
    },
    /* 定轨：只认用户说出口的话，不认任何 UI 控件 */
    isOrbitRequest(text) {
      return /定轨|轨道确定|轨道改进|軌道決定|軌道推定|orbit determination|orbit estimate|\borbit\b/i.test(text || "");
    },
    label(locale) { return { "zh-CN": "中文", "ja-JP": "日本語", "en-US": "English" }[locale] || locale; },
    setLocale(locale, persist) {
      if (!LOCALES.includes(locale)) return;
      this.locale = locale;
      S.locale = locale;
      if (persist !== false) MB.persist("locale");
      document.documentElement.lang = locale;
      this.applyStatic();
      MB.emit("locale:changed", locale);
    },
    /* 静态节点扫描：保存原文到 _i18nSrc，可反复切换 */
    applyStatic() {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (const node of nodes) {
        const parent = node.parentElement;
        if (!parent || parent.closest("[data-i18n-skip]")) continue;
        if (parent.closest("#docBody")) continue;        // 文档正文按文档语言单独渲染
        if (node._i18nSrc === undefined) node._i18nSrc = node.nodeValue;
        const raw = norm(node._i18nSrc);
        if (!raw) continue;
        const key = LOOKUP[raw];
        if (!key) {
          if (/[\u4e00-\u9fff]/.test(raw)) (this.missing || (this.missing = new Set())).add(raw);
          continue;
        }
        const lead = /^\s/.test(node._i18nSrc) ? " " : "";
        const trail = /\s$/.test(node._i18nSrc) ? " " : "";
        node.nodeValue = lead + this.t(key) + trail;
      }
      for (const el of document.querySelectorAll("[title],[placeholder],[aria-label]")) {
        if (el.closest("[data-i18n-skip], #docBody")) continue;
        for (const attr of ["title", "placeholder", "aria-label"]) {
          const src = el.getAttribute(attr);
          if (!src) continue;
          const cache = "_i18n_" + attr;
          if (el[cache] === undefined) el[cache] = src;
          const key = LOOKUP[norm(el[cache])];
          if (key) el.setAttribute(attr, this.t(key));
        }
      }
      document.title = this.t("app.title");
    },
    /* 动态节点的显式绑定：在事件里刷新时用 */
    refreshDynamic() { MB.emit("i18n:refresh"); }
  };

  S.locale = i18n.locale;
})(window);
