-- =============================================================================
-- 智能健康管家 —— PostgreSQL 16 初始化脚本（L2 摘要记忆）
-- =============================================================================
-- 执行方式（任选其一）：
--   1) 命令行（Windows PowerShell）：
--        psql -U postgres -h 127.0.0.1 -p 5432 -d postgres -f sql/init_postgres.sql
--   2) 用 Navicat / pgAdmin 打开本文件直接执行；
--   3) 什么都不做 —— 程序首次读写摘要记忆时会自动执行等价的
--      CREATE TABLE IF NOT EXISTS 自愈建表（见
--      app/ai/agent/memory/save/summary_memory.py 的 ensure_table()）。
--
-- 说明（重要，别和 MySQL 搞混）：
--   * sql/init.sql 是 **MySQL** 脚本，负责用户/档案/用药/告警/知识文档；
--   * 本文件是 **PostgreSQL** 脚本，只负责摘要记忆 conversation_summary；
--   * LangGraph 的 Checkpointer 表（checkpoints / checkpoint_writes 等）
--     由程序启动时 `AsyncPostgresSaver.setup()` 自动创建（幂等），无需手工建。
--
-- 存储分工：
--   PostgreSQL  ---  conversation_summary（摘要记忆）+ Checkpointer 状态表
--   MySQL       ---  用户 / 健康档案 / 用药 / 告警 / 知识文档
-- =============================================================================

-- 会话摘要表：一个 session_id 只保留一条最新摘要（upsert）
CREATE TABLE IF NOT EXISTS conversation_summary (
    session_id  TEXT PRIMARY KEY,
    summary     TEXT,
    update_time TIMESTAMPTZ DEFAULT NOW()
);

-- 按更新时间倒序查询（运维排查用，可选）
CREATE INDEX IF NOT EXISTS idx_conversation_summary_update_time
    ON conversation_summary (update_time DESC);

-- ---------------------------------------------------------------------------
-- 验证：应能看到 conversation_summary 表
-- ---------------------------------------------------------------------------
-- \d conversation_summary
-- SELECT session_id, left(summary, 40) AS summary_head, update_time
--   FROM conversation_summary ORDER BY update_time DESC LIMIT 10;
