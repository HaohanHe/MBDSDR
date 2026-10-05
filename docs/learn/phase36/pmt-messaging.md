<!-- SPDX-License-Identifier: MIT | Phase36 G5: GNU Radio PMT / message passing 机制深读（clean-room，只学不抄） -->

# Phase36 G5：GNU Radio PMT 与消息传递机制

> 精读对象：`repos/gnuradio/gnuradio-runtime/lib/pmt/` + `lib/basic_block.cc` + `lib/tpb_thread_body.cc` + `gr-pdu/`（真读源码）
> 我方对照：`cpp/src/ai/`（QSignals 事件）、`cpp/src/control/control_hub.*`（HTTP/命令分发）、`cpp/src/ai/llm_worker.*`+`agent_tools.*`（agent 工具调用）
> 红线：只学机制，不抄 GPL 代码；file:line 留痕。

## 1. 机制总结

GNU Radio 用一套"Lisp 风格"的动态类型系统（PMT，Polymorphic Type）做**异步消息**的载荷容器，再用**命名端口 + 发布/订阅**把 block 连成消息网。它与同步流（streaming samples）是两条完全解耦的通路。

四层结构：

| 层 | 角色 | 代表类/函数 |
|---|---|---|
| 任意类型 | 一切消息载荷的基类 | `pmt_base` / `pmt_t` |
| 数据构造 | symbol / cons(pair) / vector / dict / uniform_vector | `cons`, `make_dict`, `make_u8vector` |
| 消息端点 | 一个能收消息的抽象 sink | `msg_accepter::post(port, msg)` |
| block 集成 | 每 block 一组入队 + 一组出订阅表 | `basic_block::msg_queue` / `d_message_subscribers` |

### 1.1 PMT 对象模型：shared_ptr 即引用计数

- `pmt_base` 是纯虚基类，靠一组 `is_bool/is_symbol/is_pair/is_dict/...` 虚谓词做运行时类型判别（`include/pmt/pmt.h:45-80`）。
- **`typedef std::shared_ptr<pmt_base> pmt_t;`**（`pmt.h:85`）——所谓"透明引用计数"就是直接用 `shared_ptr`，没有手写 refcount。所有 PMT 对象都在堆上、按值传递智能指针，循环引用靠 Lisp 数据结构本身无环（dict 是不可变链表，每次 add 产生新节点）来规避。
- 类型不安全是刻意的：`car()` 内部 `dynamic_cast<pmt_pair*>`，失败抛 `wrong_type`（`pmt.cc:311-318`）。即"运行时鸭子类型 + 异常"，而非编译期模板。

### 1.2 Symbol 驻留（interning）

`string_to_symbol`（`pmt.cc:150-175`）：
- 8192 桶哈希表（`pmt.cc:137`），桶内是单链表（`pmt_symbol::d_next`，`pmt_int.h:39,48-49`）。
- 先无锁查一次；未命中再加全局 `mutex` **二次查**（double-checked），再 new 并头插。
- 结果：同名字符串永远映射到**同一个指针**，于是 symbol 相等 = 指针相等（`eq`，`pmt.cc:820`），端口名/字典 key 比较 O(1)。

### 1.3 cons / pair / dict：不可变关联表（alist）

- `cons(x,y)` 就是 `new pmt_pair(x,y)`（`pmt.cc:309`），car/cdr 两个槽（`pmt_int.h:113-128`）。
- **dict 不是哈希表，是 cons 链表实现的 association list**：`pmt_dict : pmt_pair`（`pmt_int.h:130-137`），空 dict 就是 `PMT_NIL`（`make_dict()`，`pmt.cc:650`）。
- `dict_add`（`pmt.cc:663-672`）：若 key 已存在则先删旧再头插新——**函数式、不可变**，返回新 dict，旧 dict 原样保留（共享结构）。
- `dict_ref`（`pmt.cc:697-704`）走 `assv` **线性扫描**；`dict_delete`（`pmt.cc:686-695`）递归重建链表。

> 含义：dict 是"小而少改"的元数据容器（PDU 头、配置），不是大 KV 存储。O(n) 扫描是有意的取舍。

### 1.4 uniform_vector：类型化载荷

`pmt_uniform_vector`（`pmt_int.h:186-198`）是带 `itemsize()` 的裸内存数组基类，派生出 `u8/u16/f32/c32...` 向量（`pmt.h:68-79`）。这是 PMT 里**唯一零拷贝语义**的大块数据通道——消息里传的是 IQ 字节/样本，而不是一个个 PMT 装箱对象。

### 1.5 PDU 约定

`is_pdu`（`pmt.cc:815-818`）：

```
is_pdu(o) = is_pair(o) && is_dict(car(o)) && is_uniform_vector(cdr(o))
```

即 **PDU = cons( 元数据 dict , 类型化载荷向量 )**。`gr-pdu/lib/pdu_set_impl.cc:41-48` 是标准用法：`meta=car(pdu)` → `dict_add` 改头 → `cons(new_meta, cdr(pdu))` 重建 → 重新 `message_port_pub`。PDU 块常是 `io_signature(0,0,0)`——纯消息块，无采样流。

### 1.6 两种"接收端"实现

`msg_accepter` 只是接口，有两种落地：
- **block 自带端口队列**：入端口 = `map<symbol, deque<pmt_t>>`（`basic_block.h:70-99`），由 block 的 worker 线程抽干（见 §2-C）。这是主流。
- **独立有界阻塞队列** `msg_queue`（`msg_queue.h:30-78`）：内部 `deque<pmt_t>` + 互斥 + 两个条件变量（`not_empty`/`not_full`），`insert_tail` 满则阻塞、`delete_head` 空则阻塞，`limit=0` 表示无界。`msg_accepter_msgq::post` 就是 `d_msg_queue->insert_tail(msg)`（`messages/msg_accepter_msgq.cc:27`）。
- 区别：block 端口队列**无界 + 无消费者丢最老**（§2-D）；独立 `msg_queue` **有界 + 满则反压发送方**。两种背压策略对应两种语义：实时流宁可丢旧，命令流宁可阻塞。

### 1.7 异步消息 vs 同步流（核心区分）

| 维度 | 同步流（stream） | 异步消息（message） |
|---|---|---|
| 数据 | 连续采样样本，固定 itemsize | 离散 PMT 对象，任意类型 |
| 调度 | `work()` 按 ninput/noutput 块推进，带流控 | `post()` 随时入队，worker 循环点抽干 |
| 连接 | 相邻 block 点对点，拓扑编译期定 | 命名端口 pub/sub，运行时可增减订阅者 |
| 时序 | 实时、有 deadline | 尽力而为、可丢、可延迟 |

两者在同一 block 内共存：`work()` 处理流，消息 handler 处理命令/PDU，由 TPB 循环在同一线程里分时（`tpb_thread_body.cc:73-97`）。

## 2. 关键算法（file:line）

**A. 入站消息：永远先入队，绝不直接回调**
- `msg_accepter::post(port,msg)` 是唯一虚 sink，注释明说异步、不等送达（`msg_accepter.h:30-37`）。
- 基类 `post` 把自己 `dynamic_cast` 成 `block*` 后调 `_post` → `insert_tail`（`msg_accepter.cc:30-46` → `basic_block.cc:172-191`）。
- `insert_tail`：持 `mutex` 把 msg `push_back` 到该端口的 `deque`，然后 `notify_blk` 唤醒 block 线程（`basic_block.cc:177-191`）。

**B. 出站消息：扇出（multicast）**
- 每个出端口在 `d_message_subscribers`（一个 PMT dict）里存"订阅者列表"，元素是 `(block_id . in_port)` 对（`basic_block.cc:125-136`）。
- `message_port_pub` 遍历该列表，逐个 `global_block_registry.block_lookup(block)->post(port,msg)`（`basic_block.cc:127-137`）。一对多。

**C. 调度：消息在 worker 循环的"安全点"被串行消费**
- TPB 线程主循环**先**把所有入端口队列非阻塞抽干（`delete_head_nowait`）→ `dispatch_msg` 调用户 handler，**再**跑同步流 `run_one_iteration`（`tpb_thread_body.cc:73-97`）。
- 结果：消息处理与采样流处理在**同一个 worker 线程**里串行，block 内部状态无需加锁；外部 `post` 只是无锁入队 + 唤醒。

**D. 背压：无 handler 时丢最老的，有界**
- 端口没挂 handler 且队列超过 `max_messages`（默认 100）时，从队头丢弃并告警（`tpb_thread_body.cc:82-91`）。不阻塞发送方、不无限涨内存。

## 3. 可借鉴点（按性价比排序）

1. **"消息在自己线程的安全点被抽干"这条纪律**——发送方只入队、不碰接收方状态；消费方在固定循环点串行处理。这是异步事件不踩数据竞争的根本。
2. **有界队列 + 无消费者时丢最老并告警**——比"无限排队"或"发送方阻塞"都稳。注意 GR 其实给了两档：实时态丢旧（block 端口）、命令态反压（独立 `msg_queue`）。我方 UI 高频态选"丢中间态保最新"，控制命令态选"阻塞/排队"，正好各取一档。
3. **端口名 = 驻留符号**：连接标签用 interned 字符串，相等比较退化为指针比较，且天然可做 dict key。
4. **(元数据 dict + 不透明类型化载荷) 的 PDU 信封**——头/体分离，元数据可独立增删而不动载荷。
5. **单一 `post(port,msg)` sink 抽象**——所有能收消息的东西实现同一个接口，发布者无需知道订阅者具体类型。

## 4. 我方差距判定（对照 cpp/src/ai、ControlHub、agent 工具调用）

| GR 机制 | 我方现状 | 判定 |
|---|---|---|
| 消息在 worker 安全点串行抽干 | ControlHub `dispatch` 强制在 engine 主线程跑，跨线程用 blocking queued call（`control_hub.h:35-38`）；QSignals 跨线程走 Qt queued event | **已等价**，无需补 |
| 有界队列 + 无消费者丢最老告警 | QSignal 跨线程 queued connection **无界**；高频 statusChanged/partialReady 理论上可堆积 | **值得补**：给高频 UI 事件加一个有界槽（满则丢中间态，只保最新） |
| 端口名 interned 符号 | ControlHub 已是 `CommandRow{name,write,Handler}` 名字表（`control_hub.h:137`）；工具名是 `executeTool` 里的字符串分发（`agent_tools.h:13`） | **已等价**；名字即 key，无需再做符号表 |
| 单一 post(port,msg) sink + 扇出 | 我方是 1 生产者→少量 UI 的直连 signal，Qt 已做多播 | **YAGNI**：订阅者个位数，自造发布订阅层是过度设计 |
| 全套 PMT 动态类型 / cons/dict/lisp | 工具参数已是 `QJsonObject`（`llm_worker.h:69`），天然 JSON 可序列化 | **YAGNI**：LLM 工具参数本来就是 JSON，再套一层 s-exp 装箱纯属冗余 |
| 不可变 alist dict | QJsonObject 是可变哈希 map | **不抄**：我方字典要改要查，QJsonObject 更合适；alist 的 O(n) 扫描对大字典是坑 |
| PDU = cons(meta, payload) | 工具结果是 QString/JSON，暂无大块二进制流 | **保留为未来模式**：将来做 IQ 帧/录制回放时，用 (QVariantMap 头 + QByteArray 体) 信封，现在不建 |

**结论**：核心并发纪律我方已具备（ControlHub 主线程串行 + Qt queued event）。真正的差距只有一条——**高频 UI 事件缺背压**。其余（PMT 类型系统、发布订阅、alist dict）按 YAGNI 不补，理由如上。

## 5. 反例核查（避免照抄的坑）

- **alist dict 是 O(n) 线性扫描**（`pmt.cc:697-704`）：只适合小元数据。我方任何"字典"需求都用 QJsonObject/哈希，切勿照搬链表实现。
- **symbol 表只增不删**（`pmt.cc:171-174`，进程生命周期常驻）：因为符号少且稳定。我方绝不能对用户输入/动态字符串做驻留，否则内存泄漏。
- **`message_port_pub` 每条消息都按字符串名查全局注册表**（`basic_block.cc:134`）：订阅者少时无所谓；我方订阅者模型简单，不要引入"按名查全局对象"这种间接层。
- **`msg_accepter::post` 用 `dynamic_cast<block*>` 向下转型**（`msg_accepter.cc:33`）：脆弱基类设计。我方应像 ControlHub 那样用明确接口/命令表，不在基类里猜派生类型。
- **不可变 dict 每次 add 都重建链表**（`pmt.cc:663-672`）：写多读多场景会制造大量垃圾。我方配置/状态是原地改，不采用函数式不可变。
