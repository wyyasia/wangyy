#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析优酷播放 API 响应（getPlayInfo / m3u8），识别请求清晰度并推断是否会员。

输入：优酷 getPlayInfo JSON（含 stream_types / streams / segs 字段）或 m3u8 URL/文本。
输出：
  - 实际可用最高清晰度（stream_type + 名称 + 分辨率）
  - 会员判定：达到 mp4hd2(超清/1080P) 及以上 → 疑似会员
  - 降级识别：请求档位高于实际返回档位 → 鉴权降级（非会员特征）

依据 stream_type 编码 → 清晰度对照（复用公开解析项目通行映射）：
  3gp     流畅 360P      免费
  flv/mp4 标清 480P      免费
  flvhd/mp4hd 高清 540P-720P  免费（部分片源）
  mp4hd2  超清 1080P     多数需会员
  mp4hd3  蓝光 1080P     会员
  mp4hd4  4K/HDR 2160P   会员/SVIP

=== 数字档位判断口径（ftype/hd 等参数按数字 1-6） ===
  数字档位 = 清晰度档位，数字越大越清晰，共 6 档：
    1 = 流畅 360P        免费
    2 = 标清 480P        免费
    3 = 高清 720P        免费（部分片源）
    4 = 超清 1080P       多数需会员
    5 = 蓝光 1080P 蓝光  会员
    6 = 4K / HDR 2160P   会员/SVIP
  会员判定口径：数字档位 >= 4（超清 1080P 及以上）视为会员档。
  降级判定口径：请求档位数字 > 实际可用档位数字 → 疑似鉴权降级（非会员特征）。

注意：接口字段为私有协议、版本变化快，请用真机抓包校验对照表。
"""

import json
import re
import sys
from urllib.parse import urlparse, parse_qs


# stream_type → (权重, 名称, 分辨率, 是否会员档)
# 权重用于排序取最高可用档
STREAM_TYPES = {
    "3gp":      (1,  "流畅", "360P",      False),
    "flv":      (2,  "标清", "480P",      False),
    "mp4":      (2,  "标清", "480P",      False),
    "flvhd":    (3,  "高清", "540P-720P", False),
    "mp4hd":    (3,  "高清", "540P-720P", False),
    "hd":       (3,  "高清", "540P-720P", False),
    "hd2":      (4,  "超清", "1080P",     True),
    "mp4hd2":   (4,  "超清", "1080P",     True),
    "hd3":      (5,  "蓝光", "1080P 蓝光", True),
    "mp4hd3":   (5,  "蓝光", "1080P 蓝光", True),
    "hd4":      (6,  "4K/HDR", "2160P",   True),
    "mp4hd4":   (6,  "4K/HDR", "2160P",   True),
}

# 数字档位判断口径（ftype/hd 等参数的清晰度数字 1-6）
# 数字档 = 清晰度档位，数字越大越清晰
# 与 STREAM_TYPES 的权重一一对应：3gp=1 ... mp4hd4=6
# 会员判定口径：档位 >= 4（超清 1080P 及以上）视为会员档
NUMERIC_LEVELS = {
    "1": ("3gp",     1, "流畅",     "360P",        False),
    "2": ("mp4",     2, "标清",     "480P",        False),
    "3": ("mp4hd",   3, "高清",     "540P-720P",   False),
    "4": ("mp4hd2",  4, "超清",     "1080P",       True),
    "5": ("mp4hd3",  5, "蓝光",     "1080P 蓝光",  True),
    "6": ("mp4hd4",  6, "4K/HDR",   "2160P",       True),
}

MEMBER_THRESHOLD_WEIGHT = 4  # 权重 >= 4 视为会员档


def _lookup(stream_type: str):
    """根据 stream_type 查档位信息；未知类型返回 None。"""
    key = (stream_type or "").strip().lower()
    return STREAM_TYPES.get(key)


def parse_play_info(text: str) -> dict:
    """从 getPlayInfo JSON 文本中提取清晰度信息。

    返回：
        - requested: 请求/声明的最高档（从 query/stream_types 推断）
        - available: 实际可用最高档（从 streams/segs 推断）
        - is_member: 实际可用最高档达到会员档
        - downgraded: 声明档位高于实际档位（鉴权降级）
        - streams: 可用流清单
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"不是有效的 JSON: {e}")

    # 兼容 data 包裹层
    payload = data.get("data", data)
    if isinstance(payload, dict):
        streams_info = payload.get("streams") or {}
        segs_info = payload.get("segs") or {}
        stream_types_def = payload.get("stream_types") or []
    else:
        streams_info, segs_info, stream_types_def = {}, [], []

    # 实际可用：streams 的 key 优先，其次 stream_types 数组里的 stream_type
    available_keys = set()
    if isinstance(streams_info, dict):
        available_keys.update(streams_info.keys())
    if isinstance(segs_info, dict):
        available_keys.update(segs_info.keys())
    if isinstance(stream_types_def, list):
        for item in stream_types_def:
            if isinstance(item, dict) and item.get("stream_type"):
                available_keys.add(str(item["stream_type"]))

    available = sorted(
        ((k, _lookup(k)) for k in available_keys if _lookup(k)),
        key=lambda x: x[1][0],
        reverse=True,
    )

    # 最高可用档
    best = available[0] if available else None
    best_stream, best_meta = best if best else (None, None)

    # 声明档：JSON 里是否有 requested_hd / 播放器传入的 stream_type
    declared_meta = None
    declared_hd = None
    if isinstance(payload, dict):
        declared_hd = (payload.get("requested_stream_type")
                       or payload.get("default_stream_type")
                       or payload.get("stream_type"))
        if declared_hd:
            declared_meta = _lookup(declared_hd)

    downgraded = False
    if best_meta and declared_meta:
        downgraded = declared_meta[0] > best_meta[0]

    is_member = bool(best_meta and best_meta[0] >= MEMBER_THRESHOLD_WEIGHT)

    return {
        "requested": {
            "stream_type": declared_hd,
            "name": declared_meta[1] if declared_meta else None,
            "resolution": declared_meta[2] if declared_meta else None,
        },
        "available": [
            {
                "stream_type": k,
                "name": m[1],
                "resolution": m[2],
                "member_tier": m[3],
            }
            for k, m in available
        ],
        "highest_available": {
            "stream_type": best_stream,
            "name": best_meta[1] if best_meta else None,
            "resolution": best_meta[2] if best_meta else None,
            "member_tier": best_meta[3] if best_meta else False,
        },
        "is_member": is_member,
        "downgraded": downgraded,
    }


# ================ m3u8 相关 ================
# m3u8 URL 中的清晰度参数 key（不同版本命名不同）
_QUALITY_PARAM_KEYS = ("streamtype", "stream_type", "ftype", "type", "hd", "quality", "q")

# m3u8 文本中的 stream 名称正则（EXT-X-STREAM-INF 下一行的 URL 或文件名特征）
_M3U8_STREAM_NAME_RE = re.compile(r"([\w.-]*?(?:mp4hd[0-9]?|flvhd|3gp|flv|mp4|hd[0-9]?))", re.IGNORECASE)


def parse_m3u8_url(url: str) -> dict:
    """从 m3u8 请求 URL 的 query 参数中提取清晰度。

    query 值可以是：
      - stream_type 名字（stream_type=mp4hd2）
      - 数字档位（ftype=4，按 1-6 清晰度档位口径判断）
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in _QUALITY_PARAM_KEYS:
        vals = qs.get(key)
        if not vals:
            continue
        value = vals[0].strip()
        meta = _lookup(value)
        numeric_level = None
        if meta is None:
            # 数字 1-6 档位口径
            info = _numeric_quality_lookup(value)
            if info:
                stream_type, weight, name, reso, is_member_tier = info
                return {
                    "source": "m3u8_url",
                    "param": key,
                    "numeric_level": value,
                    "stream_type": stream_type,
                    "name": name,
                    "resolution": reso,
                    "member_tier": is_member_tier,
                    "is_member": weight >= MEMBER_THRESHOLD_WEIGHT,
                }
            continue
        if meta:
            # 权重即数字档位（STREAM_TYPES 权重 1-6 与 NUMERIC_LEVELS 一一对应）
            numeric_level = str(meta[0])
            return {
                "source": "m3u8_url",
                "param": key,
                "numeric_level": numeric_level,
                "stream_type": value,
                "name": meta[1],
                "resolution": meta[2],
                "member_tier": meta[3],
                "is_member": meta[0] >= MEMBER_THRESHOLD_WEIGHT,
            }
    return {"source": "m3u8_url", "matched": False}


def parse_m3u8_text(text: str) -> dict:
    """从 m3u8 文本的 URL/文件名特征中提取最高清晰度。"""
    hits = []
    for m in _M3U8_STREAM_NAME_RE.finditer(text):
        meta = _lookup(m.group(1))
        if meta:
            hits.append((m.group(1), meta))

    if not hits:
        return {"source": "m3u8_text", "matched": False}

    # 去重取最高权重
    unique = {}
    for name, meta in hits:
        if name not in unique:
            unique[name] = meta
    top = max(unique.items(), key=lambda kv: kv[1][0])
    return {
        "source": "m3u8_text",
        "numeric_level": str(top[1][0]),  # 权重即数字档位
        "stream_type": top[0],
        "name": top[1][1],
        "resolution": top[1][2],
        "member_tier": top[1][3],
        "is_member": top[1][0] >= MEMBER_THRESHOLD_WEIGHT,
    }


def _numeric_quality_lookup(num: str):
    """按数字 1-6 档位口径查清晰度；返回 (stream_type, 权重, 名称, 分辨率, 是否会员档)，否则 None。"""
    return NUMERIC_LEVELS.get(num)


def _print_play_result(r: dict) -> None:
    print("实际可用流:")
    if not r["available"]:
        print("  (未识别到已知档位)")
    for s in r["available"]:
        tier = " 会员档" if s["member_tier"] else " 免费档"
        print(f"  {s['stream_type']:<12} {s['name']} {s['resolution']}{tier}")
    h = r["highest_available"]
    if h["stream_type"]:
        tier = "会员档" if h["member_tier"] else "免费档"
        # 权重即数字档位（1-6）
        numeric_level = STREAM_TYPES[h["stream_type"]][0]
        print(f"最高可用: {h['stream_type']} 档位{int(numeric_level)} {h['name']} {h['resolution']} ({tier})")
    print(f"是否会员(推断): {'是' if r['is_member'] else '否'}")
    if r["downgraded"]:
        print("降级: 声明档位高于实际档位 → 疑似鉴权降级（非会员特征）")
    else:
        print("降级: 否")


def main():
    args = sys.argv[1:]
    if not args:
        print("用法:")
        print("  1. 解析 getPlayInfo JSON 文件:")
        print("     python parse_youku_quality.py playinfo.json")
        print("  2. 直接传 JSON 文本: python parse_youku_quality.py \"{json}\"")
        print("  3. 解析 m3u8 URL:    python parse_youku_quality.py --m3u8 <url>")
        print("  4. 解析 m3u8 文本:   python parse_youku_quality.py --m3u8-file <file>")
        sys.exit(1)

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

    # 参数可能是文件路径或 JSON 文本
    target = args[0]
    text = None
    try:
        with open(target, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        text = target  # 当作 JSON 文本

    try:
        r = parse_play_info(text)
    except ValueError as e:
        print(f"解析失败: {e}")
        sys.exit(1)

    _print_play_result(r)


if __name__ == "__main__":
    main()