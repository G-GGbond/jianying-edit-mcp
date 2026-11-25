# -*- coding: utf-8 -*-
"""narration.py —— 口播解说：按时间轴生成配音并适配到指定时长。

核心问题：TTS 生成出来多长不由你决定，但片子的时间槽是固定的。
做法：生成 → 量实际时长 → 用 atempo 变速塞进时间槽（保持音调）→ 校验。

三个 TTS 后端：
  edge        微软神经网络语音（免费、中文自然、需联网）—— 默认
  voicestudio VoiceStudio / OmniVoice（本地/GPU 机器，可克隆音色、可控语气）
  sapi        Windows 自带（机器感强，仅作离线兜底）

用法（供 engine.py 调用）：
    generate_narration(lines, work_dir, backend=...) -> {lines:[...], problems:[...]}
"""
import json
import os
import subprocess

MAX_ATEMPO = 1.5   # 最多加速 50%，超过说明文案太长，要改稿
MIN_ATEMPO = 0.7   # 最慢减速到 70%


def _ffmpeg():
    return os.environ.get("DSH_FFMPEG_PATH") or "ffmpeg"


def probe_duration(path):
    """读音频/视频时长（秒）。"""
    try:
        out = subprocess.run(
            [_ffmpeg().replace("ffmpeg", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        return float((out.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _run(cmd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


# ---------------------------------------------------------------- SAPI 后端
def _sapi_tts(text, out_wav, voice=None, rate=0):
    """用 Windows 自带 SAPI 合成（COM 方式）。

    两个坑：System.Speech 在子进程里常报"未安装语音"，必须用 COM；
    COM 又需要 -STA 线程模型，否则 Speak() 抛 0x80045040。
    """
    import base64

    if os.path.isfile(out_wav):
        os.remove(out_wav)

    pick_voice = ""
    if voice:
        pick_voice = (
            f'$want = "{voice}"; '
            'foreach ($x in $v.GetVoices()) { '
            'if ($x.GetDescription() -like "*$want*") { $v.Voice = $x; break } }'
        )

    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    ps = f'''
$ErrorActionPreference = "Stop"
$txt = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("{b64}"))
$v = New-Object -ComObject SAPI.SpVoice
$v.Rate = {rate}
{pick_voice}
$fs = New-Object -ComObject SAPI.SpFileStream
$fs.Open("{out_wav}", 3, $false)
$v.AudioOutputStream = $fs
$v.Speak($txt)
$fs.Close()
'''
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if not os.path.isfile(out_wav) or os.path.getsize(out_wav) < 1000:
        raise RuntimeError(f"SAPI 合成失败：{(proc.stderr or '')[-400:]}")
    return out_wav


# ---------------------------------------------------------------- edge-tts 后端
def _edge_tts(text, out_mp3, voice="zh-CN-YunyangNeural", rate="+0%", volume="+0%"):
    """微软神经网络语音（edge-tts）。免费、中文自然，输出 mp3。"""
    try:
        import asyncio

        import edge_tts
    except ImportError as exc:
        raise RuntimeError("需要 edge-tts：pip install edge-tts") from exc

    if os.path.isfile(out_mp3):
        os.remove(out_mp3)

    async def _go():
        comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
        await comm.save(out_mp3)

    asyncio.run(_go())
    if not os.path.isfile(out_mp3) or os.path.getsize(out_mp3) < 1000:
        raise RuntimeError(f"edge-tts 合成失败（检查网络与 voice 名）：{voice}")
    return out_mp3


# ---------------------------------------------------------------- VoiceStudio 后端
def _voicestudio_tts(text, out_wav, base_url="http://127.0.0.1:3900", engine=None,
                     language="zh", speed=1.0, ref_audio=None, ref_text=None,
                     instruct=None, emotion=None, emo_text=None, preset_voice=None,
                     api_key=None, timeout=1800):
    """调用 VoiceStudio 的 POST /generate，支持音色克隆/音色设计/语气控制。"""
    from .voice_studio import VoiceStudioClient

    client = VoiceStudioClient(base_url, api_key=api_key, timeout=timeout)
    path, _meta = client.generate(
        text, out_wav, language=language, engine=engine,
        ref_audio=ref_audio, ref_text=ref_text, instruct=instruct,
        speed=speed, emotion=emotion, emo_text=emo_text,
        preset_voice=preset_voice,
    )
    return path


# ---------------------------------------------------------------- 时长适配
def fit_to_slot(src_wav, target_sec, out_wav):
    """把音频适配到目标时长：优先变速（保音调），不足用静音补齐。"""
    orig = probe_duration(src_wav)
    if orig <= 0:
        raise RuntimeError(f"读不到音频时长：{src_wav}")

    mode = "none"
    atempo_used = 1.0
    if orig > target_sec:
        factor = orig / target_sec
        if factor > MAX_ATEMPO:
            factor = MAX_ATEMPO
            mode = "too_long"
        else:
            mode = "speedup"
        atempo_used = round(factor, 4)
        _run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", src_wav,
              "-af", f"atempo={atempo_used}", "-ar", "44100", "-ac", "2",
              out_wav, "-y"])
    elif orig < target_sec - 0.05:
        pad = target_sec - orig
        _run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", src_wav,
              "-af", f"apad=pad_dur={pad:.3f}", "-ar", "44100", "-ac", "2",
              "-t", f"{target_sec:.3f}", out_wav, "-y"])
        mode = "pad"
    else:
        _run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", src_wav,
              "-ar", "44100", "-ac", "2", out_wav, "-y"])

    return {
        "mode": mode,
        "atempo": atempo_used,
        "original": round(orig, 3),
        "target": round(target_sec, 3),
        "fitted": round(probe_duration(out_wav), 3),
    }


def concat_with_silence(clips, out_wav, sample_rate=44100):
    """把多个音频按顺序拼接（每段已是目标时长，直接接）。"""
    if not clips:
        raise RuntimeError("没有可拼接的音频")
    list_file = out_wav + ".list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c).replace(chr(92), '/')}'\n")
    _run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "concat",
          "-safe", "0", "-i", list_file, "-ar", str(sample_rate), "-ac", "2",
          out_wav, "-y"])
    try:
        os.remove(list_file)
    except Exception:  # noqa: BLE001
        pass
    return out_wav


# ---------------------------------------------------------------- 主流程
def generate_narration(lines, work_dir, backend="edge", voice=None, rate=0,
                       rate_str="+0%", voicestudio_url="http://127.0.0.1:3900",
                       engine=None, language="zh", keep_raw=True,
                       ref_audio=None, ref_text=None, default_direction=None,
                       auto_rate_fit=False, api_key=None, timeout=1800):
    """按时间轴逐句生成配音，并适配到每句的时间槽。

    lines: [{"start": 0.0, "end": 4.0, "text": "...", "direction": "语气描述"}]

    语气控制（仅 voicestudio）：
      default_direction  全局语气，如「沉稳有力的男声，语速适中」
      line["direction"]  单句覆盖，如「开场明亮」
      先调 /tools/direction 转成 instruct 提示词，再传给 /generate。

    音色自定义（仅 voicestudio）：
      ref_audio + ref_text  音色克隆
      或只给 direction      音色设计

    auto_rate_fit: 文案超槽时先让服务端 /tools/rate-fit 改写压缩，再合成。
    """
    os.makedirs(work_dir, exist_ok=True)
    raw_dir = os.path.join(work_dir, "raw")
    fit_dir = os.path.join(work_dir, "fitted")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(fit_dir, exist_ok=True)

    client = None
    if backend == "voicestudio":
        from .voice_studio import VoiceStudioClient

        client = VoiceStudioClient(voicestudio_url, api_key=api_key, timeout=timeout)
        if not client.reachable():
            raise RuntimeError(
                f"连不上 VoiceStudio（{voicestudio_url}）。\n"
                "请先启动 VoiceStudio 桌面版；跨机器调用时把 URL 换成 GPU 那台的 IP，"
                "并确认已开启共享或在服务端配置了 OMNIVOICE_API_KEY。"
            )

    results = []
    fitted_files = []
    for i, line in enumerate(lines, 1):
        text = (line.get("text") or "").strip()
        if not text:
            continue
        start = float(line.get("start", 0.0))
        end = float(line.get("end", start + 3.0))
        slot = round(end - start, 3)
        ext = "mp3" if backend == "edge" else "wav"
        raw = os.path.join(raw_dir, f"line{i:02d}.{ext}")
        fitted = os.path.join(fit_dir, f"line{i:02d}.wav")
        notes = []

        # 文案压槽（可选）：让服务端改写
        if auto_rate_fit and client is not None:
            fitr = client.rate_fit(text, slot, target_lang=language,
                                   source_text=line.get("textSource"))
            if isinstance(fitr, dict):
                for key in ("text", "fitted_text", "result", "output"):
                    v = fitr.get(key)
                    if isinstance(v, str) and v.strip():
                        if v.strip() != text:
                            notes.append(f"文案压缩 {len(text)}→{len(v.strip())} 字")
                            text = v.strip()
                        break

        # 语气：direction → instruct
        instruct = None
        direction = line.get("direction") or default_direction
        if client is not None and direction:
            d = client.parse_direction(direction)
            if isinstance(d, dict):
                instruct = d.get("instruct_prompt") or None
                if instruct:
                    notes.append(f"语气「{direction}」")

        if backend == "sapi":
            _sapi_tts(text, raw, voice=voice, rate=rate)
        elif backend == "edge":
            _edge_tts(text, raw, voice=voice or "zh-CN-YunyangNeural",
                      rate=rate_str or "+0%")
        elif backend == "voicestudio":
            _voicestudio_tts(
                text, raw, base_url=voicestudio_url, engine=engine, language=language,
                ref_audio=ref_audio, ref_text=ref_text, instruct=instruct,
                api_key=api_key, timeout=timeout,
            )
        else:
            raise ValueError(f"未知 TTS 后端：{backend}")

        fit = fit_to_slot(raw, slot, fitted)
        fitted_files.append(fitted)
        results.append({
            "index": i, "start": start, "end": end, "slot": slot,
            "text": text, "chars": len(text),
            "charsPerSec": round(len(text) / slot, 2) if slot else 0,
            "instruct": instruct, "notes": notes,
            "raw": raw if keep_raw else None, "file": fitted,
            **fit,
        })

    problems = []
    for r in results:
        if r["mode"] == "too_long":
            problems.append(
                f'第 {r["index"]} 句超时：原长 {r["original"]}s，槽 {r["target"]}s，'
                f'最多只能压到 {MAX_ATEMPO} 倍；建议删字或加时长'
            )
        if r["charsPerSec"] > 6.5:
            problems.append(
                f'第 {r["index"]} 句 {r["charsPerSec"]} 字/秒偏快（中文舒适区 4~5.5）'
            )

    return {
        "ok": True, "backend": backend, "lines": results, "problems": problems,
        "totalSlot": round(sum(r["slot"] for r in results), 3),
        "fittedFiles": fitted_files,
    }
