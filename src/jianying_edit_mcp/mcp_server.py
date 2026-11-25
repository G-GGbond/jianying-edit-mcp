#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP stdio server —— 把剪映 AI 剪辑引擎暴露成标准 MCP 工具。

零依赖：只用标准库 + 本包的 engine.py。任何支持 MCP 的客户端都能接：
  豆包工作（自定义连接器 · STDIO）、Claude Desktop、Trae、Cursor、Codex…

通信方式：stdin/stdout，每行一条 JSON-RPC 2.0 消息（MCP stdio transport）。
日志一律写 stderr —— 写 stdout 会破坏协议。

配置（全部可选，不填会自动探测）：
  JYC_PYTHON       引擎使用的 Python 解释器（默认当前解释器）
  JYC_JY_DRAFTC    jy-draftc.exe 路径；不填则找 tools/ 目录、PATH、常见位置
  JYC_JIANYING_DIR 含 videoeditor.dll 的剪映目录；不填则自动取版本号最大的那个
"""

import json
import os
import subprocess
import sys
import traceback

SERVER_NAME = "jianying-edit"
PROTOCOL_VERSION = "2024-11-05"

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "engine.py")

try:  # 版本号跟随包
    from . import __version__ as SERVER_VERSION
except Exception:  # noqa: BLE001
    SERVER_VERSION = "0.3.0"


def detect_jianying_dir():
    """自动找含 videoeditor.dll 的剪映目录（取版本号最大的）。

    剪映装在 %LOCALAPPDATA%\\JianyingPro\\Apps\\<版本>\\ 下，
    这是官方安装器决定的，不是我们猜的路径。也可用 JYC_JIANYING_DIR 覆盖。
    """
    env = os.environ.get("JYC_JIANYING_DIR", "").strip().strip('"')
    if env and os.path.isfile(os.path.join(env, "videoeditor.dll")):
        return env
    apps = os.path.join(os.environ.get("LOCALAPPDATA", ""), "JianyingPro", "Apps")
    if not os.path.isdir(apps):
        return None
    cands = []
    for name in os.listdir(apps):
        d = os.path.join(apps, name)
        if os.path.isfile(os.path.join(d, "videoeditor.dll")):
            cands.append(name)
    if not cands:
        return None
    cands.sort(reverse=True)
    return os.path.join(apps, cands[0])


def detect_jy_draftc():
    """jy-draftc 的探测复用 engine 里的实现，避免两份逻辑走偏。"""
    try:
        from .engine import detect_jy_draftc as _detect

        return _detect()
    except Exception:  # noqa: BLE001
        env = os.environ.get("JYC_JY_DRAFTC", "").strip().strip('"')
        return env if env and os.path.isfile(env) else None


# 环境变量优先；没配就自动探测，让用户少填两项
JY_DRAFTC = os.environ.get("JYC_JY_DRAFTC") or detect_jy_draftc()
JIANYING_DIR = os.environ.get("JYC_JIANYING_DIR") or detect_jianying_dir()


def log(msg):
    sys.stderr.write(f"[{SERVER_NAME}] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- 工具定义
TOOLS = [
    {
        "name": "jy_doctor",
        "description": (
            "剪映 AI 剪辑环境体检：检查 Python 依赖（pyJianYingDraft）、ffmpeg、"
            "剪映专业版安装与版本、jy-draftc、草稿位置。返回 problems/hints 说明缺什么、怎么修。"
            "开始任何剪辑工作前先跑一次。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "jy_build",
        "description": (
            "把剪辑方案（EDL JSON）写成剪映草稿工程。EDL 描述画布、轨道与片段（视频/音频/文本）。"
            "同时输出明文草稿（可读可 diff）与剪映可读草稿（加密）。"
            "文字必须分轨：主标题一条轨、副标题另一条轨，否则同轨时间重叠会失败。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "edlPath": {"type": "string", "description": "剪辑方案 JSON 文件路径（必填）。"},
                "outDir": {"type": "string", "description": "草稿输出目录（必填）。"},
                "structuralVersion": {"type": "string", "description": "草稿结构版本，默认 189.0.0。"},
            },
            "required": ["edlPath", "outDir"],
        },
    },
    {
        "name": "jy_render",
        "description": (
            "无头导出：不打开剪映界面，直接调剪映自己的渲染引擎把草稿渲染成 MP4。"
            "30 秒成片约 10 余秒。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draftDir": {"type": "string", "description": "剪映可读草稿目录（必填）。"},
                "outPath": {"type": "string", "description": "输出 MP4 路径（必填）。"},
                "size": {"type": "string", "description": "分辨率，如 1920x1080 / 1080x1920。"},
                "fps": {"type": "number", "description": "帧率，默认 30。"},
                "bitrate": {"type": "string", "description": "码率，如 12M。"},
                "timeoutSeconds": {"type": "number", "description": "渲染超时秒数，默认 600。"},
            },
            "required": ["draftDir", "outPath"],
        },
    },
    {
        "name": "jy_inspect",
        "description": (
            "读取剪映草稿的结构摘要（时长、画布、每条轨道的片段数与各片段时长、素材数量）。"
            "加密草稿会自动解密后读取。修改别人的工程前先看一眼。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"draftDir": {"type": "string", "description": "草稿目录（必填）。"}},
            "required": ["draftDir"],
        },
    },
    {
        "name": "jy_narrate",
        "description": (
            "一键口播配音：读剧本 JSON（每句含 start/end/text/可选 direction），"
            "逐句合成、压到时间槽、拼成整轨，可按需输出 EDL。"
            "支持 edge（免费联网）/ voicestudio（可克隆音色+语气控制）/ sapi。"
            "语气用自然语言写在 direction 里，如「沉稳男声，纪录片旁白感」。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scriptPath": {"type": "string", "description": "口播脚本 JSON 路径（必填）。"},
                "outDir": {"type": "string", "description": "输出目录（必填）。"},
                "backend": {"type": "string", "enum": ["edge", "voicestudio", "sapi"], "description": "TTS 后端。"},
                "url": {"type": "string", "description": "VoiceStudio 地址，如 http://192.168.1.50:3900。"},
                "refAudio": {"type": "string", "description": "音色克隆的参考录音路径。"},
                "refText": {"type": "string", "description": "参考录音对应的原文。"},
                "direction": {"type": "string", "description": "全局语气描述。"},
                "autoRateFit": {"type": "boolean", "description": "文案超时自动压缩。"},
                "edlPath": {"type": "string", "description": "输出 EDL 的路径（可选）。"},
                "subtitles": {"type": "boolean", "description": "EDL 附带字幕轨。"},
            },
            "required": ["scriptPath", "outDir"],
        },
    },
    {
        "name": "jy_music",
        "description": (
            "搜索剪映云端曲库（内置 615 首，含中英文）。"
            "注意：云端音乐写进工程后，在剪映界面打开时才会在线解析；"
            "**无头导出（jy_render）不会解析，成片只有原声**。"
            "要让成片带配乐，请改用本地音频文件。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "标题关键词，如 科技 / 企业 / 轻快；留空则列出前若干首。"},
                "musicId": {"type": "string", "description": "按 music_id 精确查找。"},
                "limit": {"type": "number", "description": "返回条数上限，默认 20。"},
            },
        },
    },    {
        "name": "jy_cover",
        "description": (
            "从视频里挑封面候选帧（按清晰度/曝光/对比度/构图打分，扫描全程取最优几帧）。"
            "注意：规则评分只能粗筛，判断不了构图好坏（人物背影挡镜头也可能得高分）。"
            "拿到候选图后必须用视觉能力逐张看，再决定用哪张。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "video": {"type": "string", "description": "源视频路径（必填）。"},
                "count": {"type": "number", "description": "候选数量，默认 5。"},
                "outDir": {"type": "string", "description": "输出目录，默认视频同目录 covers/。"},
            },
            "required": ["video"],
        },
    },
    {
        "name": "jy_beat",
        "description": (
            "分析音乐节拍（BPM + 每拍时间点），并可按节拍吸附镜头时长做卡点。"
            "给 durations 就返回吸附后的时长；不给只返回节拍信息。"
            "纯 numpy 实现，不需要 librosa。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "music": {"type": "string", "description": "音乐文件路径（必填）。"},
                "seconds": {"type": "number", "description": "只分析前 N 秒。"},
                "durations": {
                    "type": "array", "items": {"type": "number"},
                    "description": "镜头时长列表（秒）；给了就顺带算卡点后的时长。",
                },
            },
            "required": ["music"],
        },
    },
    {
        "name": "jy_share",
        "description": (
            "把草稿打包成可分享的 zip：工程目录 + 它引用的全部素材 + 使用说明。"
            "剪映草稿只记录素材绝对路径，不打包素材对方一定打开失败。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draftDir": {"type": "string", "description": "草稿目录（必填）。"},
                "outPath": {"type": "string", "description": "输出 zip 路径（必填）。"},
                "name": {"type": "string", "description": "包内目录名（必填）。"},
            },
            "required": ["draftDir", "outPath", "name"],
        },
    },
]


# ---------------------------------------------------------------- 引擎调用
def call_engine(subcommand, params):
    """跑 engine.py 子命令，返回解析后的 dict。"""
    python = os.environ.get("JYC_PYTHON") or sys.executable
    argv = [python, ENGINE, subcommand]
    for key, value in params.items():
        if value is None:
            continue
        flag = "--" + "".join(
            "-" + c.lower() if c.isupper() else c for c in key
        ).lstrip("-")
        argv.extend([flag, str(value)])
    jy_draftc = JY_DRAFTC
    jianying_dir = JIANYING_DIR
    if jy_draftc:
        argv.extend(["--jy-draftc", jy_draftc])
    if jianying_dir:
        argv.extend(["--jianying-dir", jianying_dir])

    log("exec: " + " ".join(argv[:4]) + " ...")
    # Windows 上子进程输出可能是 UTF-8/GBK 混合，必须容错，否则解码异常会吞掉结果
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        env=env,
    )
    if proc.stderr:
        log(proc.stderr.strip()[-1500:])
    lines = [line for line in (proc.stdout or "").strip().splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError(
        f"引擎没有返回可解析结果（exit={proc.returncode}）。stderr 尾部："
        + (proc.stderr or "")[-800:]
    )


def tool_jy_doctor(args):
    return call_engine("doctor", {})


def tool_jy_build(args):
    params = {"edl": args["edlPath"], "out": args["outDir"]}
    if args.get("structuralVersion"):
        params["structural-version"] = args["structuralVersion"]
    return call_engine("build", params)


def tool_jy_render(args):
    params = {"draft": args["draftDir"], "out": args["outPath"]}
    for src, dst in (("size", "size"), ("fps", "fps"), ("bitrate", "bitrate"), ("timeoutSeconds", "timeout")):
        if args.get(src) is not None:
            params[dst] = args[src]
    return call_engine("render", params)


def tool_jy_inspect(args):
    return call_engine("inspect", {"draft": args["draftDir"]})


def tool_jy_narrate(args):
    params = {"script": args["scriptPath"], "out": args["outDir"]}
    for src_k, dst_k in (("backend", "backend"), ("url", "url"), ("refAudio", "ref-audio"),
                         ("refText", "ref-text"), ("direction", "direction"),
                         ("edlPath", "edl")):
        if args.get(src_k):
            params[dst_k] = args[src_k]
    if args.get("autoRateFit"):
        params["auto-rate-fit"] = True
    if args.get("subtitles"):
        params["subtitles"] = True
    return call_engine("narrate", params)

def tool_jy_music(args):
    params = {}
    if args.get("keyword"):
        params["keyword"] = args["keyword"]
    if args.get("musicId"):
        params["id"] = args["musicId"]
    if args.get("limit"):
        params["limit"] = args["limit"]
    return call_engine("music", params)


def tool_jy_cover(args):
    params = {"video": args["video"]}
    if args.get("count"):
        params["count"] = args["count"]
    if args.get("outDir"):
        params["out"] = args["outDir"]
    return call_engine("cover", params)


def tool_jy_beat(args):
    params = {"music": args["music"]}
    if args.get("seconds"):
        params["seconds"] = args["seconds"]
    if args.get("durations"):
        params["durations"] = [str(d) for d in args["durations"]]
    return call_engine("beat", params)


def tool_jy_share(args):
    return call_engine("share", {"draft": args["draftDir"], "out": args["outPath"], "name": args["name"]})


HANDLERS = {
    "jy_doctor": tool_jy_doctor,
    "jy_build": tool_jy_build,
    "jy_render": tool_jy_render,
    "jy_inspect": tool_jy_inspect,
    "jy_narrate": tool_jy_narrate,
    "jy_music": tool_jy_music,
    "jy_cover": tool_jy_cover,
    "jy_beat": tool_jy_beat,
    "jy_share": tool_jy_share,
}


# ---------------------------------------------------------------- JSON-RPC
def respond(msg_id, result):
    send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def respond_error(msg_id, code, message):
    send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(request):
    method = request.get("method")
    msg_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        respond(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return

    if method in ("notifications/initialized", "initialized"):
        return  # 通知无需响应

    if method == "ping":
        respond(msg_id, {})
        return

    if method == "tools/list":
        respond(msg_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            respond_error(msg_id, -32602, f"未知工具: {name}")
            return
        try:
            result = handler(arguments)
            respond(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                "isError": False,
            })
        except Exception as exc:  # noqa: BLE001
            log(traceback.format_exc())
            respond(msg_id, {"content": [{"type": "text", "text": f"执行失败：{exc}"}], "isError": True})
        return

    if method in ("resources/list", "prompts/list"):
        respond(msg_id, {"resources": []} if method.startswith("resources") else {"prompts": []})
        return

    if msg_id is not None:
        respond_error(msg_id, -32601, f"不支持的方法: {method}")


def main():
    log(f"started (engine={ENGINE})")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            log("跳过非 JSON 行")
            continue
        if isinstance(request, list):  # 批量请求
            for item in request:
                handle(item)
        else:
            handle(request)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
