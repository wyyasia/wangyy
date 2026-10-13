-- ============================================
-- Hive group by 数据倾斜：两阶段聚合完整示例
-- 场景：统计每个页面的点击量，其中 homepage 是热点
-- ============================================

-- 1. 建表：用户点击日志（uid, page_id, cnt）
CREATE TABLE IF NOT EXISTS user_click_log (
    uid     STRING,
    page_id STRING,
    cnt     BIGINT
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY '\t'
STORED AS TEXTFILE;

-- 2. 造数据
--    技巧：space(n) 生成 n 个空格，split 切出 n+1 个元素，每行贡献 1 条记录
--    结果：homepage 100 万条（倾斜 key），其余页面各 1 万条
INSERT OVERWRITE TABLE user_click_log
SELECT uid, page_id, 1 AS cnt
FROM (
    -- 热点页面：100 万条
    SELECT CONCAT('u', CAST(rand() * 100000 AS INT)) AS uid,
           'homepage' AS page_id
    FROM (SELECT explode(split(space(999999), ' '))) t1
    UNION ALL
    -- 普通页面：各 1 万条
    SELECT CONCAT('u', CAST(rand() * 100000 AS INT)) AS uid, 'search' AS page_id
    FROM (SELECT explode(split(space(9999), ' '))) t1
    UNION ALL
    SELECT CONCAT('u', CAST(rand() * 100000 AS INT)) AS uid, 'cart' AS page_id
    FROM (SELECT explode(split(space(9999), ' '))) t1
    UNION ALL
    SELECT CONCAT('u', CAST(rand() * 100000 AS INT)) AS uid, 'detail' AS page_id
    FROM (SELECT explode(split(space(9999), ' '))) t1
) t;

-- 3. 验证数据量分布（可看到 homepage 远大于其他）
SELECT page_id, COUNT(*) AS rows_cnt
FROM user_click_log
GROUP BY page_id;

-- 4. 未优化的写法：倾斜 key 全部压到同一个 reducer
SELECT page_id, SUM(cnt) AS total
FROM user_click_log
GROUP BY page_id;

-- 5. 优化写法：两阶段聚合
--    第一阶段：给 page_id 加 0~9 的随机前缀，把热点 key 打散到 10 个 reducer
--    第二阶段：去掉前缀，把同一 page_id 的局部结果再汇总
SELECT page_id, SUM(partial_total) AS total
FROM (
    SELECT page_id, rk, SUM(cnt) AS partial_total
    FROM (
        SELECT CONCAT(CAST(rand() * 10 AS INT), '_', page_id) AS rk,
               page_id,
               cnt
        FROM user_click_log
    ) t1
    GROUP BY page_id, rk          -- 先按“随机前缀+page_id”局部聚合
) t2
GROUP BY page_id;                 -- 再全局汇总

-- 6. 参数化方式（Hive 自动两阶段，无需改 SQL）
SET hive.groupby.skewindata = true;
-- 开启后，上面第 4 步的原始 SQL 会被自动优化为两阶段聚合
-- 注意：最终结果会先落到临时表，Hive 内部自动完成，行为对用户透明
