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
    # 会员权益 / 状态查询：确认开通是否成功
    "vip.video.qq.com",
    "m.vip.video.qq.com",
    "my.video.qq.com",
}

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

    if domain == "film.qq.com" or domain == "pay.video.qq.com":
        pay_result_type = "result_page"
    elif domain in ("vip.video.qq.com", "m.vip.video.qq.com", "my.video.qq.com"):
        pay_result_type = "status_query"
    elif domain.endswith((".notify.qq.com", ".callback.qq.com")):
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
        print(f"腾讯下单域名: {'是' if result['is_qq_order'] else '否'}")
        print(f"腾讯支付域名: {'是' if result['is_qq_pay'] else '否'}")
        print(f"支付结果域名: {'是' if result['is_qq_pay_result'] else '否'}")
        print(f"结果确认类型: {_PAY_RESULT_TYPE_TEXT.get(result['pay_result_type'], '未知')}")
        print(f"QQ 系域名   : {'是' if result['is_qq'] else '否'}")
    except ValueError as e:
        print(f"解析失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
