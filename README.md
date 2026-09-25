# agy-subagent-skill

把 Google Antigravity CLI（`agy`）作为子智能体接入任意 agent 宿主。

![license](https://img.shields.io/badge/license-MIT-green)
![python](https://img.shields.io/badge/python-3.8%2B-blue)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

## 简介

`agy` 提供官方无头模式，适合被程序化调用，但直接把它包成子智能体有一批实际问题：权限工具失败时进程仍返回成功退出码；无头会话的相对路径基准不是工作目录；跨平台的编码和进程终止行为不一致；子任务的长输出容易超出宿主上下文；订阅额度的限流对自动化调用不友好。

这个项目用两个文件解决：`agy_drive.py` 是零依赖执行脚本，把上述问题的处理全部固化（项目根注入、软拒绝与限流识别、输出截断、分级终止等），以退出码和结果摘要行返回结果；`SKILL.md` 说明委派方法和失败处置，供宿主的 skill 系统加载。

适用于 ZCode、Claude Code、Codex 等任何具备命令执行能力的宿主。计算资源来自本机已登录的 Antigravity 订阅。

## 特性

- 只调用官方二进制，不读取/存储/转发 OAuth 凭据，走订阅额度
- 软拒绝（exit 3）与限流（exit 29）独立退出码，宿主无需解析文本
- `--stream` 模式实时转发 agy 事件流，后台运行可随时查看进度
- 超长输出自动截断（默认 50000 字符），防止超出宿主上下文
- 项目根自动注入，任务书直接写相对路径
- 不绑定 agy 版本：只依赖官方文档的稳定接口，参数全部显式传递

## 快速开始

### 环境要求

- [agy](https://antigravity.google/docs/cli/install) 已安装并登录
- Python >= 3.8（纯标准库）
- 宿主具备命令执行能力

### 安装

```bash
git clone <repo>
cd agy-subagent-skill
# ZCode：
mkdir -p ~/.zcode/skills && cp -r . ~/.zcode/skills/agy-subagent-skill
# Claude Code：
# mkdir -p ~/.claude/skills && cp -r . ~/.claude/skills/agy-subagent-skill
python agy_drive.py --check   # 预检：版本/认证/网络，不消耗额度
```

### 使用

```bash
python agy_drive.py \
  --task "在 src/ 下创建 Hello.tsx，实现一个无状态组件" \
  --dir /path/to/your/project
```

任务书直接写相对路径（基于 `--dir`）。预期超过 5 分钟的任务加 `--stream --print-timeout 0` 后台运行，事件流实时落盘。

## 参数

| 参数 | 说明 |
|---|---|
| `--task` | 任务描述（目标、涉及文件、约束、产出、自检） |
| `--dir` | 项目根，相对路径基准 |
| `--model` | 模型 slug，不传则用 agy 默认（`agy models` 查询） |
| `--stream` | 实时转发事件流，配合后台运行查看进度 |
| `--check` | 预检版本/认证/网络，不消耗额度 |
| `--conversation` | 续接指定会话 |
| `--schema` | JSON Schema，约束结构化输出 |
| `--print-timeout` | 单次超时，默认 15m，`0` 不限时 |
| `--ask-permissions` | 关闭默认自动审批 |

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 3 | 软拒绝：工具被权限拦截，任务未完成 |
| 29 | 限流（429 / quota） |
| 1 | 其他失败 |
| 124 | 超时 |
| 127 | agy 无法启动 |

## 已知问题（2026-09 校准）

- 无头会话的相对路径基准是内部 scratch，不是工作目录（执行器已处理）
- 没有 `--cwd` flag，工作目录 = 进程 cwd + `--add-dir`
- `--mode accept-edits` 不放行写文件，无头下写文件需要 `permissions.allow` 或全放行
- Windows 没有可靠的跨进程优雅中断，超时只能整树硬杀
- 工具被权限拦截时 run 照常返回 SUCCESS，必须看 stderr 和退出码
- 每次无头调用固定约 1.2 万 input token 开销，多轮用 `--conversation` 续接

## 注意

- 只 spawn 官方二进制，不读取/存储/转发 OAuth 凭据（官方 ToS 红线）；重度批量会触发限流
- 订阅额度是共享池（短周期刷新 + 周上限），不适合无限并发
- 仅在官方支持地区可用；受限网络下登录与校验需临时配置代理，勿固化到系统
- 任务书与 agy 读取的文件内容会送云端处理，不要包含密钥等敏感数据

## License

MIT
