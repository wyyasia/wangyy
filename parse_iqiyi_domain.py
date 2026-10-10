#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析爱奇艺相关 URL，提取观看、会员、下单与支付域名信息。"""

from urllib.parse import urlparse
import re
import sys


# 爱奇艺常见的播放/观看相关域名
IQIYI_VIDEO_DOMAINS = {
    "iqiyi.com",
    "www.iqiyi.com",
    "m.iqiyi.com",
    "tv.iqiyi.com",
    "film.iqiyi.com",
    "anime.iqiyi.com",
    "sports.iqiyi.com",
    "kid.iqiyi.com",
    "documentary.iqiyi.com",
    "zongyi.iqiyi.com",
    "player.iqiyi.com",
}

# 爱奇艺会员相关域名
IQIYI_MEMBER_DOMAINS = {
    "vip.iqiyi.com",
    "member.iqiyi.com",
}

# 爱奇艺登录/账号相关域名
IQIYI_ACCOUNT_DOMAINS = {
    "passport.iqiyi.com",
    "account.iqiyi.com",
}

# 爱奇艺下单/订单相关域名
IQIYI_ORDER_DOMAINS = {
    "pay.iqiyi.com",
    "vip.iqiyi.com",
}

# 爱奇艺会员支付相关域名（含爱奇艺收银台、支付宝、微信支付、财付通、银联、百度钱包等）
IQIYI_PAY_DOMAINS = {
    # 爱奇艺官方收银台
    "pay.iqiyi.com",
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
    # 百度钱包
    "wallet.baidu.com",
}

# 爱奇艺会员支付结果确认相关域名
IQIYI_PAY_RESULT_DOMAINS = {
    # 收银台/购买结果页：展示下单、支付结果
    "pay.iqiyi.com",
    # 会员权益 / 状态查询：确认开通是否成功
    "vip.iqiyi.com",
    "member.iqiyi.com",
}

# 支付结果回调类域名后缀（后端服务器间通信）
_PAY_CALLBACK_SUFFIXES = (".notify.iqiyi.com", ".callback.iqiyi.com", ".pay.iqiyi.com")
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
    解析爱奇艺 URL，返回域名相关信息。

    返回字段：
        - original_url: 原始 URL
        - netloc: 完整主机名（如 pay.iqiyi.com）
        - domain: 主域名（如 vip.iqiyi.com，去除 www.）
        - subdomain: 子域名前缀（如 vip）
        - is_iqiyi_video: 是否属于已知的爱奇艺播放/观看域名
        - is_iqiyi_member: 是否属于爱奇艺会员相关域名
        - is_iqiyi_account: 是否属于爱奇艺登录/账号域名
        - is_iqiyi_order: 是否属于爱奇艺下单/订单相关域名
        - is_iqiyi_pay: 是否属于爱奇艺会员支付相关域名
        - is_iqiyi_pay_result: 是否用于确认会员支付是否成功
        - pay_result_type: 支付结果确认类型
            none=无关 | result_page=支付结果页 | status_query=会员状态查询 | async_callback=支付结果回调
            回调类域名通过后缀匹配（*.notify.iqiyi.com / *.callback.iqiyi.com / *.pay.iqiyi.com）
            以及支付宝网关域名（openapi.alipay.com）识别
        - is_iqiyi: 是否属于 iqiyi.com 体系
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

    if domain == "pay.iqiyi.com":
        pay_result_type = "result_page"
    elif domain in ("vip.iqiyi.com", "member.iqiyi.com"):
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

    is_iqiyi_pay_result = pay_result_type != "none"

    is_iqiyi_video = domain in IQIYI_VIDEO_DOMAINS
    is_iqiyi_member = domain in IQIYI_MEMBER_DOMAINS
    is_iqiyi_account = domain in IQIYI_ACCOUNT_DOMAINS
    is_iqiyi_order = domain in IQIYI_ORDER_DOMAINS
    is_iqiyi_pay = domain in IQIYI_PAY_DOMAINS or domain.endswith((".alipay.com", ".95516.com"))
    is_iqiyi = domain.endswith(".iqiyi.com") or domain == "iqiyi.com"
    is_alipay = domain.endswith(".alipay.com") or domain == "alipay.com"

    return {
        "original_url": url,
        "netloc": netloc,
        "domain": domain,
        "subdomain": subdomain,
        "is_iqiyi_video": is_iqiyi_video,
        "is_iqiyi_member": is_iqiyi_member,
        "is_iqiyi_account": is_iqiyi_account,
        "is_iqiyi_order": is_iqiyi_order,
        "is_iqiyi_pay": is_iqiyi_pay,
        "is_iqiyi_pay_result": is_iqiyi_pay_result,
        "pay_result_type": pay_result_type,
        "is_iqiyi": is_iqiyi,
        "is_alipay": is_alipay,
    }


def filter_pay_domains(log_text: str, include_result: bool = True) -> list[dict]:
    """
    从日志文本中筛选爱奇艺会员支付相关域名。

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

        if info["is_iqiyi_pay"] or (include_result and info["is_iqiyi_pay_result"]):
            matched.append(info)

    return matched


def _print_domain(result: dict) -> None:
    """打印单个域名解析结果。"""
    print(f"原始 URL   : {result['original_url']}")
    print(f"完整主机   : {result['netloc']}")
    print(f"主域名     : {result['domain']}")
    print(f"子域名     : {result['subdomain'] or '无'}")
    print(f"爱奇艺视频域名: {'是' if result['is_iqiyi_video'] else '否'}")
    print(f"爱奇艺会员域名: {'是' if result['is_iqiyi_member'] else '否'}")
    print(f"爱奇艺账号域名: {'是' if result['is_iqiyi_account'] else '否'}")
    print(f"爱奇艺下单域名: {'是' if result['is_iqiyi_order'] else '否'}")
    print(f"爱奇艺支付域名: {'是' if result['is_iqiyi_pay'] else '否'}")
    print(f"支付结果域名: {'是' if result['is_iqiyi_pay_result'] else '否'}")
    print(f"结果确认类型: {_PAY_RESULT_TYPE_TEXT.get(result['pay_result_type'], '未知')}")
    print(f"爱奇艺系域名: {'是' if result['is_iqiyi'] else '否'}")
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
        print("未在日志中筛选到爱奇艺会员支付相关域名。")
        return

    print(f"共筛选到 {len(results)} 条爱奇艺会员支付相关域名：")
    for i, info in enumerate(results, 1):
        print(f"\n[{i}] {info['original_url']}")
        _print_domain(info)


def main():
    args = sys.argv[1:]
    if not args:
        print("用法:")
        print("  1. 解析单个 URL:")
        print("     python parse_iqiyi_domain.py <爱奇艺URL>")
        print("  2. 从日志文件筛选支付域名:")
        print("     python parse_iqiyi_domain.py --filter <日志文件>")
        print("  3. 从标准输入筛选支付域名:")
        print("     echo <日志文本> | python parse_iqiyi_domain.py --stdin")
        sys.exit(1)

    # 日志筛选模式
    if args[0] == "--filter":
        if len(args) < 2:
            print("用法: python parse_iqiyi_domain.py --filter <日志文件>")
            sys.exit(1)
        _filter_from_file(args[1])
        return

    if args[0] == "--stdin":
        log_text = sys.stdin.read()
        results = filter_pay_domains(log_text)
        if not results:
            print("未在标准输入中筛选到爱奇艺会员支付相关域名。")
            return
        print(f"共筛选到 {len(results)} 条爱奇艺会员支付相关域名：")
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
