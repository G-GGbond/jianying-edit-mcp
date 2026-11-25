#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selfcheck.py —— MCP server 自检客户端（模拟真实客户端行为）。

启动 MCP server 子进程，走一遍 initialize → tools/list → 逐个调用工具。
用来区分"是 server 有问题"还是"是客户端连接器配置有问题"。

用法：
    python examples/selfcheck.py

需要先设好环境变量（或让 install.ps1 生成的启动器里已带）：
    JYC_JY_DRAFTC     jy-draftc.exe 路径
    JYC_JIANYING_DIR  含 videoeditor.dll 的剪映目录
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_EDL = os.path.join(HERE, "edl-12s-demo.json")


class McpClient:
    """最小 MCP 客户端（stdio / JSON-RPC 2.0，行分隔）。"""

    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "jianying_edit_mcp.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        self.next_id = 0

    def _rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("server 已退出；stderr=" + self.proc.stderr.read()[-800:])
            line = line.strip()
            if not line:
                continue
            resp = json.loads(line)
            if resp.get("id") == msg["id"]:
                return resp

    def initialize(self):
        return self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "selfcheck", "version": "1"},
        })

    def initialized(self):
        self._rpc("notifications/initialized", {}, notify=True)

    def list_tools(self):
        return self._rpc("tools/list")["result"]["tools"]

    def call(self, name, arguments):
        resp = self._rpc("tools/call", {"name": name, "arguments": arguments})
        result = resp.get("result", {})
        text = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
        if result.get("isError"):
            raise RuntimeError(f"{name} 执行出错：{text}")
        return json.loads(text) if text.strip().startswith("{") else text

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()



def make_test_media(out_dir):
    """用 ffmpeg 造一段 12 秒测试素材 + 一段配乐，自检不依赖用户文件。"""
    import subprocess
    os.makedirs(out_dir, exist_ok=True)
    ff = os.environ.get("DSH_FFMPEG_PATH") or "ffmpeg"
    video = os.path.join(out_dir, "test-clip.mp4")
    music = os.path.join(out_dir, "test-music.wav")
    if not os.path.isfile(video):
        subprocess.run([
            ff, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-shortest", video, "-y",
        ], capture_output=True, timeout=300)
    if not os.path.isfile(music):
        subprocess.run([
            ff, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=12",
            "-ar", "44100", "-ac", "2", music, "-y",
        ], capture_output=True, timeout=120)
    return video, music


def build_test_edl(video, music, path):
    """造一份只用测试素材的 EDL。"""
    edl = {
        "name": "selfcheck",
        "canvas": {"width": 1920, "height": 1080, "fps": 30},
        "tracks": [
            {"type": "audio", "name": "music", "start": 0,
             "clips": [{"src": music, "in": 0, "duration": 8, "fadeIn": 0.4, "fadeOut": 1.0}]},
            {"type": "video", "name": "main", "start": 0,
             "clips": [{"src": video, "in": 0, "duration": 4},
                        {"src": video, "in": 4, "duration": 4}]},
            {"type": "text", "name": "title",
             "texts": [{"start": 0.3, "duration": 3, "text": "Self check",
                         "style": {"size": 9, "transformY": -0.6}}]},
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(edl, f, ensure_ascii=False, indent=2)
    return path


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")

    # 报告自动探测结果，让用户知道 server 找到的是哪个剪映/jy-draftc
    try:
        from jianying_edit_mcp import mcp_server as srv

        print(f"探测：jy-draftc  = {srv.JY_DRAFTC or '（未找到，渲染与加密会失败）'}")
        print(f"探测：剪映目录   = {srv.JIANYING_DIR or '（未找到）'}")
        if not srv.JY_DRAFTC or not srv.JIANYING_DIR:
            print("如需手动指定，设环境变量 JYC_JY_DRAFTC / JYC_JIANYING_DIR")
        print()
    except Exception as exc:  # noqa: BLE001
        print(f"（无法预探测：{exc}）\n")

    out_dir = os.path.join(HERE, "..", "selfcheck-out")
    out_dir = os.path.abspath(out_dir)
    client = McpClient(env)
    failed = 0
    try:
        info = client.initialize()["result"]
        print(f"[1] initialize  OK  {info['serverInfo']['name']} v{info['serverInfo']['version']}  protocol={info['protocolVersion']}")
        client.initialized()

        tools = client.list_tools()
        print(f"[2] tools/list  OK  {len(tools)} 个：" + ", ".join(t["name"] for t in tools))

        doc = client.call("jy_doctor", {})
        flag = "OK" if doc.get("ok") else "FAIL"
        if not doc.get("ok"):
            failed += 1
        print(f"[3] jy_doctor   {flag}  剪映={doc.get('jianyingVersions')} problems={doc.get('problems')}")
        for hint in doc.get("hints") or []:
            print(f"        → {hint}")

        if not doc.get("ok"):
            print("\n环境未就绪，后续步骤跳过。先按上面的 hints 修好再跑。")
            return 1

        video, music = make_test_media(out_dir)
        edl_path = build_test_edl(video, music, os.path.join(out_dir, "selfcheck-edl.json"))
        built = client.call("jy_build", {"edlPath": edl_path, "outDir": out_dir})
        print(f"[4] jy_build    {'OK' if built.get('ok') else 'FAIL'}  segments={built.get('segments')} "
              f"texts={built.get('texts')} duration={built.get('duration')}s encrypted={built.get('encrypted')}")

        enc = built.get("draftForJianying")
        insp = client.call("jy_inspect", {"draftDir": enc})
        tracks = " ".join(f"{t['type']}:{t['segments']}" for t in insp["tracks"])
        print(f"[5] jy_inspect  {'OK' if insp.get('ok') else 'FAIL'}  tracks={tracks}  duration={insp['duration'] / 1e6:.1f}s")

        mp4 = os.path.join(out_dir, "selfcheck.mp4")
        rendered = client.call("jy_render", {"draftDir": enc, "outPath": mp4, "size": "1920x1080"})
        print(f"[6] jy_render   {'OK' if rendered.get('ok') else 'FAIL'}  "
              f"{rendered.get('bytes', 0) / 1024 / 1024:.1f}MB  {rendered.get('seconds')}s")

        print(f"\n全部通过。成片：{mp4}")
        return 0
    except RuntimeError as exc:
        print(f"\n失败：{exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
