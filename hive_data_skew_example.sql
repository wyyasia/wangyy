-- ============================================
-- Hive 数据倾斜处理完整示例
-- 业务场景：GPRS 用户访问日志（dw_gprs_url_host_day），
--          统计每个 uri_host 的流量，并关联维度表补充域名归属
-- 覆盖：倾斜诊断 / group by 倾斜 / join 倾斜 / count(distinct) 倾斜 / 验证
-- ============================================

-- ------------------------------------------------------------
-- 第 1 部分：造测试数据（模拟倾斜）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tmp_skew_visit_log (
    day_id      STRING,
    user_id     STRING,
    serv_number STRING,
    uri_host    STRING,
    ul_data     BIGINT,
    dl_data     BIGINT,
    dur_time    BIGINT
) STORED AS ORC;

-- 造数：deepseek.com 是热点（10 万条），其余域名各几千条
-- 用 rand()*N 给热点打散成不同 uid，制造 group by/join 倾斜
INSERT OVERWRITE TABLE tmp_skew_visit_log
SELECT '20260918', uid, serv, uri_host, ul, dl, dur
FROM (
    -- 热点域名 deepseek.com：10 万条
    SELECT CONCAT('u', CAST(rand() * 5000 AS INT))          AS uid,
           CONCAT('1', LPAD(CAST(rand() * 999999999 AS INT), 10, '0')) AS serv,
           'deepseek.com' AS uri_host,
           CAST(rand() * 1000 AS BIGINT)  AS ul,
           CAST(rand() * 5000 AS BIGINT)  AS dl,
           CAST(rand() * 300 AS BIGINT)   AS dur
    FROM (SELECT explode(split(space(99999), ' '))) t
    UNION ALL
    -- 普通域名：各 5000 条
    SELECT CONCAT('u', CAST(rand() * 5000 AS INT)) AS uid,
           CONCAT('1', LPAD(CAST(rand() * 999999999 AS INT), 10, '0')) AS serv,
           domain AS uri_host,
           CAST(rand() * 1000 AS BIGINT), CAST(rand() * 5000 AS BIGINT), CAST(rand() * 300 AS BIGINT)
    FROM (SELECT explode(array(
            'chatgpt.com','kimi.com','tongyi.com','baidu.com','qq.com','weibo.com')) AS domain) d
    CROSS JOIN (SELECT explode(split(space(4999), ' '))) t
) a;

-- 维度表：域名归属（hot key 值 'deepseek.com' 与明细表热点一致，模拟 join 倾斜）
CREATE TABLE IF NOT EXISTS tmp_skew_dim_domain (
    uri_host    STRING,
    domain_owner STRING,
    domain_type STRING
) STORED AS ORC;

INSERT OVERWRITE TABLE tmp_skew_dim_domain
SELECT uri_host, owner, type
FROM (
    SELECT 'deepseek.com' AS uri_host, '深度求索' AS owner, 'LLM' AS type
    UNION ALL SELECT 'chatgpt.com', 'OpenAI', 'LLM'
    UNION ALL SELECT 'kimi.com', '月之暗面', 'LLM'
    UNION ALL SELECT 'tongyi.com', '阿里', 'LLM'
    UNION ALL SELECT 'baidu.com', '百度', '搜索'
    UNION ALL SELECT 'qq.com', '腾讯', '门户'
    UNION ALL SELECT 'weibo.com', '新浪', '社交'
) t;

-- ------------------------------------------------------------
-- 第 2 部分：倾斜诊断
--  先确认哪些 key 是倾斜点，再对症处理
-- ------------------------------------------------------------
-- 2.1 查看各 uri_host 数据量分布（热点一目了然）
SELECT uri_host, COUNT(*) AS cnt
FROM tmp_skew_visit_log
GROUP BY uri_host
ORDER BY cnt DESC;

-- 2.2 更通用的诊断：按计数分桶，看集中在少数 key 与否
--    count 值 > 平均 N 倍的 key 即为候选倾斜 key
SELECT uri_host, COUNT(*) AS cnt
FROM tmp_skew_visit_log
GROUP BY uri_host
HAVING COUNT(*) > 10 * (SELECT AVG(c) FROM (SELECT COUNT(*) AS c FROM tmp_skew_visit_log GROUP BY uri_host) x);

-- ------------------------------------------------------------
-- 第 3 部分：group by 倾斜 → 两阶段聚合
-- ------------------------------------------------------------
-- 3.1 差分：热点 key 全部压到同一个 reducer（问题写法）
SELECT uri_host, SUM(ul_data) AS total_ul
FROM tmp_skew_visit_log
GROUP BY uri_host;

-- 3.2 两阶段聚合（打散 + 归并）
SELECT uri_host, SUM(partial_ul) AS total_ul
FROM (
    SELECT uri_host, rk, SUM(ul_data) AS partial_ul
    FROM (
        -- 随机前缀 0~9，热点 key 分散到 10 个 reducer
        SELECT CONCAT(CAST(rand() * 10 AS INT), '_', uri_host) AS rk,
               uri_host, ul_data
        FROM tmp_skew_visit_log
    ) t1
    GROUP BY uri_host, rk
) t2
GROUP BY uri_host;

-- 3.3 参数化兜底：Hive 自动两阶段
SET hive.groupby.skewindata = true;
-- 打开后 3.1 会被自动优化，跑完记得关闭：
-- SET hive.groupby.skewindata = false;

-- ------------------------------------------------------------
-- 第 4 部分：join 倾斜
--  大表 tmp_skew_visit_log join 小维度表 tmp_skew_dim_domain
--  deepseek.com 作为 hot key 在两个表都对不上/集中，导致单个 reducer 过载
-- ------------------------------------------------------------
SET hive.groupby.skewindata = false;

-- 4.1 方案一：MapJoin（小表进内存，不产生 shuffle/reduce）
SET hive.auto.convert.join = true;
SET hive.auto.convert.join.noconditionaltask.size = 268435456; -- 小表阈值 256MB
SELECT /*+ MAPJOIN(b) */ a.uri_host, b.domain_owner, SUM(a.dl_data) AS total_dl
FROM tmp_skew_visit_log a
LEFT JOIN tmp_skew_dim_domain b ON a.uri_host = b.uri_host
GROUP BY a.uri_host, b.domain_owner;

-- 4.2 方案二：两表都有热点、无法 MapJoin 时，把热点 key 单独拉出来打散
--    步骤：确定 hot key（deepseek.com）
SET hive.auto.convert.join = false;

-- (a) 大表中 hot key 部分：加随机后缀打散（0~9）
CREATE TABLE IF NOT EXISTS tmp_skew_hot_big AS
SELECT CONCAT('|', CAST(rand() * 10 AS INT), '|', uri_host) AS join_key, *
FROM tmp_skew_visit_log
WHERE uri_host = 'deepseek.com';

-- (b) 维度表中对应 key：广播 放大 10 份，对齐后缀
CREATE TABLE IF NOT EXISTS tmp_skew_hot_dim AS
SELECT uri_host AS join_key2, d.*
FROM tmp_skew_dim_domain d
CROSS JOIN (SELECT explode(split(space(9), ' '))) x
WHERE uri_host = 'deepseek.com';

-- (c) 大表中普通 key 部分：原样 join
SELECT uri_host, b.domain_owner, SUM(a.dl_data) AS total_dl
FROM tmp_skew_visit_log a
LEFT JOIN tmp_skew_dim_domain b ON a.uri_host = b.uri_host
WHERE a.uri_host <> 'deepseek.com'
GROUP BY uri_host, b.domain_owner

UNION ALL

-- 热点部分：join_key 对齐后再 join
SELECT SUBSTRING(a.uri_host, INSTR(a.uri_host, '|', 2) + 1) AS uri_host,
       b.domain_owner, SUM(a.dl_data) AS total_dl
FROM tmp_skew_hot_big a
LEFT JOIN tmp_skew_hot_dim b
  ON a.join_key = CONCAT('|', b.join_key2)  -- 与放大后的维度后缀对齐
GROUP BY SUBSTRING(a.uri_host, INSTR(a.uri_host, '|', 2) + 1), b.domain_owner;

-- 4.3 方案三（更常规）：SMB/分桶 + 单独处理 NULL，热点由分桶天然分散
--  对 hot key 预先聚合一次，把小表热点行数压下去，再 join
--  SET hive.optimize.bucketmapjoin = true;  -- 需表 bucketed，此处略

-- ------------------------------------------------------------
-- 第 5 部分：count(distinct) 倾斜 → 先 group by 再 count
-- ------------------------------------------------------------
-- 5.1 差：单 reducer 完成全量去重
SELECT COUNT(DISTINCT user_id) FROM tmp_skew_visit_log;

-- 5.2 好：先分发去重（map 端压数量），再全局 count
SELECT COUNT(*) AS distinct_user_cnt
FROM (
    SELECT user_id FROM tmp_skew_visit_log GROUP BY user_id
) tmp;

-- 5.3 若还要保留别的明细列，用 group by 全局唯一键再加 count
-- SELECT COUNT(*) FROM (SELECT day_id, user_id, uri_host FROM tmp_skew_visit_log GROUP BY day_id, user_id, uri_host) t;

-- ------------------------------------------------------------
-- 第 6 部分：NULL/空值倾斜
-- ------------------------------------------------------------
-- 6.1 直接过滤掉无意义的空 uri_host
SELECT uri_host, SUM(ul_data)
FROM tmp_skew_visit_log
WHERE uri_host IS NOT NULL AND trim(uri_host) <> ''
GROUP BY uri_host;

-- 6.2 如果不允许过滤（需保留统计），空值打随机前缀独立聚合，最后合并
SELECT CASE WHEN uri_host IS NULL OR trim(uri_host) = '' THEN 'NULL' ELSE uri_host END AS k,
       SUM(ul_data) AS total_ul
FROM (
    SELECT CASE WHEN uri_host IS NULL OR trim(uri_host) = '' THEN CONCAT('null_', CAST(rand()*100 AS INT)) ELSE uri_host END AS uri_host,
           ul_data
    FROM tmp_skew_visit_log
) t
GROUP BY CASE WHEN uri_host IS NULL OR trim(uri_host) = '' THEN 'NULL' ELSE uri_host END;

-- ------------------------------------------------------------
-- 第 7 部分：验证（结果一致性对比）
-- ------------------------------------------------------------
-- 7.1 各 uri_host 流量汇总（应三份结果一致：3.1 / 3.2 / MapJoin 版）
SELECT uri_host, SUM(ul_data) AS total_ul
FROM tmp_skew_visit_log
GROUP BY uri_host
ORDER BY total_ul DESC;

-- 7.2 检查 hot key 处理后的 join 结果是否与原 join 一致
--  对比 4.1 与 4.2 输出：domain_owner 分组合计应相等
--  deepseek.com 期望 owner=深度求索，行数与明细中 deepseek 记录数一致

-- 7.3 行数与记录数审计
SELECT COUNT(*) AS total_rows FROM tmp_skew_visit_log;
-- 预期 130000（深 100000 + 6 域名 * 5000）

-- ------------------------------------------------------------
-- 收尾：清掉临时表（生产环境按需保留）
-- ------------------------------------------------------------
-- DROP TABLE IF EXISTS tmp_skew_hot_big;
-- DROP TABLE IF EXISTS tmp_skew_hot_dim;

-- ------------------------------------------------------------
-- 核心结论速查
--  group by 倾斜   → 两阶段聚合 / hive.groupby.skewindata
--  join 倾斜       → MapJoin（小表）或 hot key 打散分桶 join
--  distinct 倾斜   → 先 group by 去重再 count
--  空值倾斜        → 过滤 or 打散独立聚合
--  万能兜底        → hive.exec.reducers.bytes.per.reducer 调小，增多 reducer
-- ============================================