# -*- coding: utf-8 -*-
"""cover.py —— 封面选帧：从视频里挑出最适合当封面的候选帧。

评分维度（都是可解释的规则，不依赖模型）：
  - 清晰度：Laplacian 方差（越高越锐，糊帧淘汰）
  - 曝光：亮度直方图居中程度（避免过曝/死黑）
  - 对比度：灰度标准差
  - 人脸：有正脸加分（可选，需 opencv 的 haar 级联）
  - 居中：主体靠中间加分（用图像梯度重心粗估）

用法（供 engine.py 调用）：
    pick_covers(video, count=3, out_dir=...) -> list[dict]
"""
import os
import subprocess

CANDIDATE_FPS = 2.0  # 每秒取 2 帧作为候选


def _ffmpeg_exe():
    return os.environ.get("DSH_FFMPEG_PATH") or "ffmpeg"


def _probe_duration(path):
    try:
        out = subprocess.run(
            [_ffmpeg_exe().replace("ffmpeg", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        return float((out.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _extract_candidates(video, work_dir):
    """按 CANDIDATE_FPS 抽帧到 work_dir，返回文件列表。"""
    os.makedirs(work_dir, exist_ok=True)
    pattern = os.path.join(work_dir, "cand-%04d.jpg")
    # 限制宽度，评分不需要原分辨率
    cmd = [
        _ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-i", video, "-vf", f"fps={CANDIDATE_FPS},scale=640:-2",
        "-q:v", "3", pattern, "-y",
    ]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=900)
    return sorted(
        os.path.join(work_dir, f) for f in os.listdir(work_dir) if f.startswith("cand-")
    )


def _imread_unicode(path):
    """cv2.imread 在 Windows 上不支持非 ASCII 路径，用 imdecode 绕开。"""
    import cv2
    import numpy as np

    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None


def score_frame(path, face_cascade=None):
    """给单帧打分，返回 (总分, 各维度明细)。opencv 不可用时返回 None。"""
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001
        return None

    img = _imread_unicode(path)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # 清晰度：Laplacian 方差，归一化到 0~1（>500 基本算锐）
    sharp = min(float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 500.0, 1.0)

    # 曝光：平均亮度离 128 越近越好（±60 内线性衰减）
    mean = float(np.mean(gray))
    exposure = max(0.0, 1.0 - abs(mean - 128.0) / 90.0)

    # 对比度：标准差 / 64
    contrast = min(float(np.std(gray)) / 64.0, 1.0)

    # 人脸：有脸加分（Haar 检测，只做加分项，不做否决）
    face_bonus = 0.0
    face_count = 0
    if face_cascade is not None:
        faces = face_cascade.detectMultiScale(
            cv2.resize(gray, (w // 2, h // 2)), scaleFactor=1.1, minNeighbors=5, minSize=(24, 24)
        )
        if len(faces) > 0:
            face_count = len(faces)
            face_bonus = 0.15

    # 居中：梯度能量重心离画面中心的距离，越居中越高
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    total = float(mag.sum()) or 1.0
    ys, xs = np.indices(mag.shape)
    cx = float((mag * xs).sum() / total) / w
    cy = float((mag * ys).sum() / total) / h
    center = max(0.0, 1.0 - (((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5) / 0.7)

    total_score = (
        sharp * 0.40 + exposure * 0.20 + contrast * 0.20 + center * 0.20 + face_bonus
    )
    return {
        "score": round(total_score, 4),
        "sharpness": round(sharp, 3),
        "exposure": round(exposure, 3),
        "contrast": round(contrast, 3),
        "center": round(center, 3),
        "faces": face_count,
        "meanBrightness": round(mean, 1),
    }


def pick_covers(video, count=3, out_dir=None, work_dir=None, min_gap_sec=1.0):
    """从视频里挑 count 张封面候选，导出 PNG 到 out_dir。"""
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(video)), "covers")
    work_dir = work_dir or os.path.join(os.path.dirname(out_dir), ".cover-candidates")
    os.makedirs(out_dir, exist_ok=True)

    cands = _extract_candidates(video, work_dir)
    if not cands:
        return {"ok": False, "error": "抽帧失败（检查 ffmpeg 是否可用、视频是否可解码）"}

    try:
        import cv2
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        if cascade.empty():
            cascade = None
    except Exception:  # noqa: BLE001
        return {
            "ok": False,
            "error": "封面选帧需要 opencv：pip install opencv-python",
        }

    scored = []
    for i, c in enumerate(cands):
        t = i / CANDIDATE_FPS
        s = score_frame(c, cascade)
        if s is None:
            continue
        s["time"] = round(t, 2)
        s["candidate"] = c
        scored.append(s)

    if not scored:
        return {"ok": False, "error": "没有可评分的帧"}

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 结果之间至少间隔 min_gap_sec，避免选出相邻的同一镜头
    picked = []
    for s in scored:
        if all(abs(s["time"] - p["time"]) >= min_gap_sec for p in picked):
            picked.append(s)
        if len(picked) >= count:
            break

    results = []
    for rank, s in enumerate(picked, 1):
        dst = os.path.join(out_dir, f"cover-{rank}-t{s['time']:.1f}s.png")
        # 用原分辨率导出这一帧（候选是缩略图，这里重新抽）
        subprocess.run(
            [_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
             "-ss", str(s["time"]), "-i", video, "-frames:v", "1", dst, "-y"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
        item = {k: v for k, v in s.items() if k != "candidate"}
        item["file"] = dst if os.path.isfile(dst) else None
        results.append(item)

    # 清理候选缩略图
    try:
        for c in cands:
            os.remove(c)
        os.rmdir(work_dir)
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "video": video,
        "duration": _probe_duration(video),
        "scanned": len(scored),
        "covers": results,
        "outDir": out_dir,
        "warning": (
            "规则评分只做粗筛（清晰度/曝光/对比度/构图居中），"
            "**判断不了构图好坏**（例如人物背影挡住画面仍可能得高分）。"
            "请用视觉能力逐张看 covers 里的图，再决定用哪张。"
        ),
    }
