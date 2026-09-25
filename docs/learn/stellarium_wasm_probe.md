# stellarium-web-engine WASM 构建可行性探测报告

- 探测日期：2026-09-25
- 探测环境：云 VM（Linux，无 sudo，系统目录只读）
- 仓库：`stellarium-web-engine/`（上游 https://github.com/Stellarium/stellarium-web-engine.git，commit 2987074）
- 结论先行：**可行。** 在本云环境内成功产出 `stellarium-web-engine.wasm`（1.2 MB）+ JS 胶水（76 KB）。
  但需要对上游 `SConstruct` 打 3 处小补丁（新版 emscripten 兼容性），并用 `werror=0` 构建。

---

## 1. 环境信息

| 项目 | 结果 |
|---|---|
| OS | Ubuntu 22.04.5 LTS (Jammy)，x86_64，kernel 6.6 |
| CPU / 内存 | 4 核，7.9 GiB RAM，无 swap |
| 磁盘 `~` | 挂载于 10P 网络盘，已用 419G，**空间充足** |
| 磁盘 `/` 和 `/tmp` | overlay 9.8G，已用 78M，可用 9.7G |
| 已有编译器 | gcc/g++ 11.4.0，make 4.3，cmake 3.22.1，git 2.34.1 |
| 已有 Python | /opt/python3.12/bin/python3（3.12） |
| Emscripten（探测前） | **未安装**（`which emcc` 无结果） |
| qmake / qmake6 | **不存在**（但本项目不需要 Qt，见下） |

### 网络可达性

| 主机 | 结果 |
|---|---|
| https://github.com | `HTTP/2 200` 可达 |
| https://codeload.github.com（emsdk tar 包） | `HTTP/2 200` 可达 |
| https://storage.googleapis.com（emsdk 实际下载源） | 可达（根路径 400 属正常，连接正常） |
| https://api.github.com | `HTTP/2 403`（限流，不影响 emsdk 直接下载） |
| https://naif.jpl.nasa.gov | **超时/无响应**（本项目不需要，NAIF 数据未在构建期拉取） |

---

## 2. 项目真实构建方式（与任务初始假设不同，重要更正）

任务初始描述假设这是 "C++/Qt 项目，用 CMake 构建为 WASM"。**实际探测后更正：**

- 顶层**没有 CMakeLists.txt**，也不依赖 Qt。
- 构建系统是 **SCons + emscons**：
  - `Makefile` 的 `js` 目标 = `emscons scons -j8 mode=release`
  - 构建脚本为根目录 `SConstruct`
- 所有 C/C++ 第三方依赖都 **vendored 在 `ext_src/`**，无需系统包：
  - `erfa`（天文星历）、`zlib`、`json`（jsmn 风格）、`uthash`、`stb`、
    `inih`、`nanovg`、`md4c`、`webp`、`sgp4`、`libtess2`
- 构建期只额外需要两个工具：**emscripten（提供 emcc/emscons）** 和 **scons（Python 构建驱动）**。
- `tools/make-assets.py` 仅用 Python 标准库（io/os/re/struct/zlib），自动把 `data/` 打包进 `src/assets/`。
- 产物：`build/stellarium-web-engine.js` + `build/stellarium-web-engine.wasm`。

依赖清单（从 SConstruct 提取，全部为源码内 vendored，非系统依赖）：
`erfa, zlib, json, uthash, stb, inih, nanovg, md4c, webp(dec), sgp4, libtess2`。
外部工具依赖：`emscripten ≥ 某版本`、`scons`、`python3`。无 Qt、无系统 GL、无系统 curl（`-DNO_LIBCURL`）。

---

## 3. Emscripten 安装尝试（无 sudo，装到 ~/emsdk）

| 步骤 | 结果 |
|---|---|
| `git clone --depth 1 https://github.com/emscripten-core/emsdk.git ~/emsdk` | 成功 |
| `./emsdk install latest` | 成功。解析到 SDK **6.0.10**，下载 node 24.19.0（31 MB）+ LLVM/wasm 工具链（299 MB），共约 1.7 GB 落盘 |
| `./emsdk activate latest` + `source emsdk_env.sh` | 成功 |
| `emcc --version` | `emcc (Emscripten gcc/clang-like replacement) 6.0.10` |
| `pip install --user scons` | 成功，scons 4.11.1 装到 `~/.local/bin` |

无网络超时、无磁盘满、无权限问题。安装脚本已固化为 `scripts/setup_emsdk.sh`。

---

## 4. 构建尝试（如实记录每一轮）

直接 `make js`（即 `emscons scons -j8 mode=release`）共经历 4 轮失败 → 1 轮成功：

### 第 1 轮失败：emscripten 选项改名
```
emcc: error: invalid command line setting `-sEXTRA_EXPORTED_RUNTIME_METHODS=[...]`:
No longer supported, use EXPORTED_RUNTIME_METHODS
```
- 原因：上游 SConstruct 用的是旧名 `EXTRA_EXPORTED_RUNTIME_METHODS`，emscripten 6.x 已改名。
- 补丁：SConstruct 中改为 `EXPORTED_RUNTIME_METHODS`。

### 第 2 轮失败：链接期选项被错误放进编译期
```
emcc: error: linker setting ignored during compilation: 'MODULARIZE' [-Wunused-command-line-argument] [-Werror]
```
- 原因：上游把 `-s MODULARIZE`、`--pre-js` 等**链接期** flags 同时追加进了 `CCFLAGS`（编译期）。新版 emcc 在编译 `.c→.o` 时遇到链接专属参数直接报错，叠加 `-Werror`。
- 补丁：把 `flags` 从 CCFLAGS 拆出，CCFLAGS 只保留 `-O3` + 宏定义；链接 flags 仍留在 LINKFLAGS。

### 第 3 轮失败：vendored 旧 zlib 的 K&R 函数定义
```
ext_src/zlib/*.c: error: a function definition without a prototype is deprecated ...
[-Werror,-Wdeprecated-non-prototype]
```
- 原因：vendored 的 zlib 是老版本，用旧式 K&R 定义；clang 19+（emscripten 6.0 自带）把该警告升级为 `-Werror` 下的错误。
- 处置：**不改源码**，改用 SConstruct 自带开关 `werror=0` 构建：
  `emscons scons -j8 mode=release werror=0`。

### 第 4 轮失败：JS 链接期引用了已移除的运行时符号
此时**所有 .o 目标文件已全部编译通过**，卡在 em++ 链接：
```
error: postlibrary.js: undefined exported symbol: "ALLOC_NORMAL" in EXPORTED_RUNTIME_METHODS
error: ... "_free" / "_malloc" / "allocate" ...
```
- 原因：SConstruct 的 `extra_exported` 列表来自 emscripten 1.37 时代，其中 `ALLOC_NORMAL`、`allocate`、`_free`、`_malloc` 在新版 emscripten 中已不再作为运行时方法导出。
- 补丁：从导出列表删除这 4 个符号。

### 第 5 轮：成功
```
em++: warning: JS library symbol '$writeAsciiToMemory' is deprecated. ...
scons: done building targets.
```
仅剩 1 条非致命 deprecation 警告（`writeAsciiToMemory`）。

---

## 5. 构建产物（已验证）

路径：`stellarium-web-engine/build/`

| 文件 | 大小 | 校验 |
|---|---|---|
| `stellarium-web-engine.js` | 76 KB | ASCII JS 胶水，内部引用 `stellarium-web-engine.wasm` |
| `stellarium-web-engine.wasm` | 1.2 MB | `file` 识别为 `WebAssembly binary module version 0x1`，magic `\0asm` 正确 |

> 说明：`SConstruct` 还定义了第二个 `Program` 目标 `build/stellarium-web-engine`（原设计为原生 host 程序，用于生成文档）。在 emscons 工具链下它未单独产出独立二进制——不影响 WASM 主产物。

---

## 6. 卡点分析与建议

### 为什么上游开箱即挂
上游 `SConstruct` 是多年前为 emscripten ~1.37/1.38 写的，而 `emsdk install latest` 拉的是 6.0.10。两者之间：
1. 运行时方法导出选项改名；
2. 链接/编译 flags 分类变严；
3. clang 默认 C 标准/弃用警告趋严；
4. 一批旧运行时 JS 符号被删除。

这些都是**构建脚本层面**的兼容问题，**不是 C/C++ 源码本身编译不过**——所有 130+ 个源文件在打补丁后全部编译通过。

### 仍未验证的风险（如实说明）
- 本次只验证到 **wasm/JS 产物生成成功**，**没有在浏览器里实际跑起来**。
- 因为删了 `allocate`/`ALLOC_NORMAL`/`_malloc`/`_free` 的导出，`src/js/*.js` 里的胶水代码（pre.js/obj.js/canvas.js 等）若仍调用 `Module.allocate()`、`ALLOC_NORMAL` 或 `_malloc`，**运行时可能报 undefined**。需要在浏览器（或 node + WebGL stub）里加载 `apps/simple-html/` 实测，并把胶水代码迁移到新版 API（如 `wasmMemory`、`HEAP*` 视图、`_malloc` 改为通过 `EXPORTED_FUNCTIONS` 导出 C 符号）。
- `writeAsciiToMemory` 已弃用，未来版本会彻底移除，需替换。

### 三种落地路径建议
1. **本地/云 VM 内继续**：可行。按本报告第 7 节复现脚本 + 3 处 SConstruct 补丁即可出包；后续主要工作量在 JS 胶水的运行时兼容与浏览器联调。
2. **CI 构建**（推荐）：在 GitHub Actions 上 `actions/checkout` + `emsdk` action + `pip install scons` + 打同样补丁 + `make js`，可固化产物。补丁建议以上游 patch 或 fork 分支形式维护。
3. **直接用预编译产物**：若只需在网页里嵌入渲染引擎、不需要改 C++，可直接用 Stellarium 官方已发布的 `stellarium-web-engine.js/.wasm`（apps/web-frontend 自带构建产物），不必从源码编。本次从源码编出的 1.2 MB wasm 与官方发布版体积同量级。

---

## 7. 复现步骤（已验证可跑通）

```bash
# 1. 装工具（无 sudo）
bash scripts/setup_emsdk.sh          # 装 emsdk 6.0.10 + scons

# 2. 打 3 处 SConstruct 兼容补丁（见本报告第 4 节，或直接 git diff SConstruct）

# 3. 构建
source ~/emsdk/emsdk_env.sh
cd stellarium-web-engine
emscons scons -j8 mode=release werror=0

# 4. 产物
ls -lh build/stellarium-web-engine.{js,wasm}
# 浏览器试玩: apps/simple-html/stellarium-web-engine.html
```

---

## 8. 结论

- **当前云环境能否构建？能。** 磁盘（~/ 10P）、网络（github/storage.googleapis 可达）、CPU/内存（4C/8G）都够，无 sudo 也能把 emsdk 装到 `~/emsdk`。
- **不需要 Qt、不需要 CMake、不需要系统级天文数据**——这是一个纯 C/C++ + SCons 的 WASM 项目，依赖全部 vendored。
- **唯一门槛**：上游 `SConstruct` 与新版 emscripten 6.x 之间有 3 处兼容性小补丁 + `werror=0`，本报告已全部定位并验证可出包。
- **剩余工作**：浏览器侧运行时联调（胶水 JS 里被删符号的 API 迁移），不属于"构建可行性"范畴。
