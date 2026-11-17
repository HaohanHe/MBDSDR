# 多文化天空包（`mbdsdr_ai/data/skycultures/`）

统一模型 `mbdsdr_ai/skyengine/sky_culture.py` 把 IAU 88 星座、中国星官与
Stellarium 的其他天空文化收进**同一套可插拔文化包**：每个包只声明
`groups`（星座/星群/星官 + HIP 折线）与 `names`（HIP → 该文化下的名称）。
渲染侧仍按包 id 分层绘制，不新增第三套渲染；查询侧由
`sky_culture_lookup` 工具按文化逐条返回，**绝不把不同文化的名称合并**。

## 1. 数据范围与许可边界

- **只转换结构化连线**：`constellations[].lines`、`id`、`common_name`，
  以及源数据里存在的 `common_names` 星名。插图、artwork、description
  正文与图片**一律不复制**（见 `scripts/build_sky_cultures.py` 的
  `conversion_scope`）。
- **派生数据按原许可分发**：每个包的 `meta.source_license` 保留上游许可
  （CC BY-SA 4.0 / CC BY 4.0 / CC0），`meta.attribution` 保留作者署名，
  `meta.source_url` 指回上游目录。转换产物不是 MIT 授权——它继续受原
  许可约束（如 CC BY-SA 4.0 的相同方式共享）。
- **许可白名单**（`ALLOWED_LICENSES`）：只接受 `CC BY-SA 4.0`、
  `CC BY 4.0`、`CC0 / Public Domain`；CC BY-ND、CC BY-NC*、GPL 在生成时
  拒绝，加载时还会再校验一次。
- **作用域限定词只剥离、不放宽**：上游许可行写 `Text and data: CC BY-SA 4.0`
  时（该文化的插图另有许可），生成与加载都先用同一规则剥离 `Text and data:`
  前缀（大小写/空白容错）得到规范许可 `CC BY-SA 4.0` 再判白名单；原始串
  保留在 `meta.source_license_raw`，`meta.source_license` 只放规范值，
  两者剥离后必须一致，否则拒绝加载。白名单本身不变——`CC BY-SA 2.0` /
  `3.0` / 无版本号剥离后仍不匹配，照样拒绝。
- **不猜测、不丢弃**：HIP 不在随仓星表时记入 `unresolved_hips`；非 HIP
  标识（`DSO:*`、Gaia 源 id 等）记入 `unresolved_tokens`，并断开折线，
  不冒充 HIP。每个包带 `data_sha256`，加载时校验，手改数据会直接报错。
- 没有显式星名的文化，其成员星继承所在组/星群的名称（源数据自带的分组
  名），不是凭记忆补写。

## 2. 当前收录的 30 个包

统计由 `sky_culture.stats()` 实取（groups/edges/names 为去重后的口径，
`unresolved` 是该包中不在本地星表的 HIP 数）。第二批 8 个、第三批 6 个
包均为非中国主体文化；第四批 6 个为 `modern_rey` 与 5 个中国历史变体；
第五批 3 个为许可行带 `Text and data:` 限定词的 `belarusian` /
`modern` / `modern_chinese`。五批全部走同一注册表与同一渲染分层，未新增
渲染代码。

| id | 显示名 | 来源 | 许可 | groups | edges | names | unresolved HIP |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| `iau_88` | IAU 88 Constellations / IAU 88 星座 | Stellarium `modern_iau`（适配器读取 `constellations_iau.json`） | CC BY-SA 4.0 | 88 | 755 | 721 | 1 |
| `chinese_xingguan` | Chinese Xingguan / 中国星官（三垣二十八宿） | Stellarium `chinese`（适配器读取 `xingguan_chinese.json`） | CC BY-SA 4.0 | 31 | 1012 | 2910 | 3 |
| `japanese` | Japanese Yasui Map | Stellarium `japanese` | CC BY-SA 4.0 | 373 | 1368 | 1621 | 3 |
| `indian` | Indian Vedic | Stellarium `indian` | CC BY-SA 4.0 | 49 | 281 | 285 | 1 |
| `indian_nakshatras` | Indian (Nakshatras) | Stellarium `indian_nakshatras` | CC BY 4.0 | 28 | 109 | 124 | 0 |
| `norse` | Norse | Stellarium `norse` | CC BY-SA 4.0 | 6 | 32 | 37 | 0 |
| `seri` | Seri | Stellarium `seri` | CC BY-SA 4.0 | 15 | 52 | 63 | 1 |
| `maori` | Maori / 毛利 | Stellarium `maori` | CC BY-SA 4.0 | 6 | 20 | 46 | 0 |
| `mongolian` | Mongolian / 蒙古 | Stellarium `mongolian` | CC BY-SA 4.0 | 4 | 23 | 26 | 0 |
| `sami` | Sami / 萨米 | Stellarium `sami` | CC BY-SA 4.0 | 10 | 29 | 33 | 0 |
| `hawaiian_starlines` | Hawaiian Starlines / 夏威夷星线 | Stellarium `hawaiian_starlines` | CC BY-SA 4.0 | 13 | 82 | 88 | 0 |
| `tukano` | Tukano / 图卡诺 | Stellarium `tukano` | CC BY-SA 4.0 | 11 | 187 | 186 | 0 |
| `boorong` | Boorong / 布龙 | Stellarium `boorong` | CC BY-SA 4.0 | 29 | 207 | 226 | 0 |
| `russian_siberian` | Russian (Siberian) / 俄罗斯（西伯利亚） | Stellarium `russian_siberian` | CC BY-SA 4.0 | 3 | 19 | 21 | 0 |
| `norse_edda` | Norse (Edda) / 北欧（埃达） | Stellarium `norse_edda` | CC BY 4.0 | 19 | 114 | 117 | 0 |
| `anutan` | Anutan | Stellarium `anutan` | CC BY-SA 4.0 | 11 | 49 | 56 | 0 |
| `samoan` | Samoan | Stellarium `samoan` | CC BY-SA 4.0 | 14 | 60 | 68 | 0 |
| `tongan` | Tongan | Stellarium `tongan` | CC BY-SA 4.0 | 11 | 32 | 50 | 0 |
| `vanuatu_netwar` | Vanuatu (Netwar) | Stellarium `vanuatu_netwar` | CC BY-SA 4.0 | 10 | 28 | 31 | 0 |
| `tikuna` | Tikuna | Stellarium `tikuna` | CC BY-SA 4.0 | 4 | 26 | 31 | 0 |
| `ruanui_sky_tahiti_and_society_islands` | Ruanui sky: Tahiti and Society islands | Stellarium `ruanui_sky_tahiti_and_society_islands` | CC BY-SA 4.0 | 19 | 133 | 145 | 0 |
| `chinese_song_dynasty` | Chinese Song Dynasty Sky | Stellarium `chinese_song_dynasty` | CC BY-SA 4.0 | 339 | 1245 | 1460 | 2 |
| `chinese_yuan_dynasty` | Chinese Yuan Dynasty (14th Century) | Stellarium `chinese_yuan_dynasty` | CC BY-SA 4.0 | 187 | 579 | 690 | 2 |
| `chinese_manchu` | Chinese Manchu | Stellarium `chinese_manchu` | CC BY-SA 4.0 | 24 | 278 | 271 | 1 |
| `chinese_xianglin` | Chinese Xianglin Star Chart | Stellarium `chinese_xianglin` | CC BY-SA 4.0 | 317 | 1211 | 1408 | 2 |
| `chinese_chenzhuo` | Chinese Chenzhuo Sky (3rd Century) | Stellarium `chinese_chenzhuo` | CC BY-SA 4.0 | 317 | 1282 | 1462 | 3 |
| `modern_rey` | Modern (H.A. Rey) | Stellarium `modern_rey` | CC BY-SA 4.0 | 170 | 877 | 848 | 2 |
| `belarusian` | Belarusian | Stellarium `belarusian` | CC BY-SA 4.0 | 20 | 110 | 114 | 0 |
| `modern` | Modern | Stellarium `modern` | CC BY-SA 4.0 | 88 | 695 | 1037 | 0 |
| `modern_chinese` | Modern (Chinese) | Stellarium `modern_chinese` | CC BY-SA 4.0 | 88 | 674 | 3117 | 0 |

合计：**30 包 / 2304 groups / 11569 edges / 17292 names**；跨包去重后
unresolved HIP 共 **6** 个（`1411`、`33646`、`54483`、`55203`、`78727`、
`115125`）。第二批 8 个与第三批 6 个包没有 unresolved HIP，也没有非 HIP
标识（`unresolved_tokens` 为空），单包体积 5.5–33.3 KB（第三批
7.9–24.6 KB）。第四批 6 个包单包体积 61.5–359.7 KB；其中 5 个中国历史
变体含有 `DSO:M31` / `DSO:M44` / `DSO:M7` 等非 HIP 标识，转换时按约定
断开折线并记入 `unresolved_tokens`，未冒充 HIP。第五批 3 个包为
18.3–223.8 KB，无 unresolved HIP、无非 HIP 标识。全部包仍远低于 2 MB 上限。

上游目录：

- <https://github.com/Stellarium/stellarium/tree/master/skycultures/modern_iau>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/chinese>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/japanese>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/indian>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/indian_nakshatras>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/norse>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/seri>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/maori>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/mongolian>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/sami>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/hawaiian_starlines>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/tukano>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/boorong>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/russian_siberian>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/norse_edda>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/anutan>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/samoan>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/tongan>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/vanuatu_netwar>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/tikuna>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/ruanui_sky_tahiti_and_society_islands>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/chinese_song_dynasty>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/chinese_yuan_dynasty>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/chinese_manchu>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/chinese_xianglin>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/chinese_chenzhuo>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/modern_rey>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/belarusian>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/modern>
- <https://github.com/Stellarium/stellarium/tree/master/skycultures/modern_chinese>

## 3. 不可收文化及原因

白名单是**剥离作用域限定词后的精确匹配**，与
`scripts/build_sky_cultures.py` 的 `license_decision()` 一致：先剥掉
`Text and data:` 这类前缀，再要求结果落在 `ALLOWED_LICENSES`，否则一律拒绝、
不生成文件。下表按 `reference/stellarium/skycultures/*/description.md`
的 `## License` 小节归类（同一文化只列一次）。

### 3.1 禁止演绎（ND）

`arabic_al-sufi`、`babylonian_mulapin`、`babylonian_seleucid`、`balinese`、
`egyptian_dendera`、`greek_almagest`、`greek_dante`、`greek_farnese`、
`greek_leidenAratea`、`khoi-san`、`modern_sternenkarten`、`xhosa`、`zulu`。

原因：CC BY-ND 不允许分发**改写后的**连线数据；本仓库的转换本身即派生
作品，故不能收录。

### 3.2 禁止商用（NC）

- `maya`（CC BY-NC-SA 4.0）— 本轮 `DEFAULT_CULTURES` 中唯一被拒绝的文化；
- `potawatomi`（`License (artwork): CC BY-NC-SA 4.0`）。

原因：NC 与「随 MIT 仓库公开分发、可被任意再使用」不兼容。

### 3.3 禁止商用 + 禁止演绎（NC-ND）

`arabic_arabian_peninsula`、`arabic_indigenous`、`kamilaroi`、`lokono`、
`modern_journey_to_the_west`。

### 3.4 GNU GPL

`aztec`、`egyptian`、`inuit`、`korean`、`macedonian`、`modern_hlad`、
`navajo`、`northern_andes`、`sardinian`、`tupi`。

原因：GPL 数据不能进入 MIT 主库（见 `NOTICE.md` 与
`scripts/check_data_licenses.py`；B1 决策后豁免表为空）。

### 3.5 许可版本/写法不在白名单

`armintxe`（description 缺 `## License`）、`modern_st`（CC BY-SA 2.0）、
`romanian`（CC BY-SA 3.0 Unported）、`tibetan`（`CC BY-SA`，无版本号）。

这些不是 ND/NC/GPL，而是**许可版本**本身不在白名单；
按现有规则同样拒绝——宁可少收一个文化，也不擅自把 3.0/2.0 当成 4.0。
带 `Text and data:` 前缀的三项已按作用域限定词处理，见下段，不再属于
“写法不匹配”。

> 以上目录都留在 `reference/stellarium/`，未进入 `mbdsdr_ai/data/`；
> 即使手工 `--cultures` 指定，生成脚本也会以 `REFUSED` 列出并退出，
> 不会产出文件。

本节「不可收」清单**仍然有效**：第二、三、四、五批增补只从许可白名单内
挑选，上表的 ND / NC / NC-ND / GPL / 版本不匹配文化一个都没有进入
`mbdsdr_ai/data/skycultures/`。

第五批的 `belarusian`、`modern`、`modern_chinese` 许可行写的是
`Text and data: CC BY-SA 4.0`（插图另有 Free Art License）。本仓库只转换
连线数据、不复制 artwork，故数据部分适用 CC BY-SA 4.0：生成时剥离
`Text and data:` 得到规范值并写入 `meta.source_license`，原始串保留在
`meta.source_license_raw`。这**不是**放宽白名单——`romanian`（3.0）、
`modern_st`（2.0）、`tibetan`（无版本号）剥离后仍不匹配，继续拒绝。

第四批的 `chinese_song_dynasty`、`chinese_yuan_dynasty`、`chinese_manchu`、
`chinese_xianglin`、`chinese_chenzhuo`、`modern_rey` 在各自
`description.md` 的 `## License` 下均为精确的 `CC BY-SA 4.0`，因此收录。
至此白名单内只剩 `chinese` 与 `modern_iau` 两个上游目录未生成独立文件包：
它们分别由 `chinese_xingguan` / `iau_88` 适配器直接读取本仓既有数据覆盖，
再生成文件包会与适配器 id 重复，故保持现状。

## 4. 复跑与校验

```bash
python scripts/build_sky_cultures.py            # 从本机 Stellarium 重新生成
python -m pytest tests/test_sky_cultures.py -q  # 包加载 / SHA / 许可拒绝
python -m pytest tests/test_sky_culture_layers.py -q  # 渲染分层接入
python -m pytest tests/test_all_cultures_render.py -q  # 全包机械渲染验收
python -m pytest tests/test_sky_culture_lookup.py -q  # 工具面查询
python scripts/check_data_licenses.py           # GPL 数据禁入
python scripts/tool_surface_audit.py            # 工具面登记审计
```

新增文化包时，`meta` 必须带齐 `schema`、`source`、`source_url`、
`source_license`、`source_license_url`、`attribution`、
`source_file_sha256`、`data_sha256`；缺一项即拒绝加载。许可行带作用域
限定词时，生成脚本另写 `source_license_raw` 保留原始串（可选字段；一旦
出现，剥离限定词后必须等于 `source_license`，否则拒绝加载）。
