-- ============================================
-- TextFile 表迁移为 ORC 完整脚本
-- 场景：把 TEXTFILE 格式的表迁移为 ORC + SNAPPY 压缩
--       （ORC 自带索引/谓词下推/列裁剪，扫描性能一般提升 5~10 倍）
-- ============================================

-- ------------------------------------------------------------
-- 第 0 步：迁移前检查源表（确认当前存储格式与统计信息）
-- ------------------------------------------------------------
DESCRIBE FORMATTED dw_gprs_url_host_day;
-- 关注：
--   Location    : 当前表目录
--   InputFormat : 若为 org.apache.hadoop.mapred.TextInputFormat 则为 TEXTFILE
--   表分区      : 若带分区先记下分区字段（如 day_id）

-- 行数 / 文件大小基线（迁移完成后对比用）
SELECT COUNT(*) FROM dw_gprs_url_host_day;
-- hdfs dfs -du -h /user/hive/warehouse/<db>.db/dw_gprs_url_host_day

-- ------------------------------------------------------------
-- 第 1 步：建 ORC 目标表
-- ------------------------------------------------------------

-- 情况 A：非分区表
CREATE TABLE dw_gprs_url_host_day_orc LIKE dw_gprs_url_host_day
STORED AS ORC
TBLPROPERTIES ('orc.compress' = 'SNAPPY');
-- 或显式建表（推荐，可单独调整列注释/字段）：
-- CREATE TABLE dw_gprs_url_host_day_orc (
--     day_id       STRING,
--     home_prov    STRING,
--     home_city    STRING,
--     roam_city    STRING,
--     roam_type    STRING,
--     user_id      BIGINT,
--     serv_number  STRING,
--     uri_host     STRING,
--     ul_data      BIGINT,
--     dl_data      BIGINT,
--     dur_time     BIGINT
-- )
-- STORED AS ORC
-- TBLPROPERTIES ('orc.compress' = 'SNAPPY');

-- 情况 B：分区表（按 day_id 分区，字段顺序与源表对齐）
-- CREATE TABLE dw_gprs_url_host_day_orc (
--     home_prov    STRING,
--     home_city    STRING,
--     roam_city    STRING,
--     roam_type    STRING,
--     user_id      BIGINT,
--     serv_number  STRING,
--     uri_host     STRING,
--     ul_data      BIGINT,
--     dl_data      BIGINT,
--     dur_time     BIGINT
-- )
-- PARTITIONED BY (day_id STRING)
-- STORED AS ORC
-- TBLPROPERTIES ('orc.compress' = 'SNAPPY');

-- ------------------------------------------------------------
-- 第 2 步：迁移数据（INSERT OVERWRITE ... SELECT）
-- ------------------------------------------------------------

-- 情况 A：非分区表整表迁移
INSERT OVERWRITE TABLE dw_gprs_url_host_day_orc
SELECT * FROM dw_gprs_url_host_day;

-- 情况 B：分区表动态分区迁移（按分区灌入，避免逐分区手写 365 次）
SET hive.exec.dynamic.partition = true;
SET hive.exec.dynamic.partition.mode = nonstrict;      -- 允许所有分区动态
SET hive.exec.max.dynamic.partitions = 1000;
SET hive.exec.max.dynamic.partitions.pernode = 500;

-- 只迁最近 N 天（推荐，避免一次全量）
INSERT OVERWRITE TABLE dw_gprs_url_host_day_orc PARTITION (day_id)
SELECT
    home_prov, home_city, roam_city, roam_type,
    user_id, serv_number, uri_host,
    ul_data, dl_data, dur_time,
    day_id          -- 动态分区列放 SELECT 最后
FROM dw_gprs_url_host_day
WHERE day_id >= '20260101';

-- 若不落 Metro，直接 CTAS 一步到位（等价于 建表+迁移）：
-- SET hive.exec.dynamic.partition = true;
-- SET hive.exec.dynamic.partition.mode = nonstrict;
-- CREATE TABLE dw_gprs_url_host_day_orc
-- STORED AS ORC
-- TBLPROPERTIES ('orc.compress' = 'SNAPPY')
-- AS
-- SELECT home_prov, home_city, roam_city, roam_type,
--        user_id, serv_number, uri_host,
--        ul_data, dl_data, dur_time, day_id
-- FROM dw_gprs_url_host_day
-- WHERE day_id >= '20260101';

-- ------------------------------------------------------------
-- 第 3 步：验证（行数 / 分区 / 大小 / 抽样内容）
-- ------------------------------------------------------------

-- 3.1 确认目标表存储格式为 ORC
DESCRIBE FORMATTED dw_gprs_url_host_day_orc;
-- 应看到：
--   Storage Format : ORC
--   InputFormat    : org.apache.hadoop.hive.ql.io.orc.OrcInputFormat

-- 3.2 全表行数对比（必须一致）
SELECT COUNT(*)         AS src_cnt FROM dw_gprs_url_host_day;
SELECT COUNT(*)         AS orc_cnt FROM dw_gprs_url_host_day_orc;
-- 不一致则查各分区：
SELECT day_id, COUNT(*) FROM dw_gprs_url_host_day GROUP BY day_id ORDER BY day_id;
SELECT day_id, COUNT(*) FROM dw_gprs_url_host_day_orc GROUP BY day_id ORDER BY day_id;

-- 3.3 文件大小对比（ORC 应显著小于 TEXTFILE）
-- hdfs dfs -du -h /user/hive/warehouse/<db>.db/dw_gprs_url_host_day
-- hdfs dfs -du -h /user/hive/warehouse/<db>.db/dw_gprs_url_host_day_orc
-- 对比文件数：hdfs dfs -count /user/hive/warehouse/<db>.db/dw_gprs_url_host_day(_orc)

-- 3.4 抽样抽查内容（随机抽 10 行对比）
SELECT * FROM dw_gprs_url_host_day_orc
TABLESAMPLE (10 ROWS);

-- 3.5 用真实业务 SQL 跑一遍，验证结果一致且性能提升
-- 例：跑与迁移前相同的聚合，EXPLAIN 对比 Scan 阶段是否走 ORC 谓词下推
EXPLAIN
SELECT day_id, uri_host, COUNT(*) AS cnt
FROM dw_gprs_url_host_day_orc
WHERE day_id = '20260918' AND uri_host LIKE '%deepseek.com%'
GROUP BY day_id, uri_host;

-- ------------------------------------------------------------
-- 第 4 步：验证通过后切换
-- ------------------------------------------------------------
-- 4.1 重命名（老表留 1~2 天兜底，再删除）
-- ALTER TABLE dw_gprs_url_host_day RENAME TO dw_gprs_url_host_day_bak;
-- ALTER TABLE dw_gprs_url_host_day_orc RENAME TO dw_gprs_url_host_day;
-- 4.2 重跑一次第 3 步验证新表
-- 4.3 确认无误后删备份：
-- DROP TABLE IF EXISTS dw_gprs_url_host_day_bak;
-- （DROP 会同时删除表目录数据，删除前务必确认已不需要）

-- ------------------------------------------------------------
-- 注意事项
--  1. 不要用 ALTER TABLE ... SET FILEFORMAT ORC：只改元数据，数据文件仍是文本，不会真正转格式
--  2. 分区表建议按时间范围分批迁移，降低对集群资源/SLA 的影响
--  3. 迁移是 I/O 密集任务，避开业务高峰；可选 SET mapreduce.job.reduces 控制并行
--  4. TEXTFILE 的表如果被下游 CTAS/INSERT 依赖，切换前先通知下游
--  5. 迁移后对比列数、列顺序，防止建表与源表字段错位（SELECT * 或显式列清单）
-- ============================================