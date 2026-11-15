# T1-Remote 蓝牙遥控器上位机

一套针对 **T1-Remote**（厂商 `shenzhen_xingwei`，VID `0x620A` / PID `0x0407`，
MAC `AA:BB:CC:DD:EE:FF`）的协议逆向工具与图形化上位机。

核心能力：用遥控器的**语音键**录下麦克风音频、把遥控器按键映射成电脑上的动作、
在页面上的遥控器图形里点按键完成配置。

---

## 一、环境要求

- Windows 10 / 11
- Python **3.10+**（推荐 3.12；tkinter 版需要标准发行版，不能是精简版）
- 依赖：`bleak`、`hidapi`、`numpy`（可选 `pillow`，仅调试脚本用）

```bash
python -m pip install -r requirements.txt
```

> 启动脚本里把解释器路径写死成 `D:\py\python.exe`，不存在时自动回退到 `python`。
> 如果你机器上的 Python 不在这个位置，直接改 `.bat` 里的 `set PY=` 那一行。

---

## 二、运行

**前提：遥控器必须先在 Windows 蓝牙设置里配对并连接。**
设备一旦连上 Windows 就不再广播，程序靠固定 MAC 直连，扫描是扫不到的。

| 版本 | 启动方式 | 说明 |
|---|---|---|
| **Pro 版（推荐）** | 双击 `启动上位机Pro.bat` | 本地 HTTP 服务 + Edge app 窗口，浅色极简图形界面 |
| 经典版 | 双击 `启动上位机.bat` | tkinter 原生窗口，功能等价、样式朴素 |

命令行方式：

```bash
python t1_server.py                 # 起服务并自动打开窗口
python t1_server.py --port 8800     # 指定端口（默认 8791，占用则自动顺延）
python t1_server.py --no-browser    # 只起服务，手动用浏览器开
```

服务起来后，浏览器访问 `http://127.0.0.1:8791/` 也能打开界面。
**关闭那个命令行窗口即退出服务。**

> BLE 连接是独占的 —— Pro 版和经典版**不能同时连**同一个遥控器，后启动的会显示未连接。

---

## 三、界面

导航四页：**录音 / 按键 / 配置 / 日志**，设置收在侧栏底部的齿轮里。

### 录音页
- 大圆环显示实时电平，下方是滚动波形
- 按遥控器语音键自动开麦（`MIC_OPEN`），松手后自动关麦落盘
- 每段录音按序号存档到 `recordings/`，可回放、保存、删除

### 配置页（核心）
左侧是按实物照片重绘的 SVG 遥控器，**每个按键都是可点热区**：

- 点任意键 → 右侧立刻出现该键的 ID、所属 HID 集合、名称、动作、触发统计
- 被选中的键在图上高亮描边；实体按键按下时对应热区实时闪一下（SSE 联动）
- 一个物理键上报多个码的情况做了合并，顶部出现 chip 可切换分别配置
  （例如音量长条：`C:0x00E9 音量+` / `0xEA 音量−` / `0xE2 静音`）
- 电源键未捕获到 HID 事件，故置灰并提示"无法映射"

改完点保存直接写回 `t1_ui_config.json`。

### 按键页
按 HID 集合分组的按键卡片，显示每个码的触发次数。点卡片会跳到配置页并选中该键。

### 日志页
实时事件流（BLE 连接、ATVV 命令、HID 上报、动作执行）。

---

## 四、动作语法

配置页里给按键填的「动作」支持四种写法：

| 写法 | 作用 | 示例 |
|---|---|---|
| `key:` | 合成按键（SendInput） | `key:ctrl+shift+esc`、`key:win+d` |
| `text:` | 输入一段文本 | `text:你好世界` |
| `open:` | 打开文件/网址/程序 | `open:https://example.com` |
| `cmd:` | 执行命令（默认关闭，见配置 `run_cmd`） | `cmd:notepad.exe` |

多个动作可以用 `;` 串联。

---

## 五、协议速查（Google ATVV v1.0）

服务 UUID 基址 `AB5E0001-5A21-4F05-BC7D-AF01F617B664`：

| 特征值 | 方向 | 用途 |
|---|---|---|
| `...0002` | 写 | TX 命令通道 |
| `...0003` | 通知 | AUDIO 音频上行 |
| `...0004` | 通知 | CTL 事件 |

关键命令：

```
GET_CAPS    0A 01 00 00 03 03
MIC_OPEN    0C 01
MIC_CLOSE   0D <stream_id>
MIC_EXTEND  0E <stream_id>     # 每 2s 一次，防 3s 超时
```

`CAPS_RESP` 返回 `0B 01 00 02 03 00 80 00 00` → 版本 `0x0100`、
编码 `0x02`（IMA ADPCM 16kHz 单声道）、帧长 128 字节（=256 采样=16ms，约 62.5 帧/秒）。

**音频解码两个坑**（`codec.py` 已处理）：

1. 每段流开头有 4 字节 `77 77 77 77` 填充头，**必须跳过**，否则 ADPCM 步长会被推到满量程、整段削顶
2. 解码后有明显 DC 偏移，需过 4 阶高通（默认 120Hz）再归一化

HID 侧（`0x1812`）共 5 个集合：

| 集合 | Report ID | 长度 | 内容 |
|---|---|---|---|
| COL01 键盘 | `0x01` | 8B | 方向键 0x48/0x50/0x4B/0x4D，OK 0x1C |
| COL02 Consumer | `0x02` | 3B | 语音 0x0221、主页 0x0223、返回 0x0224、音量 0xE9/0xEA、静音 0xE2 |
| COL03 System | `0x03` | 2B | 电源等系统键 |
| COL04 Mouse | `0x04` | 5B | — |
| COL05 Vendor | `0x5A` | 256B | — |

> 键盘/鼠标集合被 Windows `hidclass` 独占，`hidapi` 打不开，所以方向键走的是
> **Raw Input 旁路**（`t1_gui.py` 里的 `RawInputWatcher`），不占用设备句柄。

### 为什么最终不装拦截驱动

一开始的设想是用内核过滤驱动把按键在进入 Windows 输入队列前截住，实测两条路都走不通：

| 方案 | 结果 |
|---|---|
| TitanCSA Interception V2 | 能装（WHQL 签名，签至 2027），但**演示版每次开机仅 1 小时**，到期后双触发回归 |
| oblitum 原版（LGPL 3.0 非商业免费） | 驱动用 2012 年就已过期的**个人**证书签名（`CN=Francisco Lopes da Silva`），Win11 内核拒绝加载；它还会往键盘/鼠标 `UpperFilters` 插入过滤链，装错会让键鼠起不来 |

真正的答案更简单：**语音功能根本不需要 HID 按键**。

```text
按下语音键
  ├─ BLE 控制点 → EV_START_SEARCH → MIC_OPEN → 音频流 → SenseVoice → 键入
  └─ HID 报文   → Windows 原生处理（实测 0x0221 不触发任何系统行为）
```

所以最终形态是：

- **语音键**：走 BLE ATVV，与 HID 通道无关，不装任何驱动
- **其余按键**：不做映射，交给 Windows 原生处理（音量、方向、OK、返回本来就是想要的功能）
- 结果：**无双触发、无时限、无管理员权限要求**

`ic_keys.py` 仍保留在代码里（`ic_block: true` 可启用），但它依赖的驱动在现代 Windows
上已不可用，属于历史遗留，默认关闭。

若将来真要把方向键改成翻页之类，正确方向是 **HidHide**（`nefarius/HidHide`，MIT，
微软 attestation 签名）：按设备 cloak，让 Windows 完全看不到遥控器的 HID 集合，
同时白名单放行本程序直读 HID report。失败模式安全——只影响遥控器，物理键盘不受影响。


---

## 六、目录结构

```
├─ 启动上位机Pro.bat        Pro 版启动脚本
├─ 启动上位机.bat           经典 tkinter 版启动脚本
├─ t1_server.py             Pro 版后端：HTTP + SSE + BLE 线程 + Raw Input
├─ t1_gui.py                经典版界面，同时被 server 复用后端线程（BleWorker 等）
├─ codec.py                 IMA ADPCM 解码 + 高通 + 归一化 + WAV 写出
├─ keysynth.py              SendInput 按键/文本/程序合成
├─ ic_keys.py               Interception 驱动绑定（历史遗留，默认不启用）
├─ t1_ui_config.json        两版共用配置（键映射、音频参数、输出目录）
├─ requirements.txt
├─ web/                     Pro 版前端（原生 JS，无构建）
│   ├─ index.html           页面结构 + SVG 遥控器
│   ├─ style.css            浅色极简样式
│   └─ app.js               状态管理、SSE 接收、热区联动
├─ docs/
│   └─ ATVV-spec-1.0.txt    Google Voice over BLE 规范原文
├─ tools/
│   └─ shot.mjs             CDP 无头截图（调试 UI 用，需 node）
└─ 以下为逆向阶段的探测/分析脚本，正常情况下用不到
   t1_mic.py       命令行录音，按段存档
   t1_raw.py       Raw Input 抓包
   t1_rawdbg.py    Raw Input 调试
   t1_rawlist.py   列出 Raw Input 设备
   t1_voice.py     语音键触发录音的最小验证
   t1_selftest.py  链路自检
   t1ctl.py        ATVV 命令小工具
   t1_attrs.py     GATT 属性枚举
   analyze2.py / analyze_adpcm.py / scan_codec.py   音频参数扫描
   ble_*.ps1 / hid_*.ps1 / rawinput_*.ps1           PowerShell 早期探测脚本
```

---

## 七、配置说明

`t1_ui_config.json` 主要字段：

| 键 | 含义 |
|---|---|
| `mac` | 遥控器蓝牙地址，换设备时改这里 |
| `outdir` | 录音输出目录，相对路径则基于工程目录 |
| `skip_bytes` | 跳过流首填充字节数（实测 4） |
| `hpf_hz` | 高通截止频率（默认 120Hz，去 DC 偏移） |
| `normalize` | 是否峰值归一化 |
| `mic_extend_ms` | `MIC_EXTEND` 间隔（默认 2000ms） |
| `max_segment_s` | 单段最长时长，超时自动切段 |
| `voice_input` | 松开语音键后把这段语音识别成文字，键入到当前焦点窗口 |
| `stt_engine` | 识别引擎：`auto`（优先 SenseVoice）/ `sensevoice` / `whisper` / `sapi` |
| `stt_model` | Whisper 模型：`base` / `small`（推荐）/ `medium`（仅 whisper 引擎用） |
| `stt_lang` | 识别语种，默认 `zh` |
| `stt_beam` | Whisper 束搜索宽度，越大越准也越慢（默认 5） |
| `stt_vad` | Whisper 是否先用 VAD 剪掉静音段，减少幻觉 |
| `stt_threads` | SenseVoice 解码线程数（默认 4） |
| `stt_prompt` | Whisper 提示词，引导输出简体中文与标点；对 SenseVoice 无效 |
| `hid_listen` | 是否启用按键监听 |
| `ic_block` | 是否尝试加载 Interception 驱动拦截按键（默认 `false`；现代 Windows 上该驱动已不可用） |
| `ic_debug` | 每个按键记一行 `IC dev=N vk=0x.. 拦下/透传`，排查设备绑定时开 |
| `run_key` / `run_cmd` | 是否允许 `key:` / `cmd:` 动作执行 |
| `keymap` | 键映射表，键名格式 `K:0x..`（键盘） / `C:0x....`（Consumer） |

---

## 八、HTTP 接口

前端与后端之间的接口，需要脚本化调用时可直接用：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/bootstrap` | 初始全量状态 |
| GET | `/api/events` | SSE 事件流 |
| GET | `/api/seg/<id>` | 取某段录音（wav） |
| POST | `/api/connect` / `/api/disconnect` | 连接/断开遥控器 |
| POST | `/api/caps` | 查询 ATVV 能力 |
| POST | `/api/config` | 保存配置 |
| POST | `/api/key` | 保存单个按键映射 |
| POST | `/api/key/reset` | 重置按键映射 |
| POST | `/api/action/test` | 试跑一个动作 |
| POST | `/api/action/describe` | 解析动作语法 |
| POST | `/api/seg/save` / `/api/seg/delete` | 保存/删除录音段 |
| POST | `/api/window` | 前台窗口信息 |
| POST | `/api/hid/start` | 启动 Raw Input 监听 |
| POST | `/api/logs/clear` | 清空日志 |
| POST | `/api/shutdown` | 退出服务 |

---

## 九、常见问题

**连不上 / 一直显示未连接**
1. 确认 Windows 蓝牙设置里显示"已连接"
2. 确认另一个版本（tkinter 版）没在同时跑
3. 遥控器休眠了，按一下任意键唤醒
4. 改过 MAC 的话检查 `t1_ui_config.json` 里的 `mac`

**录音是噪音 / 满幅削顶**
检查 `skip_bytes` 是否为 4。填充头没跳过会导致 ADPCM 步长爆表。

**方向键在配置页里没反应**
方向键走键盘集合，需要 Raw Input 监听生效（`hid_listen: true`）。
另外监听窗口必须可见 —— message-only 窗口收不到 `RIDEV_INPUTSINK` 事件。

**语音识别不准 / 识别成乱码**
1. 确认设置页「语音识别 → 识别引擎」不是 `Windows 听写`。系统听写中文准确率极低
   （同样的音频，SAPI 输出"每天老妈"，SenseVoice 输出"喂喂喂，可以听到吗？"）
2. 引擎选 `auto` 或 `sensevoice`；只有需要英/日/韩/粤时才切 `whisper`
3. 日志里出现「识别引擎加载失败，已降级」通常是首次下载模型时没网，
   连上网重启一次即可
4. 首次识别会慢一点（要加载模型，SenseVoice 约 4～6 秒），之后每句 0.1～0.2 秒

**语音输入太慢 / 说完要等好几秒**
这是 Whisper 的结构性开销，不是配置问题：Whisper 每次都要把音频补齐成 **30 秒的
编码窗口**，12 层 × 1500 帧的编码器无论你说 2 秒还是 10 秒都要完整跑一遍，
在 Ryzen 5 5600H 上稳定 5～7 秒。

SenseVoice 是**非自回归**模型（CTC 风格，一次前向出全部文本），计算量只和真实音频
长度成正比。同一批录音实测：

| 音频 | Whisper small（int8） | SenseVoice（int8） |
|---|---|---|
| 2.40s「现在的语音输入正常吗？」 | 5.9s ❌ 听成「音输入」 | **0.14s** ✓ |
| 3.23s「现在的语音输入可以吗？」 | 7.3s ❌ 听成「音输入」 | **0.15s** ✓ |
| 2.51s「这是一次语音输入的测试」 | 2.5～3.5s（加提示词才对） | **0.13s** ✓ |

也就是说默认引擎既快 30～40 倍，中文准确率还更高。`sensevoice` 不需要提示词，
自带标点与数字规范化（ITN）。

模型放 `models/sense-voice/`（`model.int8.onnx` 228MB + `tokens.txt`），
首次使用会自动从镜像下载；也可以手动放置：

```bash
# 走 HF 镜像（国内直连不通）
export HF_ENDPOINT=https://hf-mirror.com
python -c "from huggingface_hub import hf_hub_download as d; \
  [d('csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17', f, local_dir='models/sense-voice') \
   for f in ('model.int8.onnx','tokens.txt')]"
```

**改代码时注意导入顺序**
`t1_gui.py` 里 `import stt` 必须排在 `from bleak import ...` 之前。
ctranslate2.dll 若在 bleak 的 WinRT backend 之后加载，初始化模型时会因 TLS 冲突
直接段错误（exit 3221225477），且不产生任何 Python 异常，极难排查。
`stt` 的导入会顺带把 ctranslate2 提前载入，顺序反过来就会崩。

**想换遥控器**
改 `t1_ui_config.json` 的 `mac`，然后删掉 `keymap` 重新抓一遍按键。

**移动了工程文件夹 / 换了一台电脑**

代码里的路径全部基于脚本所在目录（`BASE_DIR`）解析，整个文件夹可以随便搬。
换电脑时需要：

1. 装 Python 3.8+（建议 3.12），然后 `python -m pip install -r requirements.txt`
2. 语音识别默认用本地 SenseVoice（sherpa-onnx，中文专用），首次识别会自动下载模型到
   `models/`，之后完全离线；不可用时依次退回 faster-whisper（需另装，见 requirements.txt
   末尾注释）、Windows 内置听写（后者准确率明显更低）
3. 删掉 `.browser_profile/`，它存的是 Edge 用户数据，和本机路径绑定，删了会自动重建
4. 遥控器在系统蓝牙里重新配对；配置里把 `mac` 留空、`name` 填设备名即可自动查找

**Playwright / 无头截图报错**
`tools/shot.mjs` 用的是原生 CDP，需要能跑 `node`。不用它不影响主程序。
