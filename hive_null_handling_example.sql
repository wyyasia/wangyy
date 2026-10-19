-- ============================================
-- Hive SQL 空值（空指针）处理完整示例
-- 业务场景：GPRS 用户访问日志（tmp_null_visit_log）
-- SQL 层的“空指针”主要指 NULL / 空字符串引发的各种问题：
--   · sum/avg/除法结果错误或报错（NULL 传播、除零）
--   · count 不统计 NULL，导致行数对不上
--   · concat/拼接出现 NULL、口径串行
--   · join 后产生 NULL、UDF 对 NULL 抛 NPE
-- 本脚本：空值诊断 → 清洗归一 → 聚合防护 → 数据质量校验
-- ============================================

-- ------------------------------------------------------------
-- 第 1 部分：造测试数据（含 NULL 与空字符串）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tmp_null_visit_log (
    day_id      STRING,
    user_id     STRING,
    serv_number STRING,       -- 可能存在 NULL
    uri_host    STRING,       -- 可能存在空字符串 ''
    ul_data     BIGINT,       -- 可能存在 NULL
    dl_data     BIGINT,
    dur_time    BIGINT
) STORED AS ORC;

INSERT OVERWRITE TABLE tmp_null_visit_log
SELECT '20260918' AS day_id, uid,
       CASE WHEN rand() < 0.1 THEN NULL ELSE serv END AS serv_number,
       CASE WHEN rand() < 0.1 THEN '' ELSE host END   AS uri_host,
       CASE WHEN rand() < 0.1 THEN NULL ELSE CAST(rand()*1000 AS BIGINT) END AS ul_data,
       CAST(rand()*5000 AS BIGINT) AS dl_data,
       CAST(rand()*300 AS BIGINT)  AS dur_time
FROM (
    SELECT CONCAT('u', CAST(rand() * 100000 AS INT)) AS uid,
           CONCAT('1', LPAD(CAST(rand() * 999999999 AS INT), 10, '0')) AS serv,
           host
    FROM (SELECT explode(array(
            'deepseek.com','chatgpt.com','kimi.com','tongyi.com','baidu.com')) AS host) d
    CROSS JOIN (SELECT explode(split(space(19999), ' '))) t
) a;

-- ------------------------------------------------------------
-- 第 2 部分：空值诊断（先量化，再决定处理策略）
-- ------------------------------------------------------------
-- 2.1 各列 NULL 数量
SELECT
    COUNT(*) AS total_rows,
    COUNT(serv_number)                        AS serv_cnt,    -- count 列忽略 NULL
    COUNT(*) - COUNT(serv_number)             AS serv_null,
    SUM(CASE WHEN uri_host = '' THEN 1 ELSE 0 END) AS uri_empty,
    SUM(CASE WHEN ul_data IS NULL THEN 1 ELSE 0 END) AS ul_null
FROM tmp_null_visit_log;

-- 2.2 空值占比（>5% 的列需要认真清洗）
SELECT
    ROUND(100 * SUM(CASE WHEN serv_number IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2) AS serv_null_pct,
    ROUND(100 * SUM(CASE WHEN uri_host = '' THEN 1 ELSE 0 END) / COUNT(*), 2)       AS uri_empty_pct,
    ROUND(100 * SUM(CASE WHEN ul_data IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2)     AS ul_null_pct
FROM tmp_null_visit_log;

-- 2.3 直接看脏样本（NULL / 空串 长啥样）
SELECT * FROM tmp_null_visit_log
WHERE serv_number IS NULL OR uri_host = '' OR ul_data IS NULL
LIMIT 10;

-- ------------------------------------------------------------
-- 第 3 部分：空值归一化（清洗 → 统一口径）
--  原则：NULL 和 '' 二选一作为该列的标准空值表示，否则全链路都是坑
-- ------------------------------------------------------------
-- 3.1 方案 A：全部归一化到业务默认值（推荐，后续无需再判空）
WITH t_clean AS (
    SELECT
        day_id,
        user_id,
        COALESCE(serv_number, 'UNKNOWN_NUM')        AS serv_number,  -- NULL→默认
        CASE WHEN uri_host = '' THEN 'NULL_HOST'
             WHEN uri_host IS NULL THEN 'NULL_HOST'  -- 触发不进来也能兜底，直接写死兜底值
             ELSE uri_host END                      AS uri_host,      -- '' 和 NULL 合并
        NVL(ul_data, 0)                             AS ul_data,       -- NULL→0，数字列聚合安全
        NVL(dl_data, 0)                             AS dl_data,
        NVL(dur_time, 0)                            AS dur_time
    FROM tmp_null_visit_log
)
SELECT * FROM t_clean LIMIT 5;

-- 3.2 方案 B：保留 NULL 但统一（只把空串转为 NULL，方便 IS NULL 统一判断）
SELECT
    NULLIF(trim(uri_host), '') AS uri_host_norm,   -- '' → NULL（trim 顺带去空格）
    serv_number,
    ul_data
FROM tmp_null_visit_log
WHERE uri_host = '' LIMIT 5;

-- 3.3 生产上直接落一张清洗表（下游从此只读干净数据）
CREATE TABLE IF NOT EXISTS tmp_null_visit_log_clean AS
SELECT
    day_id,
    user_id,
    COALESCE(serv_number, 'UNKNOWN_NUM') AS serv_number,
    CASE WHEN NULLIF(trim(uri_host), '') IS NULL THEN 'NULL_HOST' ELSE trim(uri_host) END AS uri_host,
    NVL(ul_data, 0)  AS ul_data,
    NVL(dl_data, 0)  AS dl_data,
    NVL(dur_time, 0) AS dur_time
FROM tmp_null_visit_log;

-- ------------------------------------------------------------
-- 第 4 部分：常见“空指针”坑的防护写法
-- ------------------------------------------------------------
-- 4.1 除法：NULL / 除零 都会出错，先做分母保护
SELECT uri_host,
       SUM(ul_data) AS total_ul,
       -- 差：SUM(ul_data)/SUM(dl_data) 可能除零或 NULL
       -- 好：分母为 0 时返回 NULL，再用 NVL 兜底
       NVL(SUM(ul_data) / NULLIF(SUM(dl_data), 0), 0) AS ul_dl_ratio
FROM (SELECT COALESCE(uri_host,'NULL_HOST') AS uri_host, ul_data, dl_data FROM tmp_null_visit_log) a
GROUP BY uri_host;

-- 4.2 concat/字符串拼接：NULL 会让结果整段变 NULL，逐个 COALESCE
SELECT user_id,
       -- 差：CONCAT(user_id, '_', serv_number) 若 serv_number 为 NULL → 整串 NULL
       -- 好：
       CONCAT(user_id, '_', COALESCE(serv_number, 'NA')) AS key_val
FROM tmp_null_visit_log
LIMIT 5;

-- 4.3 聚合一致性：sum 忽略 NULL（相当于当 0），但 count 忽略 NULL（相当于没这行）
--    sum 和 count 对 NULL 语义不同，混用前确认口径
SELECT 'serv' AS col,
       COUNT(*)           AS row_cnt,
       COUNT(serv_number) AS non_null_cnt,
       SUM(ul_data)       AS sum_ul,     -- NULL 当 0 累加
       AVG(ul_data)       AS avg_ul      -- 分子分母都跳过 NULL
FROM tmp_null_visit_log;

-- 4.4 join 防 NULL 漂移：LEFT JOIN 不中时给默认值，别让 NULL 进入下游字段
SELECT a.user_id,
       a.uri_host,
       NVL(b.domain_owner, 'UNKNOWN_OWNER') AS owner   -- 未匹配上的显示
FROM tmp_null_visit_log_clean a
LEFT JOIN (SELECT 'deepseek.com' AS uri_host, '深度求索' AS domain_owner) b
  ON a.uri_host = b.uri_host
LIMIT 10;

-- 4.5 UDF/NPE 防御：先过滤再进 UDF
--   例如自定义函数 get_host_rank()，对 NULL 入参会抛 NPE：
--   差：SELECT get_host_rank(uri_host) FROM t
--   好：WHERE 前置过滤，保证入参非空
SELECT uri_host, length(uri_host) AS host_len
FROM tmp_null_visit_log_clean
WHERE uri_host IS NOT NULL AND uri_host <> 'NULL_HOST'
LIMIT 5;

-- ------------------------------------------------------------
-- 第 5 部分：数据质量校验（清洗前后对比）
-- ------------------------------------------------------------
-- 5.1 清洗后应无 NULL、无空串
SELECT
    COUNT(*) AS total_rows,
    SUM(CASE WHEN serv_number IS NULL THEN 1 ELSE 0 END)  AS serv_null,
    SUM(CASE WHEN uri_host = '' THEN 1 ELSE 0 END)        AS uri_empty,
    SUM(CASE WHEN ul_data IS NULL THEN 1 ELSE 0 END)      AS ul_null
FROM tmp_null_visit_log_clean;   -- 三类都应 = 0

-- 5.2 行数守恒：清洗表与源表行数一致（JOIN/过滤别丢行）
SELECT (SELECT COUNT(*) FROM tmp_null_visit_log)      AS src_rows,
       (SELECT COUNT(*) FROM tmp_null_visit_log_clean) AS clean_rows;

-- 5.3 口径校验：原表 sum(ul) 与清洗表 sum(ul) 一致（NULL 当 0 下两者应相等）
SELECT (SELECT SUM(ul_data) FROM tmp_null_visit_log)       AS src_sum_ul,
       (SELECT SUM(ul_data) FROM tmp_null_visit_log_clean) AS clean_sum_ul;

-- ------------------------------------------------------------
-- 第 6 部分：常用空值函数速查
--  NVL(a, x)      : a 为 NULL 时返回 x（Hive 特有，仅支持 2 参）
--  COALESCE(a,b,c): 返回第一个非 NULL 参数（可多参，推荐优先用）
--  NULLIF(a, b)   : a=b 时返回 NULL，否则返回 a（把指定值转成 NULL）
--  IF(cond, x, y) : 条件分支，可做复杂空值判断
--  CASE WHEN      : 最通用
--  * count(列)   : 忽略 NULL；count(*) 统计全部行
--  * sum/avg     : 忽略 NULL（平均值分母只数非 NULL 行）
-- ------------------------------------------------------------
-- 收尾：清理临时表（生产按需）
-- DROP TABLE IF EXISTS tmp_null_visit_log_clean;
-- ============================================