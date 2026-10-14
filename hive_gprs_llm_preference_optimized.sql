-- ============================================
-- GPRS 用户 LLM 偏好日表：SQL 优化版
-- 场景：按日扫描 dw_gprs_url_host_day，打标 AI/LLM 应用分类
-- 原 SQL 执行 93 分钟，本文件为优化版
-- 优化点：
--   1. 向量化执行 + 中间压缩（逐行字符串计算提速 2~4 倍）
--   2. WHERE 层 rlike 预过滤：非目标流量在 CASE 前被丢弃
--   3. CASE WHEN 高频分类前置短路（按实际频次调整顺序）
--   4. serv_number 多个 substr 合并为 1 个 rlike
-- ============================================

-- 会话级优化参数
SET hive.vectorized.execution.enabled=true;
SET hive.vectorized.execution.reduce.enabled=true;
SET hive.exec.parallel=true;
SET hive.exec.compress.intermediate=true;
SET hive.intermediate.compression.codec=org.apache.hadoop.io.compress.SnappyCodec;

-- 可选：调大并行度（map 数偏少时启用）
-- SET mapreduce.input.fileinputformat.split.maxsize=134217728;  -- MR 环境 128MB
-- SET tez.grouping.min-size=33554432;                          -- Tez 环境
-- SET tez.grouping.max-size=134217728;                         -- Tez 环境

-- 注意：源表建议为 ORC + SNAPPY 存储，TEXTFILE 会慢 5~10 倍
-- 建议源表建表：
-- CREATE TABLE dw_gprs_url_host_day (... ) STORED AS ORC TBLPROPERTIES ('orc.compress'='SNAPPY');

-- 建目标表前，先用 EXPLAIN 确认分区裁剪生效：
--   EXPLAIN SELECT ... WHERE day_id='20260918' AND home_prov='371';
-- 若 home_prov 不是分区列，源表应改为按 (day_id, home_prov) 分区

CREATE TABLE tmp_dw_gprs_user_llm_preference_day_20260918 AS
SELECT
    a.ROAM_CITY,
    a.HOME_PROV,
    a.HOME_CITY,
    a.ROAM_TYPE,
    a.USER_ID,
    a.SERV_NUMBER,
    a.uri_host,
    -- CASE WHEN 高频分类放前面，提前短路，减少平均分支数
    -- 请按业务实际命中频次调整顺序
    CASE
        WHEN uri_host LIKE '%doubao.com%' THEN '豆包'
        WHEN uri_host LIKE '%qianwen.com%' THEN '千问'
        WHEN uri_host LIKE '%kimi.com%' OR uri_host LIKE '%moonshot.cn%' THEN 'Kimi'
        WHEN uri_host LIKE '%deepseek.com%' THEN 'DeepSeek'
        WHEN uri_host LIKE '%chatgpt.com%' OR uri_host LIKE '%openai.com%' THEN 'ChatGPT/GPT系列'
        WHEN uri_host LIKE '%claude.ai%' OR uri_host LIKE '%anthropic.com%' THEN 'Claude系列'
        WHEN uri_host LIKE '%gemini.google.com%' OR uri_host = 'generativelanguage.googleapis.com' THEN 'Gemini系列'
        WHEN uri_host LIKE '%perplexity.ai%' THEN 'Perplexity AI'
        WHEN uri_host LIKE '%mistral.ai%' THEN 'Mistral系列'
        WHEN uri_host LIKE '%hunyuan.tencent.com%' OR uri_host LIKE '%yuanbao.tencent.com%'
            OR uri_host IN ('hunyuan.tencentcloudapi.com', 'api.hunyuan.cloud.tencent.com') THEN '混元/腾讯元宝'
        WHEN uri_host LIKE '%chatglm.cn%' OR uri_host LIKE '%zhipu.ai%'
            OR uri_host = 'open.bigmodel.cn' THEN '智谱清言'
        WHEN uri_host IN ('yiyan.baidu.com', 'wenxin.baidu.com') THEN '文心一言'
        WHEN uri_host LIKE '%xfyun.cn%'
            OR uri_host IN ('spark-api-open.xf-yun.com', 'spark-api.xf-yun.com',
                            'maas-coding-api.cn-huabei-1.xf-yun.com') THEN '讯飞星火'
        WHEN uri_host LIKE '%minimax.chat%' OR uri_host IN ('api.minimaxi.com') THEN 'MiniMax'
        WHEN uri_host IN ('tiangong.kunlun.com', 'api-maas.singularity-ai.com') THEN '天工'
        WHEN uri_host LIKE '%stepfun.com%' OR uri_host LIKE '%stepchat.com%' THEN 'Step系列'
        WHEN uri_host IN ('aip.baidubce.com', 'qianfan.baidubce.com') THEN '百度智能云API'
        WHEN uri_host IN ('ark.cn-beijing.volces.com') THEN '火山方舟API'
        WHEN uri_host IN ('dashscope.aliyuncs.com', 'token-plan.cn-beijing.maas.aliyuncs.com',
                          'coding.dashscope.aliyuncs.com') THEN '阿里云百炼API'
        WHEN uri_host IN ('api.lkeap.cloud.tencent.com') THEN '腾讯云API'
        WHEN uri_host IN ('api.siliconflow.cn') THEN '硅基流动API'
        WHEN uri_host IN ('app.10099.com.cn') THEN '中国广电营业厅'
        WHEN uri_host IN ('h5.10099.com.cn', 'partner.10099.com.cn') THEN '广电行销宝'
        WHEN uri_host IN ('app.10010.com', 'ltgz-ra.10010.cn', 'ltgz.unicom.net.cn', 'msgo.10010.com',
                          'nisportal.10010.com', 'playback.newbuy.chinaunicom.cn', 'res.mall.10010.cn',
                          'wxxapp.chinaunicom.cn') THEN '联通公众'
        ELSE NULL
    END AS APPLLM_NAME,
    a.UL_DATA,
    a.DL_DATA,
    a.DUR_TIME
FROM dw_gprs_url_host_day a
WHERE a.day_id = '20260918'
  AND a.home_prov = '371'
  -- 预过滤：只保留可能命中 AI 分类的 uri_host
  -- 绝大多数非目标流量在此被丢弃，不再进入 CASE WHEN
  -- 前提：下游不需要保留 APPLLM_NAME 为 NULL 的行
  AND a.uri_host RLIKE '(chatgpt|openai|claude|anthropic|gemini|generativelanguage|perplexity|mistral|deepseek|doubao|qianwen|hunyuan|yuanbao|kimi|moonshot|chatglm|zhipu|bigmodel|yiyan|wenxin|xfyun|xf-yun|minimax|tiangong|kunlun|singularity-ai|stepfun|stepchat|aip\.baidubce|qianfan\.baidubce|ark\.cn-beijing\.volces|dashscope|aliyuncs|token-plan|lkeap\.cloud\.tencent|siliconflow|10099|10010|unicom\.net\.cn)'
  AND length(a.serv_number) = 11        -- 号码长度 11 位
  AND substr(a.serv_number, 1, 1) = '1' -- 开头是 1
  -- 排除特殊号段：多个 substr 合并为 1 个 rlike
  AND a.serv_number NOT RLIKE '^(12|148|144|1849|1724|1729|1721|1722|1723|1725|1727|14764|14765|14766|17892|17893|17894)';

-- ============================================
-- 验证与后续排查
-- 1. 对比优化前后结果行数是否一致（若加了 rlike 预过滤，只对比匹配行的分类结果）
-- 2. 运行时观察 map task 数，偏少则调 split 参数
-- 3. 若仍慢，优先检查：
--    - home_prov 是否为分区列（分区裁剪）
--    - 源表是否为 ORC 格式
--    - 作业是否在排队等资源（YARN）
-- ============================================
