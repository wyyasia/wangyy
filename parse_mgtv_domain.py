#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析芒果TV相关 URL，提取观看、会员、下单与支付域名信息。"""

from urllib.parse import urlparse
import re
import sys


# 芒果TV常见的播放/观看相关域名
MGTV_VIDEO_DOMAINS = {
    "mgtv.com",
    "www.mgtv.com",
    "m.mgtv.com",
    "v.mgtv.com",
    "tv.mgtv.com",
    "live.mgtv.com",
}

# 芒果TV会员相关域名
MGTV_MEMBER_DOMAINS = {
    "vip.mgtv.com",
    "club.mgtv.com",
}

# 芒果TV登录/账号相关域名
MGTV_ACCOUNT_DOMAINS = {
    "passport.mgtv.com",
}

# 芒果TV下单/订单相关域名
MGTV_ORDER_DOMAINS = {
    "club.mgtv.com",
    "vip.mgtv.com",
}

# 芒果TV会员支付相关域名（含芒果收银台、支付宝、微信支付、财付通、银联等）
MGTV_PAY_DOMAINS = {
    # 芒果TV官方收银台
    "club.mgtv.com",
    # 支付宝
    "alipay.com",
    "www.alipay.com",
    "render.alipay.com",
    "mclient.alipay.com",
    "openapi.alipay.com",
    "auth.alipay.com",
    "qr.alipay.com",
    "ds.alipay.com",
    # 微信支付
    "api.mch.weixin.qq.com",
    "api.weixin.qq.com",
    "api2.weixin.qq.com",
    "weixin.qq.com",
    "pay.weixin.qq.com",
    "wx.tenpay.com",
    # 财付通
    "www.tenpay.com",
    "tenpay.com",
    # 银联 / 云闪付
    "95516.com",
    "www.95516.com",
    "gateway.95516.com",
    "unionpay.com",
}

# 芒果TV会员支付结果确认相关域名
MGTV_PAY_RESULT_DOMAINS = {
    # 收银台/购买结果页：展示下单、支付结果
    "club.mgtv.com",
    # 会员权益 / 状态查询：确认开通是否成功
    "vip.mgtv.com",
}

# 支付结果回调类域名后缀（后端服务器间通信）
_PAY_CALLBACK_SUFFIXES = (".notify.mgtv.com", ".callback.mgtv.com", ".pay.mgtv.com")
# 支付宝网关回调域名
_ALIPAY_GATEWAY_DOMAINS = {"openapi.alipay.com", "openapi.alipaydev.com"}

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
    解析芒果TV URL，返回域名相关信息。

    返回字段：
        - original_url: 原始 URL
        - netloc: 完整主机名（如 club.mgtv.com）
        - domain: 主域名（如 vip.mgtv.com，去除 www.）
        - subdomain: 子域名前缀（如 club）
        - is_mgtv_video: 是否属于已知的芒果TV播放/观看域名
        - is_mgtv_member: 是否属于芒果TV会员相关域名
        - is_mgtv_account: 是否属于芒果TV登录/账号域名
        - is_mgtv_order: 是否属于芒果TV下单/订单相关域名
        - is_mgtv_pay: 是否属于芒果TV会员支付相关域名
        - is_mgtv_pay_result: 是否用于确认会员支付是否成功
        - pay_result_type: 支付结果确认类型
            none=无关 | result_page=支付结果页 | status_query=会员状态查询 | async_callback=支付结果回调
            回调类域名通过后缀匹配（*.notify.mgtv.com / *.callback.mgtv.com / *.pay.mgtv.com）
            以及支付宝网关域名（openapi.alipay.com）识别
        - is_mgtv: 是否属于 mgtv.com 体系
        - is_alipay: 是否属于支付宝体系
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

    if domain == "club.mgtv.com":
        pay_result_type = "result_page"
    elif domain == "vip.mgtv.com":
        pay_result_type = "status_query"
    elif domain.endswith(_PAY_CALLBACK_SUFFIXES) or domain in _ALIPAY_GATEWAY_DOMAINS:
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

    is_mgtv_pay_result = pay_result_type != "none"

    is_mgtv_video = domain in MGTV_VIDEO_DOMAINS
    is_mgtv_member = domain in MGTV_MEMBER_DOMAINS
    is_mgtv_account = domain in MGTV_ACCOUNT_DOMAINS
    is_mgtv_order = domain in MGTV_ORDER_DOMAINS
    is_mgtv_pay = domain in MGTV_PAY_DOMAINS or domain.endswith((".alipay.com", ".95516.com"))
    is_mgtv = domain.endswith(".mgtv.com") or domain == "mgtv.com"
    is_alipay = domain.endswith(".alipay.com") or domain == "alipay.com"

    return {
        "original_url": url,
        "netloc": netloc,
        "domain": domain,
        "subdomain": subdomain,
        "is_mgtv_video": is_mgtv_video,
        "is_mgtv_member": is_mgtv_member,
        "is_mgtv_account": is_mgtv_account,
        "is_mgtv_order": is_mgtv_order,
        "is_mgtv_pay": is_mgtv_pay,
        "is_mgtv_pay_result": is_mgtv_pay_result,
        "pay_result_type": pay_result_type,
        "is_mgtv": is_mgtv,
        "is_alipay": is_alipay,
    }


def filter_pay_domains(log_text: str, include_result: bool = True) -> list[dict]:
    """
    从日志文本中筛选芒果TV会员支付相关域名。

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

        if info["is_mgtv_pay"] or (include_result and info["is_mgtv_pay_result"]):
            matched.append(info)

    return matched


def _print_domain(result: dict) -> None:
    """打印单个域名解析结果。"""
    print(f"原始 URL   : {result['original_url']}")
    print(f"完整主机   : {result['netloc']}")
    print(f"主域名     : {result['domain']}")
    print(f"子域名     : {result['subdomain'] or '无'}")
    print(f"芒果视频域名: {'是' if result['is_mgtv_video'] else '否'}")
    print(f"芒果会员域名: {'是' if result['is_mgtv_member'] else '否'}")
    print(f"芒果账号域名: {'是' if result['is_mgtv_account'] else '否'}")
    print(f"芒果下单域名: {'是' if result['is_mgtv_order'] else '否'}")
    print(f"芒果支付域名: {'是' if result['is_mgtv_pay'] else '否'}")
    print(f"支付结果域名: {'是' if result['is_mgtv_pay_result'] else '否'}")
    print(f"结果确认类型: {_PAY_RESULT_TYPE_TEXT.get(result['pay_result_type'], '未知')}")
    print(f"芒果系域名  : {'是' if result['is_mgtv'] else '否'}")
    print(f"支付宝域名  : {'是' if result['is_alipay'] else '否'}")


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
        print("未在日志中筛选到芒果TV会员支付相关域名。")
        return

    print(f"共筛选到 {len(results)} 条芒果TV会员支付相关域名：")
    for i, info in enumerate(results, 1):
        print(f"\n[{i}] {info['original_url']}")
        _print_domain(info)


def main():
    args = sys.argv[1:]
    if not args:
        print("用法:")
        print("  1. 解析单个 URL:")
        print("     python parse_mgtv_domain.py <芒果TV URL>")
        print("  2. 从日志文件筛选支付域名:")
        print("     python parse_mgtv_domain.py --filter <日志文件>")
        print("  3. 从标准输入筛选支付域名:")
        print("     echo <日志文本> | python parse_mgtv_domain.py --stdin")
        sys.exit(1)

    # 日志筛选模式
    if args[0] == "--filter":
        if len(args) < 2:
            print("用法: python parse_mgtv_domain.py --filter <日志文件>")
            sys.exit(1)
        _filter_from_file(args[1])
        return

    if args[0] == "--stdin":
        log_text = sys.stdin.read()
        results = filter_pay_domains(log_text)
        if not results:
            print("未在标准输入中筛选到芒果TV会员支付相关域名。")
            return
        print(f"共筛选到 {len(results)} 条芒果TV会员支付相关域名：")
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
