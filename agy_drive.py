#!/usr/bin/env python3
"""agy_drive.py — 主代理委派任务给 agy（Google Antigravity CLI）的薄封装。

设计约束：
- 零第三方依赖（仅标准库），Python 3.8+。
- 只 spawn 官方 agy 二进制；不读取、不存储、不转发任何凭据（ToS 红线）。
- 固化全部无头模式护栏：stdin=DEVNULL、--add-dir、--print-timeout、
  UTF-8 编码、软拒绝检测（exit 3）、响应截断防上下文过载、限流识别（exit 29）、
  分级 kill 防孙进程持管道、自动注入项目根（相对路径跟随项目）。

版本兼容策略：
- 逻辑只依赖官方文档的稳定契约：-p、--output-format json|stream-json、--add-dir、
  --print-timeout、--conversation、--model、--effort、--json-schema、
  --dangerously-skip-permissions，以及 JSON 信封字段（status/response/usage/...）。
- 增强（exit 3 软拒绝、exit 29 限流）是启发式叠加：特征文案若随版本失效，自动降级为
  基础行为（stderr 透明化 + 原始退出码），不会误判成败方向。不依赖任何具体版本号。
- 显式传 --print-timeout 等参数，不依赖官方默认值（文档与二进制已出现过默认值不一致）。

用法：
  python agy_drive.py --task "任务描述" --dir <工作目录> [--model <slug>]
                      [--effort medium|high] [--schema <文件或内联schema>]
                      [--print-timeout 15m] [--conversation <id>] [--stream]
                      [--check] [--ask-permissions]

退出码：0 成功；3 软拒绝（工具权限被 auto-deny）；29 额度限流（429/quota）；
1 status 非 SUCCESS；124 超时；127 无法启动 agy；2 用法/解析错误。
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading

# Windows 下重定向管道时 Python 默认用本地编码（cp936），强制 UTF-8 防乱码/崩溃
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def find_agy() -> str:
    """定位官方 agy 二进制：AGY_BIN > PATH > %LOCALAPPDATA%\\agy\\bin。"""
    p = os.environ.get("AGY_BIN")
    if p and os.path.isfile(p):
        return p
    w = shutil.which("agy")
    if w:
        return w
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cand = os.path.join(la, "agy", "bin", "agy.exe")
        if os.path.isfile(cand):
            return cand
    sys.exit("ERROR: 未找到 agy 二进制。请安装 Antigravity CLI 或设置环境变量 AGY_BIN 指向它。")


def parse_timeout(s: str) -> int:
    """'15m'/'90s'/'2h'/'600' → 秒。"""
    s = s.strip().lower()
    try:
        if s.endswith("h"):
            return int(float(s[:-1]) * 3600)
        if s.endswith("m"):
            return int(float(s[:-1]) * 60)
        if s.endswith("s"):
            return int(float(s[:-1]))
        return int(s)
    except ValueError:
        sys.exit(f"ERROR: 无法解析超时值: {s!r}")


def build_cmd(args, agy: str, task: str) -> list:
    cmd = [agy, "-p", task]
    if args.conversation:
        cmd += ["--conversation", args.conversation]
    # 实测（2026-09-25）无 --cwd flag，官方文档亦未记载：工作目录 = 子进程 cwd（Popen 传入）+ --add-dir 加入工作区
    cmd += ["--add-dir", args.dir]
    if not args.ask_permissions:
        # 用户决策（2026-09-25）：默认全放行自动审批，链路优先；
        # 官方 flag，permission_mode 变 always-proceed。敏感任务用 --ask-permissions 收敛。
        cmd += ["--dangerously-skip-permissions"]
    if args.model:
        cmd += ["--model", args.model]
    if args.effort:
        cmd += ["--effort", args.effort]
    if args.schema:
        cmd += ["--json-schema", args.schema]
    cmd += ["--output-format", "stream-json" if args.stream else "json"]
    if args.print_timeout:
        cmd += ["--print-timeout", args.print_timeout]
    return cmd


DEFAULT_MAX_CHARS = 50000  # 对标 agy-bridge 的 50k 截断：防止子代理大输出撑爆宿主上下文
SIGNAL_GRACE_S = 10  # POSIX：SIGINT 后给 agy 自行清理的宽限（单轮 headless 无"内部退出命令"，信号即最温和手段）


def clip(text: str, limit: int) -> str:
    """超长文本保头尾截断，中间标注省略量与全量。"""
    if limit <= 0 or len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return (text[:head]
            + f"\n...[agy_drive: 中间省略 {len(text) - limit} 字符（全量 {len(text)}）；"
              f"需要完整内容时用 --conversation 续接让 agy 分段返回，或调高 AGY_DRIVE_MAX_CHARS]...\n"
            + text[-tail:])


def extract_envelope(stdout: str):
    """从 stdout 提取 JSON 信封（容忍夹带的非 JSON 行）。"""
    if not stdout:
        return None
    try:
        obj = json.loads(stdout)
        if isinstance(obj, dict) and "status" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    found = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "status" in obj:
                    found = obj  # 取最后一个可解析信封
            except json.JSONDecodeError:
                continue
    return found


def emit(code: int, cls: str, msg: str) -> None:
    """结果摘要行：stdout+stderr 双写。宿主与用户在工具输出里必然可见退出码与判定，
    主代理据此按 SKILL.md 失败处理表应对，不需要解析其余文本。"""
    line = f"=== agy_drive RESULT: exit={code} class={cls} | {msg} ==="
    print(line)
    print(line, file=sys.stderr)


def report(env, stderr_text: str, returncode: int) -> int:
    """统一结果输出：状态/usage/响应/结构化输出/stderr 透明化。"""
    if env is None:
        print("=== agy_drive: 未能解析 JSON 信封 ===")
        if returncode == 0:
            print("stdout 为空。若为旧版 agy，这是非 TTY 静默空输出 bug（issue #76），先 `agy --version` 排查。")
        print("=== stderr 原文 ===")
        print(stderr_text or "(空)")
        emit(1, "no-envelope", "stdout 无有效 JSON 信封，见上方原文；常见原因：超时被杀/旧版空输出/认证失败")
        return 1

    print(f"=== status: {env.get('status')} | conversation_id: {env.get('conversation_id')} ===")
    u = env.get("usage") or {}
    if u:
        print(f"=== usage: input={u.get('input_tokens')} output={u.get('output_tokens')} "
              f"thinking={u.get('thinking_tokens')} cache_read={u.get('cache_read_tokens')} "
              f"total={u.get('total_tokens')} | turns={env.get('num_turns')} "
              f"duration={env.get('duration_seconds')}s ===")
    so = env.get("structured_output")
    if so is not None:
        print("=== structured_output ===")
        print(json.dumps(so, ensure_ascii=False, indent=2))
    print("=== response ===")
    try:
        max_chars = int(os.environ.get("AGY_DRIVE_MAX_CHARS", str(DEFAULT_MAX_CHARS)))
    except ValueError:
        max_chars = DEFAULT_MAX_CHARS
    print(clip(env.get("response") or "(空)", max_chars))
    if env.get("error"):
        print("=== error ===")
        print(env["error"])

    # 软拒绝透明化：headless 下工具被权限拦下时 run 照常 SUCCESS、exit 0、只写 stderr。
    # 这里不猜关键词，只要 stderr 非空就原样呈现并提示复核——判定权留给主代理。
    if stderr_text.strip():
        print("=== stderr 原文（注意：可能包含工具被权限拦下而软拒绝的提示，即使 status=SUCCESS 也请复核任务是否真正完成）===")
        print(stderr_text)

    if env.get("status") == "SUCCESS" and returncode == 0:
        # 启发式特征（2026-09-25 实测文案）："...a tool required the ... permission ... so it was
        # auto-denied..."。命中即说明任务未真正完成，用独立退出码 3 让调用方无需解析文本。
        # 特征词可能随版本变化：失效时此处不触发，降级为基础行为（stderr 警告段仍原样呈现），
        # 不会把失败误判为成功。
        if "auto-denied" in stderr_text:
            emit(3, "soft-deny", "工具权限被 auto-deny，任务未完成；看 stderr 点名的权限类目，改任务书或按『工具权限策略』处置")
            return 3
        dur = env.get("duration_seconds")
        emit(0, "ok", f"SUCCESS | usage total={u.get('total_tokens')}"
                      + (f" | duration={dur}s" if dur is not None else "")
                      + (f" | conversation={env.get('conversation_id')}" if env.get("conversation_id") else ""))
        return 0
    # 限流专用退出码 29：不自动重试（重试会加重限流、继续烧共享池），退避决策交给主代理
    err_all = (env.get("error") or "") + stderr_text
    if "429" in err_all or "RESOURCE_EXHAUSTED" in err_all or "quota" in err_all.lower():
        emit(29, "rate-limit", "额度被限（429/quota）。等 ≥5 分钟再重试；并发降档；撞周限只能等重置")
        return 29
    emit(1, "error", f"status={env.get('status')} | {(env.get('error') or '无 error 字段，见 stderr')[:300]}")
    return 1


def make_env() -> dict:
    """子进程环境：禁用 agy 自动更新器——官方排障手段（AGY_CLI_DISABLE_AUTO_UPDATE），
    防并发 spawn 撞 update.lock、防会话中途版本漂移。仅作用于子进程，不写用户环境。"""
    return {**os.environ, "AGY_CLI_DISABLE_AUTO_UPDATE": "true"}


def popen_platform_kwargs() -> dict:
    """让子进程成为新进程组组长：Windows 用 CREATE_NEW_PROCESS_GROUP（CTRL_BREAK 可达整组），
    POSIX 用 start_new_session（setsid，killpg 可达整组）。优雅信号因此天然覆盖 agy 的孙进程。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_graceful(p: subprocess.Popen, grace_s: int) -> str:
    """分级终止：POSIX 先 SIGINT（killpg 广播整组，agy 自行清理=最接近"内部退出"），
    宽限无响应才 SIGKILL；Windows 无可靠的跨进程优雅中断原语——CTRL_BREAK 依赖共享
    控制台，ZCode Bash 等无控制台环境实测不送达（2026-09-25 最小实验证实），故直接
    taskkill /F /T 硬杀整树：不留残留进程（继续烧额度）才是更安全。返回 graceful/killed。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True, timeout=15)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return "killed"
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGINT)
    except (OSError, ProcessLookupError):
        pass
    try:
        p.wait(timeout=grace_s)
        return "graceful"
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            p.kill()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return "killed"


def run_stream(cmd: list, cwd: str) -> int:
    """stream-json 模式：逐行转发事件流到 stdout（供后台 tail 看进度），stderr 收尾打印。"""
    p = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", cwd=cwd, env=make_env(),
        **popen_platform_kwargs(),
    )
    err_lines = []

    def drain_err():
        for line in p.stderr:
            err_lines.append(line)

    t = threading.Thread(target=drain_err, daemon=True)
    t.start()
    for line in p.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    p.wait()
    t.join(timeout=3)
    if err_lines:
        sys.stderr.write("=== agy stderr ===\n" + "".join(err_lines))
        sys.stderr.flush()
    if p.returncode != 0:
        emit(p.returncode or 1, "stream-error",
             f"stream 会话异常退出（exit={p.returncode}）；上方事件流为已产出部分，未完成内容需重派")
    return p.returncode or 0


def main() -> None:
    ap = argparse.ArgumentParser(description="委派任务给 agy 官方 CLI（无头模式）")
    ap.add_argument("--task", default=None, help="自包含任务描述（目标/目录/约束/产出/自检/返回格式）；--check 预检时可省略")
    ap.add_argument("--dir", default=os.getcwd(), help="项目根：作为子进程 cwd 与 --add-dir，并自动注入任务书作为相对路径基准")
    ap.add_argument("--model", default=None, help="模型 slug；不传则用 agy settings 默认。先用 `agy models` 查可用 slug")
    ap.add_argument("--effort", default=None,
                    help="推理深度（如 medium|high）：仅用于不带档位的模型 slug；取值由 agy 自校验，"
                         "脚本不设枚举以便兼容未来新增档位。flash 系 slug 自带档位，勿重复指定")
    ap.add_argument("--ask-permissions", action="store_true",
                    help="关闭默认的自动审批（默认传 --dangerously-skip-permissions 全放行；仅不可信仓库/陌生代码场景使用")
    ap.add_argument("--check", action="store_true",
                    help="预检：agy 可执行性、版本、认证/网络（跑 agy models，不消耗额度）。对应 codex-antigravity-subagent 的 agy_check")
    ap.add_argument("--schema", default=None, help="--json-schema：.json 文件路径或内联 schema 字符串")
    ap.add_argument("--print-timeout", default="15m", help="agy 单次运行超时（默认 15m，显式设置不依赖官方默认值）")
    ap.add_argument("--conversation", default=None, help="续接指定会话 id（并发场景勿用 --continue）")
    ap.add_argument("--stream", action="store_true",
                    help="stream-json 事件流模式：逐行转发进度事件，配合后台运行 tail 输出查看过程")
    args = ap.parse_args()

    if not args.check and not args.task:
        sys.exit("ERROR: --task 必填（--check 预检模式除外）")

    if not os.path.isdir(args.dir):
        sys.exit(f"ERROR: --dir 不存在: {args.dir}")

    if args.check:
        agy = find_agy()
        for label, argv in (("version", [agy, "--version"]), ("models", [agy, "models"])):
            try:
                r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=120)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                print(f"CHECK FAIL [{label}]: {e}", file=sys.stderr)
                sys.exit(127)
            out = (r.stdout or "") + (r.stderr or "")
            if label == "version":
                print(f"agy {out.strip().splitlines()[-1] if out.strip() else '?'}")
                continue
            low = out.lower()
            if r.returncode != 0 or "sign in" in low:
                print("CHECK FAIL [auth/network]: 无法获取模型列表（认证或网络校验未通过）。", file=sys.stderr)
                print("处置：1) 登录态过期→按 SKILL.md『认证与代理』临时加代理重新登录；"
                      "2) 网络校验不通→命令前缀临时加 HTTPS_PROXY=http://<本机代理地址> 后重试；"
                      "3) 或先开 TUN 全局代理。", file=sys.stderr)
                print(out.strip()[:2000], file=sys.stderr)
                sys.exit(1)
            models = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith("Fetching")]
            print(f"CHECK OK: {len(models)} 个可用模型；额度与认证正常。")
            sys.exit(0)

    agy = find_agy()
    # agy headless 的相对路径基准是其内部 scratch 而非 cwd（实测），
    # 故自动把 --dir 绝对路径注入任务书开头作为项目根——任务书用相对路径即可。
    # AGY_DRIVE_NO_INJECT=1 跳过注入（stub 测试用：.bat 经 cmd.exe 转发多行参数会断）。
    if os.environ.get("AGY_DRIVE_NO_INJECT"):
        task = args.task
    else:
        project_root = os.path.abspath(args.dir)
        task = f"[环境约定] 项目根目录：{project_root}。任务书中的相对路径均基于此目录解析，文件操作结果以该目录为准。\n\n{args.task}"
    cmd = build_cmd(args, agy, task)

    if args.stream:
        # 进度可见模式：进程生命周期交由调用方管理（后台运行 + 超时自行终止）
        sys.exit(run_stream(cmd, args.dir))

    try:
        grace = int(os.environ.get("AGY_DRIVE_GRACE", "60"))  # agy 侧 print-timeout 后的收尾余量
        to = parse_timeout(args.print_timeout)
        # 0 = 官方"等到任务完成"语义（不限时）：Python 侧同样不设超时，交由调用方管理
        limit = None if to == 0 else to + grace
        # 不用 subprocess.run(timeout=...)：Windows 下超时 kill 只杀直接子进程，
        # agy 的工具子进程（孙进程）若持有 stdout 管道会让清理阶段永久阻塞。
        # 改为 Popen + 分级 kill：超时杀 agy → 短超时收管道 → 再超时放弃收集、立即退出。
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,  # 非 TTY 下必须显式关闭 stdin，否则可能挂起
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=args.dir,
            env=make_env(),
            **popen_platform_kwargs(),
        )
        try:
            out, err = p.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            # 分级终止：先中断信号让 agy 自行清理（整组广播），无响应才硬杀树
            mode = terminate_graceful(p, SIGNAL_GRACE_S)
            try:
                out, err = p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = "", "(agy 已被终止，但其子进程仍占用输出管道；放弃收集残余输出)"
            # json 模式的信封在任务收尾才写出：中途终止 = 已生成的中间产物不可恢复。
            emit(124, "timeout",
                 f"print-timeout={args.print_timeout} 到期，agy 经"
                 f"{'中断信号优雅退出' if mode == 'graceful' else '强制终止'}；本次仅捕获 stdout "
                 f"{len(out or '')} 字符，json 信封未完整写出、中间产物不可恢复——"
                 f"长任务请改 --stream + 后台运行 + --print-timeout 0，或拆小任务/调大超时")
            sys.exit(124)
    except FileNotFoundError as e:
        emit(127, "launch-fail", f"无法启动 agy: {e}")
        sys.exit(127)

    env = extract_envelope(out or "")
    sys.exit(report(env, err or "", p.returncode))


if __name__ == "__main__":
    main()
