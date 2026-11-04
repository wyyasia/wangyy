#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""芒果TV VIP会员判定模块（基于DPI日志清晰度）。

判定依据（与《芒果视频会员判定方案.docx》一致）：
  非会员最高只能播高清720P；观看过 清晰度档位>=4（高清SDR 1080P / 臻享1080P）
  的视频即判定为疑似VIP会员。臻享1080P(=5)为高置信判据。

快速上手：
    from mgtv_member_detector import MgtvMemberDetector

    det = MgtvMemberDetector()
    result = det.detect_from_file("芒果-其他.xlsx")       # 单文件分析
    det.print_summary(result)                              # 打印分布报表

    # 结果结构：
    #   result["tier_stats"]    档位分布统计
    #   result["user_flags"]    {用户: {"member_flag","confidence",...}}
    #   result["summary"]       总量/命中率等汇总

也支持批量：
    det = MgtvMemberDetector()
    r1 = det.detect_from_file("a.xlsx", period="2026-10")
    r2 = det.detect_from_file("b.xlsx", period="2026-11")

依赖: pip install openpyxl
"""

import os
import re
from collections import defaultdict

try:
    import openpyxl
except ImportError:
    openpyxl = None

# ---------------- DPI 104列XDR列定义（1-based） ----------------
COL_IMSI = 12         # 用户IMSI
COL_MSISDN = 14       # 用户号码
COL_RNAME = 82        # URL中携带的视频资源名称
COL_BITRATE = 83      # 视频文件的码速
COL_SUCCESS = 84      # 播放成功标识(1=成功)
COL_DOWN_BYTES = 88   # 视频下载数据量
COL_DOWN_TIME = 89    # 视频下载时间
COL_QUALITY = 91      # 视频清晰度

QUALITY_NAMES = {
    1: "流畅360P", 2: "标清480P", 3: "高清720P",
    4: "高清SDR 1080P(VIP)", 5: "臻享1080P(VIP)", 6: "蓝光/4K(SVIP)",
}

# 文本型清晰度 → 档位（兼容中文画质名称直接出现在清晰度列的情况）
TEXT_QUALITY = {
    "流畅": 1, "标清": 2, "高清": 3,
    "高清sdr1080p": 4, "高清sdr 1080p": 4, "1080p": 4, "超清": 4,
    "臻享1080p": 5, "臻享": 5,
    "蓝光": 6, "4k": 6, "hdr": 6,
}

NOISE_RE = re.compile(r"rls_high|_audio_|_init\.mp4$", re.IGNORECASE)


class MgtvMemberDetector:
    """芒果TV VIP会员判定器。

    参数:
        member_tier:      会员档阈值，默认4（清晰度>=4即会员档）
        min_down_bytes:   有效会话最小下载数据量，默认1字节（建议线上用>1MB过滤初始化请求）
        strict_min:       严格口径所需的最少会员档播放条数，默认2
        user_keys:        用户去重键的列号列表，默认[用户号码, IMSI]，取第一个非空者
    """

    def __init__(self, member_tier=4, min_down_bytes=1, strict_min=2,
                 user_keys=None):
        self.member_tier = member_tier
        self.min_down_bytes = min_down_bytes
        self.strict_min = strict_min
        self.user_keys = user_keys or [COL_MSISDN, COL_IMSI]

    # ---------- 数据读取 ----------
    def _read_rows(self, path):
        if openpyxl is None:
            raise ImportError("缺少依赖: pip install openpyxl")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if rows and isinstance(rows[0][COL_QUALITY - 1], str):
            rows = rows[1:]  # 跳过表头
        return rows

    # ---------- 字段解析 ----------
    @staticmethod
    def _num(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _parse_quality(self, v):
        """清晰度解析：数字1-6或文本画质名，非法返回None。"""
        q = self._num(v)
        if q is not None:
            return int(q) if 1 <= q <= 6 else None
        text = str(v or "").strip().lower()
        return TEXT_QUALITY.get(text)

    def _user_id(self, row):
        for col in self.user_keys:
            v = row[col - 1]
            if v not in (None, ""):
                return str(v)
        return "unknown"

    # ---------- 单条记录有效性判定 ----------
    def _is_valid(self, row):
        if self._num(row[COL_SUCCESS - 1]) != 1:
            return False
        if (self._num(row[COL_DOWN_BYTES - 1]) or 0) < self.min_down_bytes:
            return False
        if (self._num(row[COL_DOWN_TIME - 1]) or 0) <= 0:
            return False
        if NOISE_RE.search(str(row[COL_RNAME - 1] or "")):
            return False
        return True

    # ---------- 核心：分析 ----------
    def detect(self, rows, source="", period=""):
        """分析DPI日志行，返回完整判定结果dict。"""
        stat = {
            "source": source, "period": period,
            "total": 0, "valid": 0, "noise_filtered": 0, "invalid_quality": 0,
            "tier_records": defaultdict(int),
            "tier_users": defaultdict(set),
            "tier_bitrate_sum": defaultdict(float),
            "tier_bitrate_cnt": defaultdict(int),
        }
        # 用户级：会员档播放条数、是否命中臻享(>=5)
        user_tier_cnt = defaultdict(int)
        user_premium = set()
        user_max_q = {}

        for r in rows:
            stat["total"] += 1
            q = self._parse_quality(r[COL_QUALITY - 1])
            if q is None:
                stat["invalid_quality"] += 1
                continue
            if not self._is_valid(r):
                if NOISE_RE.search(str(r[COL_RNAME - 1] or "")):
                    stat["noise_filtered"] += 1
                continue
            user = self._user_id(r)
            stat["valid"] += 1
            stat["tier_records"][q] += 1
            stat["tier_users"][q].add(user)
            br = self._num(r[COL_BITRATE - 1])
            if br:
                stat["tier_bitrate_sum"][q] += br
                stat["tier_bitrate_cnt"][q] += 1
            if q >= self.member_tier:
                user_tier_cnt[user] += 1
                if q >= 5:
                    user_premium.add(user)
                user_max_q[user] = max(user_max_q.get(user, 0), q)

        # 用户级裁决
        all_users = set().union(*stat["tier_users"].values()) if stat["tier_users"] else set()
        user_flags = {}
        for u in all_users:
            cnt = user_tier_cnt.get(u, 0)
            if cnt == 0:
                flag, conf = "非会员", "-"
            else:
                flag = "疑似VIP会员"
                if u in user_premium:
                    conf = "高置信(臻享1080P)"
                elif cnt >= self.strict_min:
                    conf = f"中置信(>= {self.strict_min}次1080P)"
                else:
                    conf = "低置信(单次1080P)"
            user_flags[u] = {
                "member_flag": flag,
                "confidence": conf,
                "member_tier_sessions": cnt,
                "max_quality": user_max_q.get(u, None),
            }

        member_users = set(user_tier_cnt)
        stat["summary"] = {
            "total_users": len(all_users),
            "member_users": len(member_users),
            "member_ratio": round(len(member_users) / len(all_users), 4) if all_users else 0.0,
            "premium_users": len(user_premium),
            "member_records": sum(user_tier_cnt.values()),
        }
        stat["user_flags"] = user_flags
        return stat

    # ---------- 便捷入口 ----------
    def detect_from_file(self, path, period=""):
        """读取xlsx文件并分析。"""
        return self.detect(self._read_rows(path),
                           source=os.path.basename(path), period=period)

    def detect_member_recall(self, member_file, **kw):
        """已知会员样本验证：返回召回率（0~1）。"""
        r = self.detect_from_file(member_file, **kw)
        s = r["summary"]
        return s["member_users"] / s["total_users"] if s["total_users"] else 0.0

    # ---------- 报表输出 ----------
    def print_summary(self, result, title=""):
        s = result["summary"]
        print("=" * 78)
        print(f"样本: {title or result['source']}  周期: {result['period'] or '-'}")
        print(f"总记录 {result['total']} | 有效 {result['valid']} | "
              f"广告剔除 {result['noise_filtered']} | 清晰度非法 {result['invalid_quality']}")
        print("-" * 78)
        print(f"{'档位':<4}{'画质':<20}{'记录数':>8}{'观看人数':>10}{'人数占比':>10}{'平均码速Mbps':>14}")
        n = s["total_users"]
        for q in range(1, 7):
            users = result["tier_users"].get(q, set())
            cnt = result["tier_bitrate_cnt"].get(q, 0)
            avg = f"{result['tier_bitrate_sum'][q] / cnt / 1e6:.2f}" if cnt else "-"
            pct = f"{len(users) / n * 100:.1f}%" if n else "-"
            mark = " <-- 会员档" if q >= self.member_tier else ""
            print(f"{q:<4}{QUALITY_NAMES[q]:<20}{result['tier_records'].get(q, 0):>8}"
                  f"{len(users):>10}{pct:>10}{avg:>14}{mark}")
        print("-" * 78)
        print(f"去重用户 {s['total_users']} | 疑似会员 {s['member_users']}"
              f"({s['member_ratio'] * 100:.1f}%) | 高置信(臻享) {s['premium_users']}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python mgtv_member_detector.py <DPI日志.xlsx> [更多文件...]")
        sys.exit(1)
    det = MgtvMemberDetector()
    for f in sys.argv[1:]:
        det.print_summary(det.detect_from_file(f))
