"""
记忆存储层（save）

各层的存储介质与降级策略：
    window_memory.py  L1 窗口记忆：Redis List  → 进程内字典（模块级 _FALLBACK）
    summary_memory.py L2 摘要记忆：PostgreSQL  → 进程内字典（连接池为 None 时）
    long_memory.py    L3 长期记忆：Milvus → ChromaDB → 进程内字典（三级降级示例）
    profile_memory.py L4 用户画像：Redis Hash  → 进程内字典

硬性约定：
    1. redis / psycopg_pool / pymilvus / chromadb 全部函数内延迟导入 + try/except，
       未安装依赖时"模块仍可导入、接口仍可调用"；
    2. 所有存储调用都可能失败，失败一律降级并记日志，永不向上抛异常；
    3. 阻塞型客户端（Milvus / Chroma）用 run_in_executor 包装，避免卡住事件循环。
"""
