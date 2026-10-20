#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析芒果TV播放 API 响应（getSource），识别请求清晰度并推断是否会员。

输入：芒果TV播放接口 JSON（pcweb.api.mgtv.com/player/getSource 等的 data.stream）
      或 m3u8 URL/文本。
输出：
  - 实际可用最高清晰度（stream 码 + 名称 + 分辨率）
  - 会员判定：达到 超清1080P 及以上 → 疑似会员
    （依据芒果官方会员特权：1080P/蓝光/4K 为 VIP 专享，免费用户通常只到高清）
  - 降级识别：声明档位高于实际返回档位 → 鉴权降级（非会员特征）

字段依据（公开解析项目 + 第三方文档通行映射，需抓包校准）：
  data.stream 为数组，元素是按 流码 → 流信息 的 dict，流码形如 h1~h4
  h1  标清 360-540P  免费
  h2  高清 720P      免费（部分片源）
  h3  超清 1080P     会员
  h4  蓝光/4K 1080P-2160P  会员

注意：接口为私有协议、版本变化快，请用真机抓包校验对照表。
"""

import json
import re
import sys
from urllib.parse import urlparse, parse_qs


# 芒果 流码 → (权重, 名称, 分辨率, 是否会员档)
STREAM_TYPES = {
    # 芒果流码（新版接口 data.stream 的 key）
    "h1": (1, "标清", "360P-540P", False),
    "h2": (2, "高清", "720P",      False),
    "h3": (3, "超清", "1080P",     True),
    "h4": (4, "蓝光/4K", "1080P-2160P", True),
    # 数值码（旧接口 / 第三方解析文档：360P(0) 540P(1) 720P(2) 1080P(3)）
    "0": (1, "标清", "360P",       False),
    "1": (1, "标清", "540P",       False),
    "2": (2, "高清", "720P",       False),
    "3": (3, "超清", "1080P",      True),
    # 别称（部分解析工具出现过的命名）
    "hd": (3, "超清", "1080P",     True),
    "shd": (3, "超清", "1080P",    True),
    "fd": (4, "蓝光", "1080P",     True),
}

MEMBER_THRESHOLD_WEIGHT = 3  # 权重 >= 3（超清 1080P 起）视为会员档


def _lookup(code):
    if not isinstance(code, str):
        return None
    key = code.strip().lower()
    return STREAM_TYPES.get(key)


def _extract_stream_dict(stream_container) -> dict:
    """把芒果 data.stream 的各种形态归一成 {流码: 流信息} 字典。

    形态1: {"h2": {...}, "h1": {...}}          的元素
    形态2: [{"h1": {...}}, {"h2": {...}}]      的 list
    形态3: {"h1": {...}}                        直接为 dict
    """
    result = {}
    items = stream_container
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                if any(_lookup(k) is not None for k in item.keys()):
                    # 元素本身就是 {流码: 流信息}
                    result.update(item)
                else:
                    # 元素是流信息本体，需要从内部字段找流码
                    merged = _merge_stream_item(item)
                    if merged:
                        result.update(merged)
    elif isinstance(items, dict):
        if any(_lookup(k) is not None for k in items.keys()):
            result.update(items)
        else:
            result.update(_merge_stream_item(items))
    return result


# 流信息里的可读字段：有些接口直接返回中文清晰度名
_DEF_TEXT_KEYS = ("definition", "definition_name", "name", "desc", "title")

# 中文名 → 权重（用于按文本字段兜底）
_TEXT_QUALITY = {
    "蓝光": 4, "4k": 4, "超清": 3, "hd": 3, "高清": 2, "标清": 1, "流畅": 1, "普通": 1,
}


def _merge_stream_item(item: dict) -> dict:
    """当流信息不带流码 key 时，用文本字段推断档位，构造 {档名: 流信息}。"""
    merged = {}
    for k in _DEF_TEXT_KEYS:
        text = str(item.get(k) or "")
        if not text:
            continue
        if text in STREAM_TYPES:
            merged[text] = item
        else:
            # 尝试匹配中文档位名
            for cn, w in _TEXT_QUALITY.items():
                if cn in text.lower() or cn in text:
                    merged[cn] = item
                    break
    return merged


def parse_play_info(text: str, requested: str = None) -> dict:
    """从芒果 getSource JSON 提取清晰度信息。

    参数：
        - text: JSON 文本
        - requested: 播放器声明/请求的档位（可选，用于降级判断）
    返回：
        - requested 声明档
        - available 实际可用流（按权重降序）
        - highest_available 最高可用档
        - is_member: 最高可用档 >= 会员阈值
        - downgraded
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"不是有效的 JSON: {e}")

    payload = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(payload, dict):
        raise ValueError("data 不是对象")

    stream_container = payload.get("stream")
    if stream_container is None:
        # 容错：字段可能是 streams / stream_info
        stream_container = payload.get("streams") or payload.get("stream_info")
    if stream_container is None:
        raise ValueError("未找到 stream 字段")

    all_streams = _extract_stream_dict(stream_container)

    available = sorted(
        ((k, _lookup(k)) for k, v in all_streams.items() if _lookup(k)),
        key=lambda x: x[1][0],
        reverse=True,
    )

    best = available[0] if available else None
    best_code, best_meta = best if best else (None, None)

    # 声明档解析（优先参数，其次 payload 里的声明字段；仅接受标量字符串）
    declared_code = requested
    if not declared_code and isinstance(payload, dict):
        for key in ("requested_stream", "default_stream", "stream"):
            val = payload.get(key)
            if isinstance(val, str) and val:
                declared_code = val
                break
    declared_meta = _lookup(declared_code) if declared_code else None

    downgraded = False
    if best_meta and declared_meta:
        downgraded = declared_meta[0] > best_meta[0]

    is_member = bool(best_meta and best_meta[0] >= MEMBER_THRESHOLD_WEIGHT)

    return {
        "requested": {
            "code": declared_code if declared_meta else None,
            "name": declared_meta[1] if declared_meta else None,
            "resolution": declared_meta[2] if declared_meta else None,
        },
        "available": [
            {
                "code": c,
                "name": m[1],
                "resolution": m[2],
                "member_tier": m[3],
            }
            for c, m in available
        ],
        "highest_available": {
            "code": best_code,
            "name": best_meta[1] if best_meta else None,
            "resolution": best_meta[2] if best_meta else None,
            "member_tier": best_meta[3] if best_meta else False,
        },
        "is_member": is_member,
        "downgraded": downgraded,
    }


# ================ m3u8 相关 ================
_QUALITY_PARAM_KEYS = ("definition", "streamtype", "stream_type", "ftype", "type", "hd", "quality", "q")

_M3U8_STREAM_NAME_RE = re.compile(r"([\w.-]*?h[1-4]|h[1-4])", re.IGNORECASE)


def parse_m3u8_url(url: str) -> dict:
    """从 m3u8 请求 URL 的 query 参数提取清晰度。"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in _QUALITY_PARAM_KEYS:
        vals = qs.get(key)
        if not vals:
            continue
        value = vals[0]
        meta = _lookup(value)
        if meta:
            return {
                "source": "m3u8_url",
                "code": value,
                "name": meta[1],
                "resolution": meta[2],
                "member_tier": meta[3],
                "is_member": meta[0] >= MEMBER_THRESHOLD_WEIGHT,
            }
    return {"source": "m3u8_url", "matched": False}


def parse_m3u8_text(text: str) -> dict:
    """从 m3u8 文本/切片 URL 的特征提取最高清晰度。"""
    hits = []
    for m in _M3U8_STREAM_NAME_RE.finditer(text):
        meta = _lookup(m.group(1))
        if meta:
            hits.append((m.group(1), meta))
    if not hits:
        return {"source": "m3u8_text", "matched": False}

    unique = {name: meta for name, meta in hits}
    top = max(unique.items(), key=lambda kv: kv[1][0])
    return {
        "source": "m3u8_text",
        "code": top[0],
        "name": top[1][1],
        "resolution": top[1][2],
        "member_tier": top[1][3],
        "is_member": top[1][0] >= MEMBER_THRESHOLD_WEIGHT,
    }


def _print_play_result(r: dict) -> None:
    print("实际可用流:")
    if not r["available"]:
        print("  (未识别到已知档位)")
    for s in r["available"]:
        tier = " 会员档" if s["member_tier"] else " 免费档"
        print(f"  {s['code']:<6} {s['name']} {s['resolution']}{tier}")
    h = r["highest_available"]
    if h["code"]:
        tier = "会员档" if h["member_tier"] else "免费档"
        print(f"最高可用: {h['code']} {h['name']} {h['resolution']} ({tier})")
    print(f"是否会员(推断): {'是' if r['is_member'] else '否'}")
    if r["downgraded"]:
        print("降级: 声明档位高于实际档位 → 疑似鉴权降级（非会员特征）")
    else:
        print("降级: 否")


def main():
    args = sys.argv[1:]
    if not args:
        print("用法:")
        print("  1. 解析 getSource JSON 文件: python parse_mgtv_quality.py playinfo.json")
        print("  2. 直接传 JSON 文本: python parse_mgtv_quality.py \"{json}\"")
        print("  3. 带声明档位: python parse_mgtv_quality.py playinfo.json --requested h4")
        print("  4. 解析 m3u8 URL:    python parse_mgtv_quality.py --m3u8 <url>")
        print("  5. 解析 m3u8 文本:   python parse_mgtv_quality.py --m3u8-file <file>")
        sys.exit(1)

    requested = None
    if "--requested" in args:
        idx = args.index("--requested")
        if idx + 1 < len(args):
            requested = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if args[0] == "--m3u8" and len(args) >= 2:
        r = parse_m3u8_url(args[1])
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    if args[0] == "--m3u8-file" and len(args) >= 2:
        try:
            with open(args[1], "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError as e:
            print(f"读取失败: {e}")
            sys.exit(1)
        r = parse_m3u8_text(text)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return

    target = args[0]
    try:
        with open(target, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        text = target

    try:
        r = parse_play_info(text, requested=requested)
    except ValueError as e:
        print(f"解析失败: {e}")
        sys.exit(1)

    _print_play_result(r)


if __name__ == "__main__":
    main()