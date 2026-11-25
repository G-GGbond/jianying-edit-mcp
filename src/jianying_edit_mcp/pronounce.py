# -*- coding: utf-8 -*-
"""pronounce.py —— 中文口播的读法规范化。

TTS 对数字、型号、英文缩写常常念错（2026 念成"两千零二十六"、
T536 念成"T五百三十六"）。这里按规则预先把文本改写成该念的样子。
"""
import re

# 数字逐位读（型号、年份用）
_DIGIT_CN = {
    "0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
    "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
}

# 需要逐字母读的缩写
_SPELL_OUT = [
    "FCU", "NXP", "RK", "RV", "CPU", "GPU", "USB", "AI", "IoT", "PCBA",
    "MCU", "SOC", "DDR", "HDMI", "PCB", "OEM", "ODM", "API", "ARM", "NPU",
]


def _digits_to_cn(digits):
    return "".join(_DIGIT_CN.get(c, c) for c in digits)


def normalize(text, extra_spell=None):
    """把文本改写成 TTS 该念的样子。

    规则（顺序有讲究）：
      1. 四位数年份（1900-2099）→ 逐位读：2026 → 二零二六
      2. 字母+数字型号（T536、RV1126、RK3588）→ 字母 + 数字逐位读
      3. 已知缩写 → 字母间加空格，让 TTS 逐个念
      4. 顿号改成逗号（TTS 对顿号停顿很短）
    """
    if not text:
        return text

    out = text

    # 1) 年份
    def _year(m):
        y = m.group(0)
        if 1900 <= int(y) <= 2099:
            return _digits_to_cn(y)
        return y

    out = re.sub(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", _year, out)

    # 2) 型号：单个大写前缀字母 + 2~4 位数字（T536 / K230 / C906 / A55）
    out = re.sub(
        r"\b([A-Z])(\d{2,4})\b",
        lambda m: m.group(1) + " " + _digits_to_cn(m.group(2)),
        out,
    )
    # 型号：2~3 个字母 + 数字（RK3588 / RV1126 / FET536）
    out = re.sub(
        r"\b([A-Z]{2,3})(\d{3,4})\b",
        lambda m: m.group(1) + " " + _digits_to_cn(m.group(2)),
        out,
    )

    # 3) 缩写逐字母
    spell = list(_SPELL_OUT) + list(extra_spell or [])
    for abbr in sorted(set(spell), key=len, reverse=True):
        out = re.sub(
            rf"\b{re.escape(abbr)}\b",
            " ".join(abbr),
            out,
        )

    # 4) 顿号 → 逗号（更明显的停顿）
    out = out.replace("、", "，")

    # 5) 折叠多余空格
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out


if __name__ == "__main__":
    samples = [
        "2026 上海，光伏与智慧能源展。",
        "这里是飞凌嵌入式 FORLINX 展台。",
        "整墙核心板，覆盖 NXP、Rockchip 等主流平台。",
        "重点看这套 T536 光伏箱变测控方案，从汇流箱到升压变，全链路打通。",
        "桌面上的 FCU 系列，是面向现场的边缘计算终端。",
        "搭载 RK3588 与 RV1126，支持 4x100pin 高速连接器。",
    ]
    for s in samples:
        print(f"原: {s}")
        print(f"读: {normalize(s)}")
        print()
