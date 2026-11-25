# -*- coding: utf-8 -*-
"""jyc.py —— 剪映 AI 剪辑引擎 CLI（插件与 skill 共用的唯一入口）。

子命令：
  doctor   环境体检：Python 依赖 / ffmpeg / 剪映安装与版本 / jy-draftc / 草稿位置
  build    EDL(json) → 剪映草稿（明文，可选加密为剪映可读格式）
  render   无头导出 MP4（调用剪映自己的渲染引擎）
  inspect  解密草稿并输出结构摘要（轨道/片段/时长），供二次修改前查看
  share    打包分享包：工程 + 素材 + 说明 → zip

所有子命令输出一行 JSON（stdout），日志走 stderr，便于宿主解析。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

FFMPEG_HINTS = ("ffmpeg", "ffprobe")
JY_APP = os.path.join(os.environ.get("LOCALAPPDATA", ""), "JianyingPro", "Apps")
JY_DRAFT_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "JianyingPro", "User Data", "Projects", "com.lveditor.draft"
)

# ---------------------------------------------------------------- 云端曲库
_HERE = os.path.dirname(os.path.abspath(__file__))
CLOUD_MUSIC_FILE = os.path.join(_HERE, "cloud_music.json")
_cloud_lib = None


def cloud_music_lib():
    """加载剪映云端曲库（615 首，内置数据）。剪映打开草稿时按 music_id 在线解析。"""
    global _cloud_lib
    if _cloud_lib is None:
        try:
            # utf-8-sig：容忍 Windows 工具写入的 BOM
            with open(CLOUD_MUSIC_FILE, encoding="utf-8-sig") as f:
                _cloud_lib = json.load(f)
        except Exception as exc:  # noqa: BLE001
            log(f"云端曲库加载失败：{exc}")
            _cloud_lib = []
    return _cloud_lib


def search_cloud_music(keyword, limit=20):
    """按标题关键词模糊搜索云端曲库（忽略大小写与空格）。"""
    lib = cloud_music_lib()
    if not keyword or not keyword.strip():
        return lib[:limit]
    norm = keyword.lower().replace(" ", "").replace("_", "")
    hits = []
    for item in lib:
        title = item["title"]
        if norm in title.lower().replace(" ", "").replace("_", ""):
            hits.append(item)
            if len(hits) >= limit:
                break
    return hits


def find_cloud_music(keyword):
    """按关键词找一首：优先完全匹配标题，其次第一个命中。"""
    lib = cloud_music_lib()
    norm = keyword.lower().replace(" ", "").replace("_", "")
    for item in lib:
        if item["title"].lower().replace(" ", "").replace("_", "") == norm:
            return item
    hits = search_cloud_music(keyword, limit=1)
    return hits[0] if hits else None


def emit(obj):
    """输出结果（单行 JSON）。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(msg):
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- doctor
def find_jianying():
    """返回 (版本列表, 选中的安装目录, videoeditor.dll 路径)。"""
    versions = []
    if os.path.isdir(JY_APP):
        for name in os.listdir(JY_APP):
            d = os.path.join(JY_APP, name)
            dll = os.path.join(d, "videoeditor.dll")
            if os.path.isfile(dll):
                versions.append(name)
    versions.sort(reverse=True)
    if not versions:
        return [], None, None
    pick = versions[0]
    d = os.path.join(JY_APP, pick)
    return versions, d, os.path.join(d, "videoeditor.dll")


def run_proc(cmd, timeout):
    """统一跑外部程序：强制 UTF-8 且容错解码。

    Windows 上 jy-draftc / 剪映引擎的输出可能是 UTF-8 或 GBK，
    默认按系统编码（GBK）解码会在中文路径上抛 UnicodeDecodeError，
    进而吞掉 stderr 与返回码，必须显式指定并允许坏字节替换。
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def detect_jy_draftc():
    """自动找 jy-draftc.exe。

    查找顺序（不假设任何盘符）：
      1. 环境变量 JYC_JY_DRAFTC
      2. 包目录旁边的 tools/
      3. 当前工作目录下的 tools/
      4. PATH 上的 jy-draftc
      5. 用户主目录 ~/.jy-draftc/
    找不到就是找不到——由 jy_doctor 明确报错并提示怎么配，不猜路径。
    """
    env = os.environ.get("JYC_JY_DRAFTC", "").strip().strip('"')
    if env and os.path.isfile(env):
        return env

    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(here), "tools", "jy-draftc.exe"),   # repo 根/tools
        os.path.join(os.path.dirname(os.path.dirname(here)), "tools", "jy-draftc.exe"),
        os.path.join(os.getcwd(), "tools", "jy-draftc.exe"),             # 当前工作目录
        os.path.join(os.getcwd(), "jy-draftc.exe"),
        os.path.expanduser(os.path.join("~", ".jy-draftc", "jy-draftc.exe")),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path

    return shutil.which("jy-draftc")


def cmd_doctor(args):
    report = {"ok": True, "problems": [], "hints": []}

    # Python 依赖
    try:
        import pyJianYingDraft  # noqa: F401
        report["pyJianYingDraft"] = getattr(pyJianYingDraft, "__version__", "installed")
    except Exception as exc:  # noqa: BLE001
        report["pyJianYingDraft"] = None
        report["ok"] = False
        report["problems"].append(f"缺少 pyJianYingDraft: {exc}")
        report["hints"].append("pip install pyJianYingDraft")

    # ffmpeg
    ff = shutil.which("ffmpeg")
    report["ffmpeg"] = ff
    if not ff:
        report["problems"].append("PATH 上找不到 ffmpeg")
        report["hints"].append("安装 ffmpeg 并加入 PATH")

    # 剪映
    versions, jy_dir, dll = find_jianying()
    report["jianyingVersions"] = versions
    report["jianyingDir"] = jy_dir
    report["videoeditorDll"] = dll
    if not versions:
        report["ok"] = False
        report["problems"].append("没找到剪映专业版（缺 videoeditor.dll）")
        report["hints"].append("winget install --id ByteDance.JianyingPro --exact")
    report["draftRoot"] = JY_DRAFT_ROOT if os.path.isdir(JY_DRAFT_ROOT) else None

    # jy-draftc：加密与无头导出必需
    jd = args.jy_draftc or detect_jy_draftc()
    report["jyDraftc"] = jd
    if not jd:
        report["ok"] = False
        report["problems"].append("没找到 jy-draftc.exe（加密与无头导出必需）")
        report["hints"].append(
            "下载与剪映版本匹配的 jy-draftc：https://github.com/wenshui330/jy-draftc/releases"
        )
        report["hints"].append(
            "把 jy-draftc.exe 放到「当前工作目录/tools/」或「本包目录/tools/」，"
            "或放在 PATH 上，或设环境变量 JYC_JY_DRAFTC 指向它（任选一个）"
        )
    else:
        jd_version = None
        if jy_dir:
            try:
                out = run_proc([jd, "-v", "--jy-dir", jy_dir], 60)
                first = (out.stdout or "").splitlines()
                if first:
                    jd_version = first[0].strip()
            except Exception as exc:  # noqa: BLE001
                report["problems"].append(f"jy-draftc 自检失败: {exc}")
        report["jyDraftcVersion"] = jd_version

    emit(report)


# ---------------------------------------------------------------- build
EDL_REQUIRED = ("canvas", "tracks")


def _load_edl(path):
    with open(path, encoding="utf-8") as f:
        edl = json.load(f)
    for key in EDL_REQUIRED:
        if key not in edl:
            raise ValueError(f"EDL 缺少字段 {key}")
    return edl


def build_cloud_music_material(pyJianYingDraft_mod, music_id, title, duration_s):
    """构造云端音乐素材：草稿里 type=music、path=<cloud://id>，剪映打开时在线解析。"""
    import uuid

    from pyJianYingDraft.local_materials import AudioMaterial

    class CloudMusicMaterial(AudioMaterial):
        def __init__(self):
            self.material_id = uuid.uuid4().hex
            self.material_name = title
            self.music_id = music_id
            self.path = f"<cloud://{music_id}>"
            self.duration = int(round(float(duration_s) * 1_000_000))

        def export_json(self):
            data = super().export_json()
            data["type"] = "music"
            data["music_id"] = self.music_id
            return data

    return CloudMusicMaterial()


def cmd_music(args):
    """搜索/列出剪映云端曲库。"""
    lib = cloud_music_lib()
    if args.id:
        for item in lib:
            if item["musicId"] == args.id:
                emit({"ok": True, "found": item, "total": len(lib)})
                return
        emit({"ok": False, "error": f"曲库里没有 id={args.id}", "total": len(lib)})
        return
    hits = search_cloud_music(args.keyword, limit=args.limit)
    emit({
        "ok": True,
        "keyword": args.keyword,
        "count": len(hits),
        "total": len(lib),
        "items": hits,
        "hint": "在 EDL 的音乐片段里写 {\"cloudMusic\": \"标题片段\"} 或 {\"cloudMusicId\": \"id\"} 即可使用",
    })


def _make_blur_bg(src, inpoint, dur, w, h, outdir, blur, brightness, tag):
    """用 ffmpeg 生成"铺满画布 + 模糊 + 压暗"的背景片段，供竖屏黑边修复用。"""
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, f"{tag}.mp4")
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},gblur=sigma={blur}"
    )
    if brightness:
        vf += f",eq=brightness={brightness}"
    ff = os.environ.get("DSH_FFMPEG_PATH") or "ffmpeg"
    cmd = [
        ff, "-hide_banner", "-loglevel", "error",
        "-ss", str(inpoint), "-t", str(dur), "-i", src,
        "-an", "-vf", vf, "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", out, "-y",
    ]
    run_proc(cmd, 600)
    if not os.path.isfile(out):
        raise RuntimeError(f"背景生成失败：{src}")
    return out


def cmd_cover(args):
    """封面选帧：从视频里挑评分最高的若干帧。"""
    from . import cover as cover_mod

    result = cover_mod.pick_covers(
        args.video,
        count=args.count,
        out_dir=args.out,
        min_gap_sec=args.min_gap,
    )
    emit(result)


def cmd_beat(args):
    """分析音乐节拍；给了镜头时长就顺带算卡点后的时长。"""
    from . import beat as beat_mod

    info = beat_mod.analyze_beats(args.music, seconds=args.seconds)
    if args.durations:
        durs = [float(x) for x in args.durations]
        snapped, detail = beat_mod.snap_durations(durs, info["bpm"])
        info["snappedDurations"] = snapped
        info["snapDetail"] = detail
        info["totalBefore"] = round(sum(durs), 3)
        info["totalAfter"] = round(sum(snapped), 3)
    emit(info)


def cmd_narrate(args):
    """一键配音：读口播脚本 → 逐句合成 → 压到时间槽 → 拼成整轨 → 可选出 EDL。"""
    from . import narration as narr
    from . import pronounce as pron

    with open(args.script, encoding="utf-8") as f:
        script = json.load(f)

    voice_cfg = script.get("voice") or {}
    backend = args.backend or voice_cfg.get("backend") or "edge"
    url = args.url or voice_cfg.get("url") or "http://127.0.0.1:3900"
    ref_audio = args.ref_audio or voice_cfg.get("refAudio")
    ref_text = args.ref_text or voice_cfg.get("refText")
    direction = args.direction or script.get("defaultDirection") or voice_cfg.get("direction")
    engine_name = args.engine or voice_cfg.get("engine")
    voice_name = args.voice or voice_cfg.get("voice")
    rate_str = args.rate
    auto_fit = bool(args.auto_rate_fit or script.get("autoRateFit"))

    raw_lines = script.get("lines") or []
    if not raw_lines:
        raise SystemExit("脚本里没有 lines")

    lines = []
    for ln in raw_lines:
        text = ln.get("text") or ""
        if not args.no_normalize:
            text = pron.normalize(text)
        lines.append({
            "start": ln.get("start", 0.0),
            "end": ln.get("end", 3.0),
            "text": text,
            "direction": ln.get("direction"),
            "textSource": ln.get("text"),
        })

    result = narr.generate_narration(
        lines, args.out, backend=backend, voice=voice_name, rate_str=rate_str,
        voicestudio_url=url, engine=engine_name,
        ref_audio=ref_audio, ref_text=ref_text, default_direction=direction,
        auto_rate_fit=auto_fit,
    )

    # 拼成整轨并做响度标准化
    full = os.path.join(args.out, "narration-full.wav")
    narr.concat_with_silence(result["fittedFiles"], full)
    normalized = os.path.join(args.out, "narration.wav")
    ff = os.environ.get("DSH_FFMPEG_PATH") or "ffmpeg"
    run_proc([
        ff, "-hide_banner", "-loglevel", "error", "-i", full,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "2",
        normalized, "-y",
    ], 600)

    result["narrationTrack"] = normalized if os.path.isfile(normalized) else full
    result["duration"] = round(narr.probe_duration(result["narrationTrack"]), 3)

    # 可选：直接生成 EDL（解说轨 + 字幕轨），供 jy_build 用
    if args.edl:
        edl = {
            "name": script.get("name") or "narration",
            "canvas": script.get("canvas") or {"width": 1920, "height": 1080, "fps": 30},
            "tracks": [{
                "type": "audio", "name": "解说", "start": 0,
                "clips": [{"src": result["narrationTrack"], "in": 0,
                           "duration": round(result["duration"] + 0.2, 2)}],
            }],
        }
        if args.subtitles:
            edl["tracks"].append({
                "type": "text", "name": "解说字幕",
                "texts": [{
                    "start": r["start"], "duration": round(r["slot"] - 0.1, 2),
                    "text": r["text"], "style": {"size": 6, "transformY": -0.78},
                } for r in result["lines"]],
            })
        with open(args.edl, "w", encoding="utf-8") as f:
            json.dump(edl, f, ensure_ascii=False, indent=2)
        result["edl"] = args.edl

    emit(result)


def cmd_build(args):
    import pyJianYingDraft as draft
    from pyJianYingDraft import ClipSettings, TextStyle, TrackSpec, TrackType, trange_seconds

    edl = _load_edl(args.edl)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    name = edl.get("name") or "untitled"
    canvas = edl["canvas"]
    w, h = int(canvas.get("width", 1920)), int(canvas.get("height", 1080))
    fps = int(canvas.get("fps", 30))

    folder = draft.DraftFolder(out_dir)
    target = os.path.join(out_dir, name)
    if os.path.isdir(target) and args.replace:
        shutil.rmtree(target)
    script = folder.create_draft(name, w, h, fps=fps)

    # 轨道顺序：音频在下，视频居中，文本在上
    specs = []
    track_map = {}

    # 竖屏黑边修复：为第一个视频轨自动生成"模糊铺满"背景轨，放在最底层
    auto_bg = edl.get("autoBackground")
    if auto_bg:
        main_track = next((t for t in edl["tracks"] if t["type"] == "video"), None)
        if main_track and main_track.get("clips"):
            blur = int(auto_bg.get("blur", 30))
            bright = float(auto_bg.get("brightness", -0.12))
            bg_dir = os.path.join(out_dir, ".background")
            bg_clips = []
            for i, clip in enumerate(main_track["clips"]):
                dur = float(clip["duration"])
                path = _make_blur_bg(
                    clip["src"], float(clip.get("in", 0.0)), dur, w, h,
                    bg_dir, blur, bright, f"bg{i:03d}",
                )
                bg_clips.append({"src": path, "in": 0, "duration": dur})
                log(f"  背景 {i + 1}/{len(main_track['clips'])} 生成完成")
            bg_track = {
                "type": "video",
                "name": auto_bg.get("name") or "模糊背景",
                "clips": bg_clips,
            }
            specs.append(TrackSpec(TrackType.video, bg_track["name"]))
            track_map[bg_track["name"]] = bg_track
            log(f"  已生成模糊背景轨（{len(bg_clips)} 段，blur={blur}）")

    for t in edl["tracks"]:
        kind = t["type"]
        tname = t.get("name") or kind
        specs.append(TrackSpec(getattr(TrackType, kind), tname))
        track_map[tname] = t
    script.append_tracks(specs)

    stats = {"segments": 0, "texts": 0, "duration": 0.0}
    for tname, track in track_map.items():
        kind = track["type"]
        if kind in ("video", "audio"):
            cursor = float(track.get("start", 0.0))
            for clip in track.get("clips", []):
                cloud_key = clip.get("cloudMusic") or clip.get("cloudMusicId")
                src = clip.get("src")
                inpoint = float(clip.get("in", 0.0))
                dur = float(clip["duration"])
                if kind == "video":
                    mat = draft.VideoMaterial(src)
                    avail = mat.duration / 1_000_000 - inpoint
                    if avail < dur:
                        log(f"  裁剪 {os.path.basename(src)}: {dur:.2f}s -> {avail:.2f}s")
                        dur = round(avail - 0.02, 3)
                    if dur <= 0.1:
                        continue
                    seg = draft.VideoSegment(
                        mat,
                        trange_seconds(cursor, duration=dur),
                        source_timerange=trange_seconds(inpoint, duration=dur),
                        volume=float(clip.get("volume", 1.0)),
                    )
                elif cloud_key:
                    # 云端曲库音乐：草稿里只存 music_id，剪映【在界面打开工程时】才按 id 在线解析。
                    # 实测：无头导出（jy-draftc -ex）不会解析云端音乐，导出的片子只有视频原声。
                    # 所以这条路径只适合「生成工程给人到剪映里继续编辑再导出」，
                    # 要让无头出片带上音乐，请改用本地音频文件。
                    key = str(cloud_key)
                    item = next((x for x in cloud_music_lib() if x["musicId"] == key), None)
                    if item is None and not key.isdigit():
                        item = find_cloud_music(key)
                    if item is None:
                        raise ValueError(
                            f"云端曲库找不到「{key}」。用 `jianying-edit music --keyword 关键词` 搜索可用曲目。"
                        )
                    log(
                        f"  注意：云端音乐「{item['title']}」只写入工程；"
                        f"无头导出不会解析它，成片只有原声。需要配乐请用本地音频。"
                    )
                    cloud_mat = build_cloud_music_material(
                        draft, item["musicId"], item["title"], item["duration"]
                    )
                    total_s = cloud_mat.duration / 1_000_000
                    if inpoint + dur > total_s:
                        log(f"  云端曲目「{item['title']}」只有 {total_s:.1f}s，片段裁剪到剩长")
                        dur = max(round(total_s - inpoint - 0.02, 3), 0.5)
                    seg = draft.AudioSegment(
                        cloud_mat,
                        trange_seconds(cursor, duration=dur),
                        source_timerange=trange_seconds(inpoint, duration=dur),
                    )
                    stats.setdefault("cloudMusic", []).append(item["title"])
                    stats["cloudMusicWarning"] = "云端音乐不会被无头导出解析，成片只有原声"
                else:
                    seg = draft.AudioSegment(
                        src,
                        trange_seconds(cursor, duration=dur),
                        source_timerange=trange_seconds(inpoint, duration=dur),
                    )
                if clip.get("fadeIn") or clip.get("fadeOut"):
                    seg.add_fade(f"{clip.get('fadeIn', 0)}s", f"{clip.get('fadeOut', 0)}s")
                if clip.get("volume") is not None and kind == "audio":
                    seg.volume = float(clip["volume"])
                script.add_segment(seg, tname)
                stats["segments"] += 1
                cursor += dur
                stats["duration"] = max(stats["duration"], cursor)
        elif kind == "text":
            for card in track.get("texts", []):
                start = float(card["start"])
                dur = float(card["duration"])
                style = card.get("style", {})
                seg = draft.TextSegment(
                    card["text"],
                    trange_seconds(start, duration=dur),
                    style=TextStyle(
                        size=float(style.get("size", 8.0)),
                        color=tuple(style.get("color", (1.0, 1.0, 1.0))),
                        align=int(style.get("align", 1)),
                    ),
                    clip_settings=ClipSettings(transform_y=float(style.get("transformY", -0.7))),
                )
                script.add_segment(seg, tname)
                stats["texts"] += 1

    script.save()
    plain_dir = target

    result = {
        "ok": True,
        "draft": plain_dir,
        "name": name,
        "canvas": f"{w}x{h}",
        "fps": fps,
        "segments": stats["segments"],
        "texts": stats["texts"],
        "duration": round(stats["duration"], 3),
        "encrypted": False,
    }
    for extra in ("cloudMusic", "cloudMusicWarning"):
        if extra in stats:
            result[extra] = stats[extra]

    # 加密成剪映可读的草稿结构（需要 jy-draftc + 剪映）
    if args.jy_draftc and args.jianying_dir:
        enc_dir = os.path.join(out_dir, name + "_剪映可读")
        if os.path.isdir(enc_dir):
            shutil.rmtree(enc_dir)
        version = args.structural_version or "189.0.0"
        cmd = [
            args.jy_draftc, "-ug", plain_dir, "-t", version, "--encrypted",
            "-o", enc_dir, "--jy-dir", args.jianying_dir,
        ]
        out = run_proc(cmd, 600)
        if out.returncode == 0:
            result["encrypted"] = True
            result["draftForJianying"] = enc_dir
        else:
            result["ok"] = False
            result["encryptError"] = (out.stdout or out.stderr)[-800:]
    emit(result)


# ---------------------------------------------------------------- render
def cmd_render(args):
    if not args.jy_draftc:
        raise SystemExit("需要 --jy-draftc 指定 jy-draftc.exe")
    if not args.jianying_dir:
        raise SystemExit("需要 --jianying-dir 指定含 videoeditor.dll 的剪映目录")
    out_file = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
    cmd = [
        args.jy_draftc, "-ex", os.path.abspath(args.draft), "-o", out_file,
        "--size", args.size, "--fps", str(args.fps),
        "--bitrate", args.bitrate, "--jy-dir", args.jianying_dir,
        "--timeout", str(args.timeout),
    ]
    started = time.time()
    out = run_proc(cmd, args.timeout + 120)
    elapsed = round(time.time() - started, 1)
    ok = out.returncode == 0 and os.path.isfile(out_file) and os.path.getsize(out_file) > 1024
    emit({
        "ok": ok,
        "output": out_file if ok else None,
        "bytes": os.path.getsize(out_file) if os.path.isfile(out_file) else 0,
        "exitCode": out.returncode,
        "seconds": elapsed,
        "tail": None if ok else (out.stdout or out.stderr)[-1200:],
    })


# ---------------------------------------------------------------- inspect
def cmd_inspect(args):
    """解密草稿并输出结构摘要。需要 jy-draftc（剪映加密格式）。"""
    src = os.path.abspath(args.draft)
    # 不用系统 tempdir：DSH 沙箱下的子进程可能访问不到，改用草稿同级目录
    tmp = os.path.join(os.path.dirname(src) or ".", ".jyc-inspect")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        plain = None
        content = os.path.join(src, "draft_content.json")
        raw = b""
        if os.path.isfile(content):
            raw = open(content, "rb").read()
        is_json = raw.lstrip()[:1] == b"{"
        if is_json:
            plain = json.loads(raw.decode("utf-8"))
        else:
            if not args.jy_draftc or not args.jianying_dir:
                raise SystemExit("草稿是加密的，需要 --jy-draftc 与 --jianying-dir 才能解密")
            out = run_proc([args.jy_draftc, "-dc", src, "-o", os.path.join(tmp, "plain"), "--jy-dir", args.jianying_dir], 300)
            if out.returncode != 0:
                raise SystemExit("解密失败: " + (out.stdout or out.stderr)[-600:])
            cand = os.path.join(tmp, "plain")
            for root, _d, files in os.walk(tmp):
                for f in files:
                    if f in ("draft_content.json", "draft_info.json") and "partial" not in root:
                        cand = root
            plain = json.load(open(os.path.join(cand, "draft_content.json"), encoding="utf-8"))

        summary = {
            "ok": True,
            "duration": plain.get("duration"),
            "canvas": plain.get("canvas_config"),
            "tracks": [],
            "materials": {
                "videos": len(plain.get("materials", {}).get("videos", []) or []),
                "audios": len(plain.get("materials", {}).get("audios", []) or []),
                "texts": len(plain.get("materials", {}).get("texts", []) or []),
            },
        }
        for t in plain.get("tracks", []) or []:
            segs = t.get("segments") or []
            summary["tracks"].append({
                "type": t.get("type"),
                "segments": len(segs),
                "clipSeconds": [
                    round((s.get("target_timerange", {}).get("duration", 0) or 0) / 1e6, 2)
                    for s in segs[:50]
                ],
            })
        emit(summary)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- share
def cmd_share(args):
    """把草稿目录 + 引用到的素材 + 说明打包成 zip。"""
    draft_dir = os.path.abspath(args.draft)
    out_zip = os.path.abspath(args.out)
    # 同上：临时区放在输出旁边的隐藏目录，避开沙箱对系统 tempdir 的限制
    staging = os.path.join(os.path.dirname(out_zip) or ".", ".jyc-share")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    pkg = os.path.join(staging, args.name)
    try:
        shutil.copytree(draft_dir, pkg)
        # 找素材：从明文草稿读路径；加密草稿交给 --materials 显式指定
        mats = []
        plain = os.path.join(draft_dir, "draft_content.json")
        raw = open(plain, "rb").read() if os.path.isfile(plain) else b""
        if raw.lstrip()[:1] == b"{":
            data = json.loads(raw.decode("utf-8"))
            for group in ("videos", "audios"):
                for m in data.get("materials", {}).get(group, []) or []:
                    if m.get("path"):
                        mats.append(m["path"])
        for extra in args.materials or []:
            mats.append(extra)
        mats = [m for m in dict.fromkeys(mats) if os.path.isfile(m)]
        if mats:
            mdir = os.path.join(pkg, "materials")
            os.makedirs(mdir, exist_ok=True)
            for m in mats:
                shutil.copy2(m, os.path.join(mdir, os.path.basename(m)))
        with open(os.path.join(pkg, "说明.txt"), "w", encoding="utf-8") as f:
            f.write(args.readme or DEFAULT_README)
        os.makedirs(os.path.dirname(out_zip) or ".", exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _d, files in os.walk(pkg):
                for fn in files:
                    full = os.path.join(root, fn)
                    z.write(full, os.path.relpath(full, staging))
        emit({
            "ok": True,
            "zip": out_zip,
            "bytes": os.path.getsize(out_zip),
            "materials": [os.path.basename(m) for m in mats],
        })
    finally:
        shutil.rmtree(staging, ignore_errors=True)


DEFAULT_README = """剪映 AI 剪辑工程 · 分享包

【内容】
  工程目录/         剪映草稿（draft_content.json 等，剪映加密格式）
  materials/        工程引用的全部素材
  说明.txt          本文件

【怎么用】
  1. 打开剪映 → 全局设置 → 查看「草稿位置」
  2. 把工程目录整个复制到该路径下
  3. 重启剪映，草稿列表里出现该工程
  4. 若提示素材丢失，用「重新链接」指向 materials 目录

【版本】
  草稿结构与剪映版本绑定，对方请使用相同版本的剪映专业版。
"""


def main():
    ap = argparse.ArgumentParser(prog="jyc")
    # 两个全局参数挂到每个子命令上，避免"某子命令不认"的问题
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--jy-draftc")
    common.add_argument("--jianying-dir")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", parents=[common])
    d.set_defaults(func=cmd_doctor)

    b = sub.add_parser("build", parents=[common])
    b.add_argument("--edl", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--structural-version", default="189.0.0")
    b.add_argument("--replace", action="store_true")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("render", parents=[common])
    r.add_argument("--draft", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--size", default="1920x1080")
    r.add_argument("--fps", type=int, default=30)
    r.add_argument("--bitrate", default="12M")
    r.add_argument("--timeout", type=int, default=600)
    r.set_defaults(func=cmd_render)

    i = sub.add_parser("inspect", parents=[common])
    i.add_argument("--draft", required=True)
    i.set_defaults(func=cmd_inspect)

    cv = sub.add_parser("cover", parents=[common])
    cv.add_argument("--video", required=True, help="源视频路径")
    cv.add_argument("--count", type=int, default=3, help="候选数量，默认 3")
    cv.add_argument("--out", default=None, help="输出目录，默认视频同目录 covers/")
    cv.add_argument("--min-gap", type=float, default=1.0, help="候选最小间隔秒数，默认 1.0")
    cv.set_defaults(func=cmd_cover)

    bt = sub.add_parser("beat", parents=[common])
    bt.add_argument("--music", required=True, help="音乐文件路径")
    bt.add_argument("--seconds", type=float, default=None, help="只分析前 N 秒")
    bt.add_argument("--durations", nargs="*", type=float, default=None,
                    help="镜头时长列表（秒）；给了就顺带算卡点结果")
    bt.set_defaults(func=cmd_beat)

    nr = sub.add_parser("narrate", parents=[common])
    nr.add_argument("--script", required=True, help="口播脚本 JSON（含 lines: start/end/text）")
    nr.add_argument("--out", required=True, help="输出目录")
    nr.add_argument("--backend", default=None, choices=["edge", "voicestudio", "sapi"],
                    help="TTS 后端；留空读脚本里的 voice.backend，否则用 edge")
    nr.add_argument("--url", default=None, help="VoiceStudio 地址，如 http://192.168.1.50:3900")
    nr.add_argument("--engine", default=None, help="VoiceStudio 引擎名，如 omnivoice-gguf")
    nr.add_argument("--voice", default=None, help="edge 语音名，如 zh-CN-YunyangNeural")
    nr.add_argument("--rate", default="+0%", help="edge 语速，如 +15%%")
    nr.add_argument("--ref-audio", default=None, help="音色克隆的参考录音（10 秒以上）")
    nr.add_argument("--ref-text", default=None, help="参考录音对应的文字")
    nr.add_argument("--direction", default=None,
                    help="全局语气描述，如「沉稳有力的男声，语速适中」")
    nr.add_argument("--auto-rate-fit", action="store_true",
                    help="文案超槽时让 VoiceStudio 自动改写压缩")
    nr.add_argument("--no-normalize", action="store_true", help="不做数字/型号读法规范化")
    nr.add_argument("--edl", default=None, help="按需输出 EDL（解说轨 + 字幕轨）")
    nr.add_argument("--subtitles", action="store_true", help="EDL 里附带字幕轨")
    nr.set_defaults(func=cmd_narrate)

    m = sub.add_parser("music", parents=[common])
    m.add_argument("--keyword", default="", help="按标题关键词搜索，留空则列出前 N 首")
    m.add_argument("--id", default=None, help="按 music_id 精确查找")
    m.add_argument("--limit", type=int, default=20, help="返回条数上限，默认 20")
    m.set_defaults(func=cmd_music)

    s = sub.add_parser("share", parents=[common])
    s.add_argument("--draft", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--materials", nargs="*")
    s.add_argument("--readme")
    s.set_defaults(func=cmd_share)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
