#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""芒果TV会员判定规则验证脚本。

读取两份DPI日志样本：
  - 芒果-mangguo.xlsx : 已知会员用户的观看日志（验证召回率）
  - 芒果-其他.xlsx    : 随机用户的观看日志（估计疑似会员渗透率）

统计不同清晰度档位的观看人数/记录数分布，并按方案规则验证：
  会员档 = 清晰度档位 >= 4（高清SDR 1080P / 臻享1080P 为VIP专享）

用法:
  python validate_mgtv_member.py [会员文件] [随机文件]
  缺省时在脚本所在目录自动查找 *芒果-mangguo*.xlsx / *芒果-其他*.xlsx

依赖: pip install openpyxl
"""

import glob
import os
import re
import sys
from collections import defaultdict

try:
    import openpyxl
except ImportError:
    sys.exit("缺少依赖: 请先执行 pip install openpyxl")

# ---------------- DPI列定义（104列XDR，列序从1开始） ----------------
COL_IMSI = 12        # 用户IMSI
COL_MSISDN = 14      # 用户号码
COL_RNAME = 82       # URL中携带的视频资源名称
COL_BITRATE = 83     # 视频文件的码速
COL_SUCCESS = 84     # 播放成功标识 (1=成功)
COL_DOWN_BYTES = 88  # 视频下载数据量
COL_DOWN_TIME = 89   # 视频下载时间
COL_QUALITY = 91     # 视频清晰度（档位1-6）
COL_VIDEO_ID = 92    # 合成视频ID

QUALITY_NAMES = {
    1: "流畅360P", 2: "标清480P", 3: "高清720P",
    4: "高清SDR 1080P(VIP)", 5: "臻享1080P(VIP)", 6: "蓝光/4K(SVIP)",
}
MEMBER_TIER = 4  # 档位>=4 即会员档

# 广告/音频/纯初始化素材特征（正则，命中即剔除）
NOISE_RE = re.compile(r"rls_high|_audio_|_init\.mp4$|^$", re.IGNORECASE)


def to_num(v):
    """安全转数值，失败返回None。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_rows(path):
    """读取xlsx为原始行列表（首行为表头时跳过）。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    # 若首行第91列为'视频清晰度'等文本表头，则跳过
    if rows and isinstance(rows[0][COL_QUALITY - 1], str):
        rows = rows[1:]
    return rows


def classify(path, label):
    """按方案规则统计一份样本，返回统计结果dict。"""
    rows = read_rows(path)
    stat = {
        "label": label, "file": os.path.basename(path),
        "total": len(rows), "valid": 0,
        "tier_users": defaultdict(set),   # 档位 -> 去重用户集合
        "tier_records": defaultdict(int), # 档位 -> 记录数
        "tier_bitrate_sum": defaultdict(float),
        "tier_bitrate_cnt": defaultdict(int),
        "member_records": 0, "member_users": set(),
        "premium_users": set(),           # 命中过档位5的用户（高置信）
        "noise_filtered": 0, "invalid_quality": 0,
    }
    for r in rows:
        q = to_num(r[COL_QUALITY - 1])
        if q is None or not (1 <= q <= 6):
            stat["invalid_quality"] += 1
            continue
        q = int(q)
        success = to_num(r[COL_SUCCESS - 1])
        dbytes = to_num(r[COL_DOWN_BYTES - 1]) or 0
        dtime = to_num(r[COL_DOWN_TIME - 1]) or 0
        rname = str(r[COL_RNAME - 1] or "")
        if success != 1 or dbytes <= 0 or dtime <= 0:
            continue
        if NOISE_RE.search(rname):
            stat["noise_filtered"] += 1
            continue
        user = r[COL_MSISDN - 1] or r[COL_IMSI - 1] or "unknown"
        stat["valid"] += 1
        stat["tier_records"][q] += 1
        stat["tier_users"][q].add(user)
        br = to_num(r[COL_BITRATE - 1])
        if br:
            stat["tier_bitrate_sum"][q] += br
            stat["tier_bitrate_cnt"][q] += 1
        if q >= MEMBER_TIER:
            stat["member_records"] += 1
            stat["member_users"].add(user)
            if q >= 5:
                stat["premium_users"].add(user)
    return stat


def print_stat(stat):
    print("=" * 78)
    print(f"样本: {stat['label']}  ({stat['file']})")
    print(f"总记录 {stat['total']} 条 | 有效播放 {stat['valid']} 条 | "
          f"广告/音频剔除 {stat['noise_filtered']} 条 | 清晰度非法 {stat['invalid_quality']} 条")
    print("-" * 78)
    print(f"{'档位':<4}{'画质':<20}{'记录数':>8}{'观看人数':>10}{'人数占比':>10}{'平均码速Mbps':>14}")
    all_users = set().union(*stat["tier_users"].values()) if stat["tier_users"] else set()
    for q in range(1, 7):
        rec = stat["tier_records"].get(q, 0)
        users = stat["tier_users"].get(q, set())
        pct = f"{len(users) / len(all_users) * 100:.1f}%" if all_users else "-"
        cnt = stat["tier_bitrate_cnt"].get(q, 0)
        avg = f"{stat['tier_bitrate_sum'][q] / cnt / 1_000_000:.2f}" if cnt else "-"
        mark = " <-- 会员档" if q >= MEMBER_TIER else ""
        print(f"{q:<4}{QUALITY_NAMES[q]:<20}{rec:>8}{len(users):>10}{pct:>10}{avg:>14}{mark}")
    print("-" * 78)
    n = len(all_users)
    print(f"去重用户总数: {n}")
    print(f"命中会员档(>=4, >=1条) 用户: {len(stat['member_users'])}"
          + (f"  占 {len(stat['member_users']) / n * 100:.1f}%" if n else ""))
    print(f"命中臻享1080P(=5, 高置信) 用户: {len(stat['premium_users'])}")
    return stat


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    member_file = sys.argv[1] if len(sys.argv) > 1 else next(
        iter(glob.glob(os.path.join(base, "*芒果-mangguo*.xlsx")) +
             glob.glob(os.path.join(base, "*mangguo*.xlsx"))), None)
    other_file = sys.argv[2] if len(sys.argv) > 2 else next(
        iter(glob.glob(os.path.join(base, "*芒果-其他*.xlsx"))), None)

    if not member_file or not os.path.exists(member_file):
        sys.exit("未找到会员样本 芒果-mangguo.xlsx，请确认路径或作为参数传入")
    if not other_file or not os.path.exists(other_file):
        sys.exit("未找到随机样本 芒果-其他.xlsx，请确认路径或作为参数传入")

    m = print_stat(classify(member_file, "已知会员样本"))
    o = print_stat(classify(other_file, "随机用户样本"))

    print("=" * 78)
    print("【可行性验证结论】")
    if m["tier_users"]:
        recall = len(m["member_users"]) / len(set().union(*m["tier_users"].values()))
        print(f"1. 召回率: 已知会员样本中 {len(m['member_users'])}/{len(set().union(*m['tier_users'].values()))} "
              f"个用户命中会员档, 召回率 {recall * 100:.1f}%")
    print(f"2. 渗透率: 随机样本中疑似会员占比 "
          f"{len(o['member_users']) / max(len(set().union(*o['tier_users'].values())), 1) * 100:.1f}%")
    print(f"3. 码速交叉验证: 档位4/5平均码速应落在1.5~3.5Mbps区间(见上表)")
    print(f"4. 严格口径(>=2条会员档)可进一步降低误判, 建议结合业务场景选择")


if __name__ == "__main__":
    main()
