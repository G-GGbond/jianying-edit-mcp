# 剪映 AI 剪辑 MCP

让 AI 直接产出**可以在剪映里继续改的工程**，而不是丢给你一段改不了的视频。

支持把它接进 **豆包工作 / Claude Desktop / Trae / Cursor** 等任何支持 MCP 的工具。

---

## 它和别的"AI 剪辑"有什么不一样

| | 普通 AI 剪辑工具 | 这个 |
|---|---|---|
| 产出 | 一段 MP4，改一个字要重剪 | **剪映草稿工程**（多轨时间线 + 字幕轨 + 音乐轨） |
| 二次修改 | 只能重来 | 在剪映里随便拖、随便改 |
| 出片 | 云端渲染 | **调剪映自己的渲染引擎**无头导出（不占桌面） |
| 素材留在哪 | 上传到别人服务器 | **全程本机**，不上传 |

---

## 快速开始（Windows）

### 第 1 步：装剪映和 jy-draftc

```powershell
# 剪映（版本必须与你下载的 jy-draftc 匹配，当前验证组合是 11.6.0.14552）
winget install --id ByteDance.JianyingPro --exact --version 11.6.0.14552
```

去 [jy-draftc releases](https://github.com/wenshui330/jy-draftc/releases) 下载**对应版本**的
`jy-draftc-11.6.0.14552-*.zip`，解压出 `jy-draftc.exe`，放到任意位置并设环境变量 `JYC_JY_DRAFTC` 即可
（程序会自动找到它，也可以放别处再设环境变量）。

> ⚠️ 装完剪映请**关掉自动更新**。剪映一升级，草稿结构和加密方式可能变化，jy-draftc 就得跟着换。

### 第 2 步：装 MCP 包（二选一）

**方式 A：一条命令（推荐）**

```powershell
pip install git+https://github.com/G-GGbond/jianying-edit-mcp
```

**方式 B：下载仓库 + 一键脚本**（适合想用启动器、不想管 PATH 的人）

```powershell
git clone https://github.com/G-GGbond/jianying-edit-mcp
cd jianying-edit-mcp
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

`install.ps1` 会自动检查 Python、装依赖、探测剪映和 jy-draftc、生成启动器，并**打印你要粘贴的配置**。

### 第 3 步：接进豆包

打开**豆包工作客户端**（先登录），然后：

1. 侧边栏 → **「技能 · 连接器 · 伙伴」** → **「技能 · 连接器」**
2. 右上角 **「+ 新建」** → **「新建自定义连接器」**
3. **传输类型选 `STDIO`**（默认是 HTTP，一定要改）
4. 按下表填：

| 字段 | 方式 A 填这个 | 方式 B 填这个 |
|---|---|---|
| 服务器名称 | `jianying-edit` | 同左 |
| 传输类型 | `STDIO` | 同左 |
| 命令 | `python` | `C:\...\start-jianying-mcp.bat`（脚本会打印） |
| 参数 | `-m jianying_edit_mcp.mcp_server` | 留空 |
| 环境变量 | **留空**（自动探测） | 留空（已写进启动器） |

> 环境变量不用填：server 会自动找剪映（取版本号最大的那个）和 `jy-draftc.exe`
> （依次找 当前目录/tools → 包目录/tools → PATH → ~/.jy-draftc/）。
> 只有探测失败时，才需要设 `JYC_JY_DRAFTC` / `JYC_JIANYING_DIR` 手动指路。

5. 保存。**如果提示「连接器运行失败」，到「我的技能 → 连接器」列表点「重新启动」**（首次初始化慢，正常）。

**建议**：把 [`docs/AI-工作规则.md`](docs/AI-工作规则.md) 的内容贴进连接器的"描述/系统提示"。
不然 AI 不知道"剪映工程"该怎么建，可能一上来就想用 ffmpeg 拼成片。

### 第 4 步：说一句话试试

> 先用剪映工具做个体检

> 把 `E:\素材\展会` 里那几段剪成 30 秒横屏介绍片，带字幕和配乐

> 把工程打包发给同事

---

## 连不上？先跑自检

```powershell
python examples\selfcheck.py
```

它会打印探测到的剪映与 jy-draftc，然后跑完整流程（建工程 → 出片）。
**全绿就说明 server 没问题，问题在客户端连接器配置。**

---

## 在其他客户端里用

<details>
<summary><b>Claude Desktop</b></summary>

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "jianying-edit": {
      "command": "C:\\你的路径\\start-jianying-mcp.bat",
      "args": []
    }
  }
}
```
</details>

<details>
<summary><b>Trae / Cursor</b></summary>

同样填 `mcpServers` 结构，命令指向 `start-jianying-mcp.bat`。
</details>

<details>
<summary><b>不想用启动器（自己填环境变量）</b></summary>

```json
{
  "mcpServers": {
    "jianying-edit": {
      "command": "python",
      "args": ["-m", "jianying_edit_mcp.mcp_server"],
      "env": {
        "JYC_JY_DRAFTC": "C:\\tools\\jy-draftc.exe",
        "JYC_JIANYING_DIR": "C:\\Users\\you\\AppData\\Local\\JianyingPro\\Apps\\11.6.0.14552"
      }
    }
  }
}
```

`JYC_JIANYING_DIR` 必须是指向 `videoeditor.dll` 的那一层目录。
</details>

<details>
<summary><b>只想要命令行</b></summary>

```powershell
pip install jianying-edit-mcp

# 环境体检
jianying-edit doctor

# EDL → 剪映草稿
jianying-edit build --edl examples\edl-12s-demo.json --out .\out

# 无头导出
jianying-edit render --draft .\out\xxx_剪映可读 --out .\out\final.mp4
```
</details>

---

## 九个工具

| 工具 | 做什么 |
|---|---|
| `jy_doctor` | 环境体检：Python 依赖、ffmpeg、剪映版本、jy-draftc、草稿位置 |
| `jy_build` | 剪辑方案（EDL JSON）→ 剪映草稿（同时给明文版和剪映可读版） |
| `jy_render` | 调剪映自己的引擎无头导出 MP4 |
| `jy_inspect` | 读草稿结构：轨道、片段数与时长、素材数量（加密草稿自动解密） |
| `jy_narrate` | **一键口播配音**：脚本 → 逐句合成 → 压到时间槽 → 整轨 + EDL。支持音色克隆与语气控制 |
| `jy_music` | 搜索剪映云端曲库（内置 615 首，含中英文） |
| `jy_cover` | 封面选帧：按清晰度/曝光/对比度/构图挑候选（需视觉复核） |
| `jy_beat` | 音乐节拍分析（BPM）与镜头时长卡点吸附 |
| `jy_share` | 打包分享：工程 + 全部素材 + 说明 → zip |

## 竖屏黑边：自动背景轨

16:9 素材放进 9:16 画布会上下留黑。EDL 加一个字段就能自动修：

```json
{
  "canvas": { "width": 1080, "height": 1920, "fps": 30 },
  "autoBackground": { "blur": 32, "brightness": -0.14 },
  "tracks": [ ... ]
}
```

`jy_build` 会用 ffmpeg 为第一段视频轨的每个片段生成"铺满画布 + 模糊 + 压暗"的背景，
自动插到最底层。渲染出来上下是模糊的画面延伸，不再有黑边。

## 卡点：让切换踩在节拍上

```powershell
# 先看 BPM 和节拍点
jianying-edit beat --music bgm.mp3 --seconds 30

# 把镜头时长吸附到节拍（最多允许改 35%，防止画面被硬拉）
jianying-edit beat --music bgm.mp3 --durations 3.6 2.4 2.4 4.2
```

纯 numpy 实现，不需要 librosa。实测：对已知 100 BPM 的曲子检出 99.4 BPM（误差 0.6%）。
`confidence` 低于 0.1 说明节拍不明显（氛围音），卡点效果有限。

## 封面选帧：规则只能粗筛

```powershell
jianying-edit cover --video clip.mp4 --count 5
```

按清晰度（Laplacian 方差）、曝光、对比度、构图居中打分，扫描全程取最优几帧。

> 必须视觉复核。实测踩过坑：一张"人物背影挡住画面"的帧因为清晰、曝光好、
> 居中，拿到了 0.94 高分——规则评分判断不了构图好坏。
> 拿到候选图后要逐张看，再决定用哪张。

需要 `pip install opencv-python`（可选依赖，只在用封面选帧时需要）。

## 配乐：本地音乐 vs 云端曲库

**配乐请优先用本地音频文件。** 云端曲库能写进工程，但有无头导出的限制（见下）。

### 本地音乐（推荐，可无头出片）

```json
{ "type": "audio", "name": "音乐", "start": 0,
  "clips": [{ "src": "E:\\music.wav", "in": 0, "duration": 30, "fadeIn": 0.4, "fadeOut": 1.8 }] }
```

### 云端曲库（只写工程，无头导出不解析）

内置 615 首剪映云端曲库，可搜索：

```powershell
jianying-edit music --keyword 科技 --limit 5
```

EDL 里这样引用：

```json
{ "type": "audio", "name": "音乐", "start": 0,
  "clips": [{ "cloudMusic": "科技引领", "in": 0, "duration": 30, "fadeIn": 0.4 }] }
```

> ⚠️ **实测限制（对照实验验证）**：云端音乐只写入工程，剪映**在界面打开工程时**才按 id 在线解析。
> 用 `jy_render` 无头导出时**不会解析**，成片只有视频原声。
> 带云端音乐的 EDL 会返回 `cloudMusicWarning` 字段提醒。
>
> 结论：**要无头出片带配乐 → 用本地音频**；只是生成工程给人到剪映里编辑 → 云端曲库可用。

## EDL 长什么样

```json
{
  "name": "我的片子",
  "canvas": { "width": 1920, "height": 1080, "fps": 30 },
  "tracks": [
    { "type": "audio", "name": "音乐", "start": 0,
      "clips": [{ "src": "E:\\music.wav", "in": 0, "duration": 30, "fadeIn": 0.4, "fadeOut": 1.8 }] },
    { "type": "video", "name": "主画面", "start": 0,
      "clips": [{ "src": "E:\\a.mp4", "in": 1.2, "duration": 2.4 },
                { "src": "E:\\b.mp4", "in": 0.8, "duration": 3.6 }] },
    { "type": "text", "name": "主标题",
      "texts": [{ "start": 0.3, "duration": 3.1, "text": "标题",
                  "style": { "size": 12, "transformY": -0.62 } }] }
  ]
}
```

要点：**文字必须分轨**（主标题一条轨、副标题另一条），同轨时间重叠会失败；
素材写绝对路径；`in + duration` 不能超过素材实际时长。

完整说明见 [`docs/AI-工作规则.md`](docs/AI-工作规则.md)。

---

## 和其他剪映自动化方案的区别

同样做"AI 剪映自动化"，[isYangs/jianying-editor-skill](https://github.com/isYangs/jianying-editor-skill)（522★）
是另一个成熟选择。底层都用 pyJianYingDraft 写草稿，**分岔在导出方式和版本策略**：

| | jianying-editor-skill | 本项目 |
|---|---|---|
| 形态 | Skill（给 Claude Code / Trae 等 IDE agent） | MCP server（豆包 / Claude / Trae / Cursor 通用） |
| 剪映版本 | **5.9 或更低** | **11.x（验证 11.6.0.14552）** |
| 导出 | `uiautomation` 模拟点击 UI | 调 `videoeditor.dll` 无头渲染 |
| 功能广度 | 很广：TTS 配音、字幕对齐、云端曲库、特效搜索、录屏、影视解说 | 窄：建工程 / 导出 / 检查 / 曲库 / 打包 |
| 平台 | Windows + macOS | 仅 Windows |

**怎么选**：只能用老版本剪映、或需要 TTS/录屏/特效搜索 → 用它的；
要新版本剪映 + 接豆包 + 无人值守批量出片 → 用这个。

## 剪映版本为什么这么要紧

这个工具直接读写工程文件、调用剪映的渲染引擎，所以**剪映版本 / jy-draftc 版本 / 草稿结构版本三者必须配套**。
版本对不上会表现为：自检报 DLL 不符、导出入口解析不全、剪映打不开工程、或打开后字幕错位。

装好后**请立刻关掉剪映的自动更新**。
细节与各版本对应关系见 [`docs/剪映版本说明.md`](docs/剪映版本说明.md)。

## 依赖

| 依赖 | 必需 | 说明 |
|---|---|---|
| Windows | ✅ | 依赖剪映的 `videoeditor.dll`，仅 Windows |
| 剪映专业版 | ✅ | 提供草稿格式与渲染引擎，**版本要与 jy-draftc 对齐** |
| [jy-draftc](https://github.com/wenshui330/jy-draftc) | ✅ | 草稿加解密 + 无头导出，按剪映版本选发行包 |
| Python 3.10+ | ✅ | 运行 MCP server 与引擎 |
| ffmpeg | 建议 | 素材探针、抽帧、预处理（不加也能建工程，但少了素材分析） |

安装 MCP 包时会自动装 `pyJianYingDraft`（写剪映草稿的库）。

---

## 常见问题

**Q：连接器提示「运行失败」？**
首次启动初始化慢，去「我的技能 → 连接器」点「重新启动」。还不行就看下一题。

**Q：怎么确认 MCP server 本身没问题？**
```powershell
python examples\selfcheck.py
```
全绿会打印六行 OK。**如果它全绿而豆包连不上，问题一定在连接器配置，不在 server。**

**Q：`jy_doctor` 报找不到剪映？**
`JYC_JIANYING_DIR` 要指向**含 `videoeditor.dll`** 的那一层，形如
`...\JianyingPro\Apps\11.6.0.14552`，不是 `Apps` 本身。

**Q：导出 0 字节一直等？**
路径经过 `cmd /c` 转发被转成乱码。不要用 cmd 包装，直接调用启动器。

**Q：草稿在剪映里看不到？**
本工具只**生成文件**，不改剪映草稿注册表（剪映启动时会覆盖手工改的注册表）。
正确做法：把草稿文件夹复制到剪映「全局设置 → 草稿位置」目录下，重启剪映。

**Q：剪映能打开但元素错乱？**
草稿结构版本不匹配。`jy_build` 的 `structuralVersion` 默认 `189.0.0`（对应剪映 11.x），
版本不同要改，或者换对应版本的剪映。

**Q：发给别人的工程打不开、素材丢失？**
用 `jy_share` 打包。剪映草稿只记录素材**绝对路径**，只发工程目录对方一定找不到素材。

**Q：`pip install` 卡住不动？**
在某些代理环境下 pip 解析旧的 numpy 元数据会卡很久。加参数重试：
```powershell
pip install --no-cache-dir --retries 1 --timeout 30 jianying-edit-mcp
```
或先 `pip install -U pip` 再装。

**Q：能跨版本用吗？**
不能保证。剪映升级会改草稿结构与加密，jy-draftc 是按版本适配的。
**关掉剪映自动更新**是最省心的做法。

---

## 能力边界

**能做**：多轨时间线、片段截取与变速、关键帧、转场、特效滤镜动画、文本样式/花字、
词级字幕、配乐淡入淡出、无头导出、工程打包。

**不能做（要人在剪映里补）**：曲线变速、复合片段、部分新版特效、精细调色手感。

**AI 也不该替你做的**：审美与节奏拍板、文案定稿、要不要旁白、哪句该删。
这些让它给 2~3 个候选，你来选。

---

## 致谢

- [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) —— 剪映草稿生成的基石
- [jy-draftc](https://github.com/wenshui330/jy-draftc) —— 高版本草稿加解密与无头导出
- [Model Context Protocol](https://modelcontextprotocol.io/) —— 让这套能力能被任何 AI 调用

## License

MIT
