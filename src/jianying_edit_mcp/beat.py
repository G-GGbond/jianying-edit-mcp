# -*- coding: utf-8 -*-
"""beat.py —— BGM 卡点：分析音乐节拍，把镜头切换点吸附到节拍上。

纯 numpy 实现（不需要 librosa）：
  1. ffmpeg 解码成 22050Hz 单声道
  2. 计算 onset strength（短时能量的正向差分）
  3. 自相关估计 BPM
  4. 生成节拍网格，把镜头切点吸附到最近节拍

用法（供 engine.py 调用）：
    analyze_beats(music, seconds=None) -> {bpm, beats:[...], ...}
    snap_durations(durations, beats)    -> 吸附后的时长列表
"""
import os
import subprocess
import wave

SR = 22050
HOP = 512          # 帧移（样本）
WIN = 2048         # 窗长


def _ffmpeg_exe():
    return os.environ.get("DSH_FFMPEG_PATH") or "ffmpeg"


def decode_mono(path, seconds=None, work_dir=None):
    """把音频解码成单声道 wav，返回采样数据（numpy float32）。"""
    try:
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("卡点分析需要 numpy：pip install numpy") from exc

    work_dir = work_dir or os.path.dirname(os.path.abspath(path))
    os.makedirs(work_dir, exist_ok=True)
    wav = os.path.join(work_dir, ".beat-src.wav")
    cmd = [_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", path]
    if seconds:
        cmd = [_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
               "-t", str(seconds), "-i", path]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", wav, "-y"]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=600)
    if not os.path.isfile(wav):
        raise RuntimeError("音频解码失败（检查 ffmpeg 与输入文件）")
    with wave.open(wav, "rb") as f:
        frames = f.readframes(f.getnframes())
    try:
        os.remove(wav)
    except Exception:  # noqa: BLE001
        pass
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def onset_strength(samples):
    """短时能量的正向差分，作为 onset 强度曲线。"""
    import numpy as np

    n_frames = 1 + max(0, (len(samples) - WIN) // HOP)
    if n_frames < 8:
        return np.zeros(8, dtype=np.float32), np.zeros(8, dtype=np.float32)
    energy = np.zeros(n_frames, dtype=np.float32)
    idx = np.arange(WIN)
    window = np.hanning(WIN).astype(np.float32)
    for i in range(n_frames):
        seg = samples[i * HOP: i * HOP + WIN]
        energy[i] = float(np.sum((seg * window) ** 2))
    energy = np.log1p(energy * 1000.0)
    onset = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
    times = np.arange(n_frames) * HOP / SR
    return onset, times


def estimate_bpm(onset, min_bpm=60, max_bpm=180):
    """自相关估计 BPM。"""
    import numpy as np

    x = onset - onset.mean()
    if not np.any(x):
        return 100.0, 0.0
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    ac = ac / (ac[0] + 1e-9)
    frame_rate = SR / HOP  # onset 帧率
    lag_min = int(60.0 / max_bpm * frame_rate)
    lag_max = min(int(60.0 / min_bpm * frame_rate), len(ac) - 1)
    if lag_max <= lag_min:
        return 100.0, 0.0
    seg = ac[lag_min:lag_max]
    best = int(np.argmax(seg)) + lag_min
    bpm = 60.0 * frame_rate / best if best > 0 else 100.0
    return round(float(bpm), 1), round(float(ac[best]), 3)


def analyze_beats(music, seconds=None, work_dir=None):
    """分析音乐节拍，返回 BPM、节拍时间点、时长。"""
    import numpy as np

    samples = decode_mono(music, seconds=seconds, work_dir=work_dir)
    duration = len(samples) / SR
    onset, times = onset_strength(samples)
    bpm, confidence = estimate_bpm(onset)

    # 用 onset 峰值精修节拍相位：取能量最大的峰作为第一个强拍
    period = 60.0 / bpm if bpm > 0 else 0.6
    if len(onset) > 0:
        strong = int(np.argmax(onset))
        phase = float(times[strong])
    else:
        phase = 0.0
    # 从相位往前推，覆盖整段
    first = phase - (phase // period) * period
    beats = []
    t = first
    while t < duration:
        if t >= 0:
            beats.append(round(t, 3))
        t += period

    return {
        "ok": True,
        "music": music,
        "duration": round(duration, 2),
        "bpm": bpm,
        "beatInterval": round(period, 3),
        "confidence": confidence,
        "beats": beats,
        "note": "confidence 低于 0.1 说明节拍不明显（如氛围音），卡点效果有限",
    }


def snap_durations(durations, bpm, start=0.0, tolerance=0.35):
    """把每段时长吸附到节拍网格：切换点落在最近的节拍上。

    durations: 原始时长列表（秒）
    tolerance: 允许调整的最大比例（0.35 = 最多改 35%，防止画面被截太多）
    """
    period = 60.0 / bpm if bpm > 0 else 0.0
    if period <= 0:
        return list(durations), []

    snapped = []
    cursor = start
    detail = []
    for dur in durations:
        target = cursor + dur
        n = max(1, round((target - start) / period))
        beat_time = start + n * period
        new_dur = beat_time - cursor
        if new_dur < 0.3 or abs(new_dur - dur) > dur * tolerance:
            new_dur = dur  # 偏差太大就保持原样，避免画面被硬拉
            adjusted = False
            beat_time = cursor + new_dur
        else:
            adjusted = True
        snapped.append(round(new_dur, 3))
        detail.append({
            "original": round(dur, 3),
            "snapped": round(new_dur, 3),
            "delta": round(new_dur - dur, 3),
            "adjusted": adjusted,
            "atBeat": round((beat_time - start) / period, 2),
        })
        cursor = beat_time
    return snapped, detail
