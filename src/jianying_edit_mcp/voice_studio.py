# -*- coding: utf-8 -*-
"""voice_studio.py —— VoiceStudio（OmniVoice）本地/远程 API 客户端。

对接三个接口，正好覆盖"一键配音"需要的全部能力：
  POST /tools/direction  自然语言语气描述 → instruct 提示词（语气控制）
  POST /tools/rate-fit   文案压到指定时间槽（脚本适配）
  POST /generate         实际合成（支持音色克隆 ref_audio、音色设计 instruct）

服务默认监听 http://127.0.0.1:3900，回环地址免认证；
跨机器调用 GPU 那台时把 base_url 换成它的 IP 即可（需在它上面开共享或配 API key）。

所有请求只用标准库 urllib，不引入依赖。
"""
import json
import os
import time
import urllib.error
import urllib.request
import uuid


class VoiceStudioError(RuntimeError):
    pass


def _multipart(fields, files=None):
    """构造 multipart/form-data 请求体。files: {name: (filename, bytes)}"""
    boundary = "----jyc" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        if v is None:
            continue
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
            f"{v}\r\n"
        ).encode("utf-8")
    for name, (filename, data) in (files or {}).items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


class VoiceStudioClient:
    def __init__(self, base_url="http://127.0.0.1:3900", api_key=None, timeout=1800):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("VOICESTUDIO_API_KEY")
        self.timeout = timeout

    # ------------------------------------------------------------ 底层
    def _headers(self, extra=None):
        h = dict(extra or {})
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _post_json(self, path, payload, timeout=120):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path, data=data, method="POST",
            headers=self._headers({"Content-Type": "application/json"}),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise VoiceStudioError(f"{path} HTTP {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise VoiceStudioError(
                f"{path} 连不上 {self.base_url}：{exc}\n"
                f"检查：VoiceStudio 是否已启动？跨机器调用请把 URL 换成本机 IP，并确认端口可达。"
            ) from exc

    # ------------------------------------------------------------ 能力
    def reachable(self):
        """探活：拿 OpenAPI 或 health，任一成功即算可达。"""
        for path in ("/openapi.json", "/system/info", "/"):
            try:
                req = urllib.request.Request(self.base_url + path,
                                             headers=self._headers())
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status < 500:
                        return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def engines(self):
        """列出可用 TTS 引擎及其状态。"""
        for path in ("/engines", "/tools/plugins"):
            try:
                req = urllib.request.Request(self.base_url + path,
                                             headers=self._headers())
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                continue
        return None

    def parse_direction(self, text):
        """把"沉稳有力，语速稍慢"这类描述 → instruct 提示词。失败时返回原文。"""
        if not text:
            return None
        try:
            r = self._post_json("/tools/direction", {"text": text}, timeout=60)
            return r
        except VoiceStudioError:
            return None

    def rate_fit(self, text, slot_seconds, target_lang="zh", source_text=None):
        """让服务端把文案压到时间槽（LLM 辅助改写）。失败返回 None，由调用方兜底。"""
        payload = {"text": text, "slot_seconds": float(slot_seconds),
                   "target_lang": target_lang}
        if source_text:
            payload["source_text"] = source_text
        try:
            return self._post_json("/tools/rate-fit", payload, timeout=180)
        except VoiceStudioError:
            return None

    # ------------------------------------------------------------ 合成
    def generate(self, text, out_path, *, language="zh", engine=None,
                 ref_audio=None, ref_text=None, instruct=None,
                 speed=1.0, emotion=None, emo_text=None, emo_alpha=None,
                 preset_voice=None, seed=None):
        """调用 POST /generate 合成一句，写到 out_path。

        返回 (out_path, meta)。ref_audio 给了就是音色克隆，只给 instruct 就是音色设计。
        """
        fields = {"text": text, "language": language, "speed": str(speed)}
        if engine:
            fields["engine"] = engine
        if ref_text:
            fields["ref_text"] = ref_text
        if instruct:
            fields["instruct"] = instruct
        if emotion:
            fields["emotion"] = emotion
        if emo_text:
            fields["emo_text"] = emo_text
        if emo_alpha is not None:
            fields["emo_alpha"] = str(emo_alpha)
        if preset_voice:
            fields["preset_voice"] = preset_voice
        if seed is not None:
            fields["seed"] = str(seed)

        files = {}
        if ref_audio:
            if not os.path.isfile(ref_audio):
                raise VoiceStudioError(f"参考录音不存在：{ref_audio}")
            with open(ref_audio, "rb") as f:
                files["ref_audio"] = (os.path.basename(ref_audio), f.read())

        body, ctype = _multipart(fields, files)
        req = urllib.request.Request(
            self.base_url + "/generate", data=body, method="POST",
            headers=self._headers({"Content-Type": ctype}),
        )
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                rtype = (resp.headers.get("Content-Type") or "").lower()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            raise VoiceStudioError(f"/generate HTTP {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise VoiceStudioError(f"/generate 请求失败：{exc}") from exc
        elapsed = round(time.time() - started, 2)

        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

        # 形态 1：直接返回音频字节
        if raw[:4] == b"RIFF" or "audio" in rtype or "octet-stream" in rtype:
            with open(out_path, "wb") as f:
                f.write(raw)
            return out_path, {"mode": "binary", "seconds": elapsed, "bytes": len(raw)}

        # 形态 2：返回 JSON（可能含 audio_id / 路径 / URL）
        try:
            j = json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            raise VoiceStudioError(
                f"/generate 返回无法解析（{len(raw)} 字节, {rtype}）：{raw[:200]!r}"
            ) from exc

        # 2a. 直接给本地路径
        for key in ("path", "file", "audio_path", "output", "wav", "wav_path"):
            p = j.get(key) if isinstance(j, dict) else None
            if isinstance(p, str) and os.path.isfile(p):
                with open(p, "rb") as src, open(out_path, "wb") as dst:
                    dst.write(src.read())
                return out_path, {"mode": "json-path", "seconds": elapsed,
                                  "source": p, "json": j}

        # 2b. 给 audio_id / url，再取一次
        audio_id = None
        for key in ("audio_id", "id", "audioId", "audio"):
            v = j.get(key) if isinstance(j, dict) else None
            if isinstance(v, str) and v:
                audio_id = v
                break
        candidate_urls = []
        for key in ("url", "audio_url", "download_url"):
            v = j.get(key) if isinstance(j, dict) else None
            if isinstance(v, str) and v:
                candidate_urls.append(v if v.startswith("http") else self.base_url + v)
        if audio_id:
            base = audio_id
            if base.lower().endswith(".wav"):
                base = base[:-4]
            candidate_urls += [
                f"{self.base_url}/audio/{base}.mp3",
                f"{self.base_url}/audio/{base}.ogg",
                f"{self.base_url}/audio/{base}",
            ]
        for url in candidate_urls:
            try:
                get = urllib.request.Request(url, headers=self._headers())
                with urllib.request.urlopen(get, timeout=120) as resp:
                    data = resp.read()
                if len(data) > 1000:
                    with open(out_path, "wb") as f:
                        f.write(data)
                    return out_path, {"mode": "fetched", "seconds": elapsed,
                                      "url": url, "json": j}
            except Exception:  # noqa: BLE001
                continue

        raise VoiceStudioError(
            "无法从 /generate 的返回里拿到音频。原始响应："
            + json.dumps(j, ensure_ascii=False)[:600]
        )
