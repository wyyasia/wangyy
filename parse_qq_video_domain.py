#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析腾讯视频相关 URL，提取观看、下单与支付域名信息。"""

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
    "vip.video.qq.com",
}

# 腾讯视频支付相关域名（含腾讯收银台、微信支付、财付通等）
QQ_PAY_DOMAINS = {
    # 腾讯官方收银台
    "pay.qq.com",
    "pay.video.qq.com",
    # 财付通
    "www.tenpay.com",
    "tenpay.com",
    "wx.tenpay.com",
    # 微信支付商户 / 开放平台
    "api.mch.weixin.qq.com",
    "api.weixin.qq.com",
    "api2.weixin.qq.com",
    "weixin.qq.com",
    "pay.weixin.qq.com",
    "payapp.weixin.qq.com",
    # 微信支付品牌域名
    "wechatpay.com",
    "www.wechatpay.com",
}

# 腾讯视频下单/订单相关域名
QQ_ORDER_DOMAINS = {
    "vip.video.qq.com",
    "film.qq.com",
}

# 腾讯视频会员支付结果确认相关域名
QQ_PAY_RESULT_DOMAINS = {
    # 支付结果页：展示开通成功/失败
    "film.qq.com",
    "pay.video.qq.com",
    "v.qq.com",
    # 会员权益 / 状态查询：确认开通是否成功
    "vip.video.qq.com",
    "m.vip.video.qq.com",
    "my.video.qq.com",
}

# 支付结果回调类域名后缀（后端服务器间通信）
_PAY_CALLBACK_SUFFIXES = (".notify.qq.com", ".callback.qq.com", ".pay.qq.com")
# 微信支付结果回调域名
_WEIXIN_CALLBACK_DOMAINS = {"api.mch.weixin.qq.com"}

# URL 路径中的支付结果确认特征（回调、结果页、订单状态）
_PAY_RESULT_PATH = re.compile(
    r"(/notify|/callback|/payresult|/result|/success|/status|/order)",
    re.IGNORECASE,
)
# 区分回调类路径特征
_CALLBACK_PATH = re.compile(r"/(notify|callback)", re.IGNORECASE)
# 区分结果页类路径特征
_RESULT_PATH = re.compile(r"/(result|success|status)", re.IGNORECASE)

# 支付结果确认类型的可读描述
_PAY_RESULT_TYPE_TEXT = {
    "none": "无关（非支付结果域名）",
    "result_page": "支付结果页",
    "status_query": "会员状态查询",
    "async_callback": "支付结果回调",
}

# 日志文本中提取 URL 的特征
_LOG_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def extract_domain(url: str) -> dict:
    """
    解析腾讯视频 URL，返回域名相关信息。

    返回字段：
        - original_url: 原始 URL
        - netloc: 完整主机名（如 m.v.qq.com）
        - domain: 主域名（如 v.qq.com，去除 www.）
        - subdomain: 子域名前缀（如 m）
        - is_qq_video: 是否属于已知的腾讯视频域名
        - is_qq_order: 是否属于腾讯视频下单/订单相关域名
        - is_qq_pay: 是否属于腾讯视频支付相关域名
        - is_qq_pay_result: 是否用于确认会员支付是否成功
        - pay_result_type: 支付结果确认类型
            none=无关 | result_page=支付结果页 | status_query=会员状态查询 | async_callback=支付结果回调
            回调类域名通过后缀匹配（*.notify.qq.com / *.callback.qq.com / *.pay.qq.com）
            以及微信支付回调域名（api.mch.weixin.qq.com）识别
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

    # 支付结果确认类型：none / result_page / status_query / async_callback
    pay_result_type = "none"

    if domain in ("film.qq.com", "pay.video.qq.com", "v.qq.com"):
        pay_result_type = "result_page"
    elif domain in ("vip.video.qq.com", "m.vip.video.qq.com", "my.video.qq.com"):
        pay_result_type = "status_query"
    elif domain.endswith(_PAY_CALLBACK_SUFFIXES) or domain in _WEIXIN_CALLBACK_DOMAINS:
        pay_result_type = "async_callback"

    # 根据路径特征判定（回调/结果页）
    path = parsed.path or ""
    if _PAY_RESULT_PATH.search(path):
        if _CALLBACK_PATH.search(path):
            pay_result_type = "async_callback"
        elif _RESULT_PATH.search(path):
            pay_result_type = "result_page"
        else:
            pay_result_type = pay_result_type if pay_result_type != "none" else "status_query"

    is_qq_pay_result = pay_result_type != "none"

    is_qq_video = domain in QQ_VIDEO_DOMAINS or domain.endswith(".v.qq.com")
    is_qq_order = domain in QQ_ORDER_DOMAINS
    is_qq_pay = domain in QQ_PAY_DOMAINS or domain.endswith((".tenpay.com", ".wechatpay.com"))
    is_qq = domain.endswith(".qq.com") or domain == "qq.com"

    return {
        "original_url": url,
        "netloc": netloc,
        "domain": domain,
        "subdomain": subdomain,
        "is_qq_video": is_qq_video,
        "is_qq_order": is_qq_order,
        "is_qq_pay": is_qq_pay,
        "is_qq_pay_result": is_qq_pay_result,
        "pay_result_type": pay_result_type,
        "is_qq": is_qq,
    }


def filter_pay_domains(log_text: str, include_result: bool = True) -> list[dict]:
    """
    从日志文本中筛选腾讯会员支付相关域名。

    参数：
        - log_text: 日志文本（可包含 URL、域名、普通日志行）
        - include_result: 是否同时包含支付结果确认域名（默认 True）

    返回：
        - 符合条件的域名信息列表，元素结构与 extract_domain 返回值一致。
    """
    if not log_text or not isinstance(log_text, str):
        return []

    matched = []
    seen = set()

    for url in _LOG_URL_RE.findall(log_text):
        # 清理 URL 尾部可能附带的分隔符
        url = url.rstrip(",.;)")
        if url in seen:
            continue
        seen.add(url)

        try:
            info = extract_domain(url)
        except ValueError:
            continue

        if info["is_qq_pay"] or (include_result and info["is_qq_pay_result"]):
            matched.append(info)

    return matched


def _print_domain(result: dict) -> None:
    """打印单个域名解析结果。"""
    print(f"原始 URL : {result['original_url']}")
    print(f"完整主机 : {result['netloc']}")
    print(f"主域名   : {result['domain']}")
    print(f"子域名   : {result['subdomain'] or '无'}")
    print(f"腾讯视频域名: {'是' if result['is_qq_video'] else '否'}")
    print(f"腾讯下单域名: {'是' if result['is_qq_order'] else '否'}")
    print(f"腾讯支付域名: {'是' if result['is_qq_pay'] else '否'}")
    print(f"支付结果域名: {'是' if result['is_qq_pay_result'] else '否'}")
    print(f"结果确认类型: {_PAY_RESULT_TYPE_TEXT.get(result['pay_result_type'], '未知')}")
    print(f"QQ 系域名   : {'是' if result['is_qq'] else '否'}")


def _filter_from_file(path: str, include_result: bool = True) -> None:
    """从日志文件筛选支付相关域名并打印。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            log_text = f.read()
    except OSError as e:
        print(f"读取日志文件失败: {e}")
        sys.exit(1)

    results = filter_pay_domains(log_text, include_result=include_result)
    if not results:
        print("未在日志中筛选到腾讯会员支付相关域名。")
        return

    print(f"共筛选到 {len(results)} 条腾讯会员支付相关域名：")
    for i, info in enumerate(results, 1):
        print(f"\n[{i}] {info['original_url']}")
        _print_domain(info)


def main():
    args = sys.argv[1:]
    if not args:
        print("用法:")
        print("  1. 解析单个 URL:")
        print("     python parse_qq_video_domain.py <腾讯视频URL>")
        print("  2. 从日志文件筛选支付域名:")
        print("     python parse_qq_video_domain.py --filter <日志文件>")
        print("  3. 从标准输入筛选支付域名:")
        print("     echo <日志文本> | python parse_qq_video_domain.py --stdin")
        sys.exit(1)

    # 日志筛选模式
    if args[0] == "--filter":
        if len(args) < 2:
            print("用法: python parse_qq_video_domain.py --filter <日志文件>")
            sys.exit(1)
        _filter_from_file(args[1])
        return

    if args[0] == "--stdin":
        log_text = sys.stdin.read()
        results = filter_pay_domains(log_text)
        if not results:
            print("未在标准输入中筛选到腾讯会员支付相关域名。")
            return
        print(f"共筛选到 {len(results)} 条腾讯会员支付相关域名：")
        for i, info in enumerate(results, 1):
            print(f"\n[{i}] {info['original_url']}")
            _print_domain(info)
        return

    # 单个 URL 解析模式
    try:
        result = extract_domain(args[0])
        _print_domain(result)
    except ValueError as e:
        print(f"解析失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
