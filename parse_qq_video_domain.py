#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析腾讯视频相关 URL，提取观看域名信息。"""

from urllib.parse import urlparse
import re
import sys


# 腾讯视频常见的播放/观看相关域名
QQ_VIDEO_DOMAINS = {
    "v.qq.com",
    "m.v.qq.com",
    "film.qq.com",
    "tv.qq.com",
    "anime.qq.com",
    "kid.qq.com",
    "doki.qq.com",
    "game.qq.com",
    "sports.qq.com",
    "video.qq.com",
}


def extract_domain(url: str) -> dict:
    """
    解析腾讯视频 URL，返回域名相关信息。

    返回字段：
        - original_url: 原始 URL
        - netloc: 完整主机名（如 m.v.qq.com）
        - domain: 主域名（如 v.qq.com，去除 www.）
        - subdomain: 子域名前缀（如 m）
        - is_qq_video: 是否属于已知的腾讯视频域名
        - is_qq: 是否属于 qq.com 体系
    """
    if not url or not isinstance(url, str):
        raise ValueError("请输入有效的 URL 字符串")

    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url

    parsed = urlparse(url)
    netloc = parsed.netloc.lower()

    # 移除端口（如 :8080）
    netloc = netloc.split(":")[0]

    # 移除 www. 前缀用于主域名判断
    domain = re.sub(r"^www\.", "", netloc)

    # 子域名前缀
    subdomain = ""
    parts = domain.split(".")
    if len(parts) > 2:
        subdomain = parts[0]

    is_qq_video = domain in QQ_VIDEO_DOMAINS or domain.endswith(".v.qq.com")
    is_qq = domain.endswith(".qq.com") or domain == "qq.com"

    return {
        "original_url": url,
        "netloc": netloc,
        "domain": domain,
        "subdomain": subdomain,
        "is_qq_video": is_qq_video,
        "is_qq": is_qq,
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python parse_qq_video_domain.py <腾讯视频URL>")
        print("示例: python parse_qq_video_domain.py https://v.qq.com/x/cover/xxxxx.html")
        sys.exit(1)

    raw_url = sys.argv[1]
    try:
        result = extract_domain(raw_url)
        print(f"原始 URL : {result['original_url']}")
        print(f"完整主机 : {result['netloc']}")
        print(f"主域名   : {result['domain']}")
        print(f"子域名   : {result['subdomain'] or '无'}")
        print(f"腾讯视频域名: {'是' if result['is_qq_video'] else '否'}")
        print(f"QQ 系域名   : {'是' if result['is_qq'] else '否'}")
    except ValueError as e:
        print(f"解析失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
