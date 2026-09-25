---
name: agy-subagent-skill
description: 把前端 / UI 设计与实现任务委派给 agy（Google Antigravity CLI）子代理：组件实现、页面样式/CSS、设计稿或截图转代码、UI 还原与多变体、前端代码探索与对抗式 UI 审阅。也承接通用重活委派（跨文件分析、深搜、文档查询）。不适用于浏览器实时验证、最终集成与验收（这些由主代理完成）。当任务涉及 frontend/UI/component/样式/还原/前端目录时使用。
---

# agy 子代理委派（前端 / UI 优先）

`agy` 是 Google 官方 Antigravity CLI。本 skill 通过无头模式 spawn **官方二进制**把它当子代理：走本机订阅额度，不碰 OAuth token（官方论坛 2026-09-17 背书的合规姿势）。

## 红线（先读）

- **工具权限：默认全放行**（脚本自动传 `--dangerously-skip-permissions`，避免链路被审批打断）。仅对不可信仓库/陌生代码的委派加 `--ask-permissions` 收敛。
- **禁止**读取、存储、转发 agy 的 OAuth 凭据——只 spawn 二进制本身，凭据留在 agy 进程内。
- 订阅额度是共享池（5 小时刷新 + 周上限，按 token 折算）：一次 spawn 冷启动实测 ~1.2 万 input token，**别碎问、别当无限 worker**。

## 认证与代理（重要，实测结论）

- agy 需先完成一次交互式登录（终端**不带参数**运行 `agy`，走浏览器 OAuth）。未登录时无头调用报 `authentication required`。
- **登录必须走临时代理**：不走代理时 OAuth 完成后本地登录态识别不出来。按用户实际终端选写法（代理均不落盘）：

  ```powershell
  # PowerShell：$env: 是进程级变量，仅当前窗口生效，关窗口即失效
  $env:HTTPS_PROXY = "http://127.0.0.1:<代理端口>"
  $env:HTTP_PROXY  = "http://127.0.0.1:<代理端口>"
  agy
  # 登录完成后清理该会话变量：
  Remove-Item Env:HTTPS_PROXY, Env:HTTP_PROXY -ErrorAction SilentlyContinue
  ```

  ```bash
  # Git Bash：POSIX 前缀写法，只作用于这一条命令
  HTTPS_PROXY=http://127.0.0.1:<代理端口> HTTP_PROXY=http://127.0.0.1:<代理端口> agy
  ```

- **禁止固化**：不用 `setx`、不写系统代理设置、不写任何配置文件。PowerShell 的 `$env:` 只活在那一个窗口；agy 登录是交互式 TUI，必须在当前窗口跑（勿用 Start-Process）。`<代理端口>` 为占位符，使用前替换为本机代理的实际监听端口。
- 运行时报网络/认证类错误时同理：按所用终端临时加代理重试（Git Bash 命令前缀 / PowerShell `$env:`），用完清理，不要固化。

## 何时派给 agy

**前端 / UI（主要入口）**：

- 组件实现：规格明确的新组件（React/Vue/HTML+CSS），从设计稿、截图或文字描述转代码
- 页面样式：CSS 修整、响应式适配、主题/暗色模式、过渡动效、布局重构
- UI 变体：按同一设计系统生成 2~3 个方案对比（可并发多实例）
- 前端代码探索：样式考古、组件依赖梳理、超大前端文件/日志分析
- 对抗式 UI 审阅：实现 vs 设计稿逐项找差异

**通用重活（次要入口）**：跨文件/跨目录检索、会产生大量外围材料的阅读、长任务中重新确认模块现状。

**不应分派的场景**：

- 浏览器实时验证与截图（Browser Use 是主代理特权，agy 没有）
- 最终集成、构建、验收、合并决策（子代理产出只是线索与半成品）
- 奠基性文档、即将修改的核心代码定稿
- 任务比派发成本低的（单文件小改、单一事实、一行查询）

## 调用方式

首选辅助脚本（已固化全部护栏：stdin=DEVNULL、--add-dir 工作区注入、UTF-8、软拒绝与限流识别、超时分级终止、响应截断）：

```bash
python "<本 skill 目录>/agy_drive.py" \
  --task "<任务书>" \
  --dir "<目标仓库或前端目录>" \
  --effort high            # 实施/编码 high；纯探索 medium；无 low
  [--model <slug>]         # 不传则用 agy settings 默认；要指定先 `agy models` 查
  #
  # 模型 slug（2026-09-25 实测 `agy models`）：flash 系自带档位，勿再叠加 --effort：
  #   gemini-3.8-flash-high / -medium / -low   ← 前端实施默认 high 档
  #   gemini-3.7-flash-* / gemini-3.6-flash-* / gemini-3.1-pro-{high,low}
  #   claude-sonnet-4-6 / claude-opus-4-6-thinking / gpt-oss-120b-medium（独立限流池，省着用）
  #   --effort 仅用于不带档位的 slug；前台 UI 实施组合 = --model gemini-3.8-flash-high
  [--schema "<json文件或内联schema>"]   # 需要结构化返回时（如 {files:[],notes:[]}）
  [--print-timeout 15m]    # 大任务调大；接近超时说明任务该拆
```

裸命令兜底（脚本不可用时，护栏一个都不能省）：

```bash
agy -p "<任务书>" --add-dir "<dir>" --output-format json \
    --dangerously-skip-permissions --print-timeout 15m   # 无 --cwd flag（实测不存在，官方文档亦未记载）；stdin 用 < /dev/null
```

退出码：0 成功；**3 软拒绝**（SUCCESS 但工具权限被 auto-deny，任务未完成；仅 `--ask-permissions` 或裸命令漏传 flag 时出现）；**29 额度限流**（429/quota，脚本自动判定）；1 status 非 SUCCESS；124 超时；127 启动失败。

**主代理应对协议**：脚本每次运行在输出末尾输出一行 `=== agy_drive RESULT: exit=<码> class=<类别> | 摘要 ===`（stdout/stderr 双写，用户与主代理都必然可见）。读到 `exit=0 class=ok` 之外的 RESULT，**必须**按下方失败处理表处置（重派/降级/请示），不得无视退出码继续推进，也不得在委派失败后默默自己接手重活。

预检（首次使用、或报认证/网络类错误时先跑，不消耗额度）：

```bash
python "<本 skill 目录>/agy_drive.py" --check
```

输出 agy 版本 + 可用模型数 + 认证/网络诊断（含"临时加代理"处置提示）。

## 工具权限策略（已决策全放行，2026-09-25）

- 脚本默认自动审批：写文件、跑命令一次放行，链路不卡。全放行意味着 **agy 可无确认执行任意命令、写任意文件**——因此任务书必须写明边界（禁改哪些文件、自检命令限哪些），这是主要的安全闸门。
- 更强隔离可选：agy 官方 `--sandbox` flag（终端沙箱限制）可叠加；可能卡住部分命令，与"不卡链路"取舍后默认不开。
- 脚本对超长 response 自动截断（默认 50000 字符，`AGY_DRIVE_MAX_CHARS` 可调，保头尾并标注省略量）——防止子代理大输出撑爆宿主上下文；要完整内容让 agy 写入项目文件再由主代理按需读取。
- 收敛手段（仅不可信场景）：脚本加 `--ask-permissions`（恢复 request-review，软拒绝 exit 3），或在 `~/.gemini/antigravity-cli/settings.json` 配 `permissions.allow` 白名单（语法 `action(target)`，如 `write_file(src/)`、`command(regex:npm run (build|lint|test))`）。
- 不要给 agy 开浏览器验证的预期：它自带 browser 系工具，但 UI 最终验证由主代理用 ZCode Browser Use 做。

## 任务书模板（填空后作为 --task）

agy 无头且无状态，任务书必须自包含——它看不到你和用户的对话。**文件路径写相对路径（基于项目根）即可**：脚本自动把 `--dir` 的绝对路径作为项目根注入任务书开头，agy 据此解析相对路径（2026-09-25 实测有效；仅在绕过脚本裸跑时相对路径会落到 agy 内部 scratch，见失败处理表）：

```
目标：<一句话说清要什么>
涉及文件：<相对项目根路径，如 src/App.tsx、styles/theme.css>
上下文：<技术栈与版本、设计系统/tokens 位置、命名与目录规范、禁改文件>
产出：<要创建/修改的文件清单> 或 <要回答的问题清单>
自检：<如"运行 tsc --noEmit 确认无类型错误""组件可独立渲染"；命令超出范围需明示边界>
返回格式：<实施型：改动文件相对路径清单+每文件一句话自述+不确定点；
          探索型：结论+file:line 出处+关键原文>
```

角色设定（拼在任务书开头）：`你是主代理派出的前端子代理，只做委派范围内的实现与核验，不做方案取舍；产出直接喂给主代理复核，附出处，存疑处明确标注。`

**隐私提醒**：任务书与 agy 读取的文件内容都会送 Google 云端处理——**任务书勿粘贴密钥/令牌/个人数据**，敏感凭据文件不要让 agy 读取。

**填好示例（React 组件委派，照此改填）**：

```
目标：实现 PricingCard 组件，按设计稿 docs/pricing-v2.png 还原，支持 dark 模式
涉及文件：src/components/PricingCard.tsx（新建）；src/styles/tokens.css（只读参考）
上下文：React 18 + TS + Tailwind v4；色板一律用 tokens.css 既有变量，勿硬编码色值；禁改 src/App.tsx 与路由文件
产出：PricingCard.tsx 单文件，含 props 类型定义
自检：运行 tsc --noEmit 确认无类型错误；勿执行 npm install / git 操作
返回格式：先输出 git diff --stat 摘要，再列每文件一句话自述，最后列不确定点（如 tokens.css 缺失的设计变量）
```

实施型返回格式建议固定以 `git diff --stat` 开头（全放行下 agy 可跑）——主代理 review 改动从这行开始最省上下文。

## 并发与派发机制

- 最多 **5 个**并发实例；相互独立的任务在同一轮并发派发（多个后台 Bash 调用）。
- 并发会并行烧同一个额度池；前端变体对比（2~3 个）是典型合理并发。
- **并行实例的文件分工必须不相交**（agy 不做文件冲突仲裁），同仓大改用 git worktree 隔离。
- 并发撞凭据锁（stderr 出现 `secret keyring is locked`）是官方文档记录的已知问题：串行重跑失败的实例即可。
- 脚本已默认给子进程注入 `AGY_CLI_DISABLE_AUTO_UPDATE=true`（防并发撞 update.lock、防会话中途版本漂移）。
- 派发后主代理停止其余分析/检索/改文件，等结果返回再动。
- 通常一轮任务书派一个实例；续接用脚本 `--conversation <id>`（来自上次输出的 conversation_id），**勿用 `--continue`**（并发下会串号）。
- **委派翻车时主代理不要默默自己接手重活**（对标 agy-bridge 的 strict 模式）：先按退出码/失败表诊断，缩小任务重派；仍失败则请示用户——默默自己干会让"委派失败"被伪装成成功。
- 主代理职责不变：拿回产出后 review 改动（git diff）、浏览器验证、集成与最终验证由主代理负责。

## 进度查看与长任务（--stream）

**超时与长输出的关系（实测）**：json 模式的 JSON 信封在任务收尾才输出——任务中途超时被杀，**已生成的中间产物全部不可恢复**（RESULT 行会报告捕获量）；stream 模式事件实时落盘，中断不丢已产出部分。因此：

- **短任务（预期 <5 分钟）**：默认 json 模式 + `--print-timeout 15m`。
- **长任务（前端实施、大重构，预期 >5 分钟）**：一律 `--stream` + ZCode Bash 后台运行 + `--print-timeout 0`（agy 官方"等到完成"语义，脚本已正确处理不再限时）——进度可看、中断不丢、链路不卡。
- 拆分优先：能把大任务拆成多个独立子任务时，优先拆分并发派发。

需要看到 agy 行为过程时，用 ZCode Bash 的后台模式跑 stream：

```bash
python ".../agy_drive.py" --task "..." --dir "..." --stream   # run_in_background
```

随后随时读该后台任务的输出文件，事件流逐行出现：

| 事件 | 含义（实测 schema，2026-09-25；事件名与字段以官方文档为准） |
| --- | --- |
| `init` | 会话启动：`init.model`、`init.cwd`、`init.tools[]`（agy 自带 50+ 工具，含浏览器/命令/内部子代理）、`init.permission_mode`（默认 request-review） |
| `step_update` | **进度主体**：`step_type`（`user_input`/`agent_response`/`tool`/`checkpoint`）+ `state`（`ACTIVE` 运行中→`DONE` 完成）+ `text_delta`（增量文本）+ 每步 `usage`/`duration_seconds` |
| `result` | 终态：`status` + `response` + `usage`（整个会话累计） |

看到 `tool` 类型的 `step_update` ACTIVE→DONE 就是 agy 正在执行动作；`agent_response` 的 `text_delta` 是它在写结论。

## 任务中止与退出

- 正常情况 agy 跑完自行退出，无需干预；多轮 stream-json 模式里"关 stdin"是官方优雅退出（供未来热会话模式采用）。
- **单轮 headless 没有官方"内部退出命令"**（无 cancel 子命令，control 通道在输入流中被禁止）——超时/中止时：**POSIX** 先 SIGINT（killpg 广播整组，agy 自行清理）宽限 10s 无响应再 SIGKILL；**Windows** 直接 `taskkill /F /T` 整树硬杀（CTRL_BREAK 依赖共享控制台，ZCode Bash 无控制台、实测不送达；硬杀整树不留残留进程即最安全）。
- 硬杀会连带杀掉 agy 正在执行的命令子进程——这也是任务书写明"自检命令边界"的原因之一。

## 版本兼容策略

agy 迭代快（本机已见自动更新），skill 刻意分两层，避免被版本绑死：

- **稳定契约层（脚本逻辑依赖）**：只使用官方文档主推的公开接口——`-p`、`--output-format json|stream-json`、`--add-dir`、`--print-timeout`、`--conversation`、`--model`、`--effort`、`--json-schema`、`--dangerously-skip-permissions`，以及 JSON 信封字段（status/response/usage/conversation_id/structured_output）。所有参数**显式传值**，不依赖官方默认值（文档与二进制出现过默认值不一致）。
- **启发式增强层（失效自动降级）**：exit 3 软拒绝（特征串 `auto-denied`）、exit 29 限流（`429/RESOURCE_EXHAUSTED/quota`）、--check 的认证诊断，都是对官方错误文案的模式识别。**特征随版本变化时，增强失效但方向不会错**——降级为基础行为（stderr 原样透明化 + 原始退出码），不会把失败误判为成功。
- **模型 slug 表只是快照**（2026-09-25）：模型迭代比 CLI 更快，派发前用 `agy models` 校验目标 slug 存在，或干脆不传 `--model` 走 settings 默认。
- **自愈规则**：实测行为与本文件描述不符时（新版本改了行为），以 stderr/stdout 实际输出为准做处置，并同步更新本 skill 与脚本注释。

## 失败处理

| 症状 | 判定与处置 |
| --- | --- |
| exit 3（脚本判定软拒绝）：stderr 含 `auto-denied`，status=SUCCESS 但无产出 | 工具权限被拦——只在 `--ask-permissions` 或裸命令漏传 flag 时出现。看 stderr 点名的权限类目，改任务书避开或按"工具权限策略"配白名单 |
| status=SUCCESS、exit 0，但目标目录找不到应生成的文件 | 相对路径基准问题——仅裸命令绕过脚本时发生（agy 相对路径落到其内部 scratch `~/.gemini/antigravity-cli/scratch/`）。经脚本派发已自动注入项目根，不会发生；裸跑时须在任务书开头声明项目根绝对路径，或全部用绝对路径 |
| `ERROR` 含 EOF / connection（如 `loadCodeAssist: EOF`） | 代理链路瞬时中断（实测出现过）：原样重试一次即可；连续失败再查本机代理是否存活。**重试要克制**——官方风控曾把"网络不稳大量重连"误判为滥用（2026-09-11 案例） |
| exit 29（脚本判定限流）：error/stderr 含 429/RESOURCE_EXHAUSTED/quota | **不要立刻自动重试**（重试加重限流、继续烧池）：等 ≥5 分钟；并发降档；usage 异常大说明撞的是 5h/周池，等重置 |
| stdout 空 + exit 0 且无信封 | 旧版 agy 的非 TTY 静默空输出 bug（issue #76），先 `agy --version` 排查 |
| `authentication required` | 登录态失效：按上文"认证与代理"重新登录（记得临时代理） |
| duration 逼近 print-timeout | 任务太大：改用 `--stream` + `--print-timeout 0` 后台跑（见"进度查看与长任务"），或拆小并发派发 |
| usage 里 cache_read 长期偏低 | 任务书没复用会话；多轮需求改用 `--conversation` 续接而非反复 spawn |
