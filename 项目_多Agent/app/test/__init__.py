"""
app/test 包 —— 教学式自测脚本集合

每个脚本都可独立运行（不依赖 pytest），运行方式统一为：

    # 推荐：以模块方式运行（自动把项目根加入 sys.path）
    python -m app.test.test_skill
    # 也可以直接跑文件（脚本内部已做 sys.path 处理）
    python app/test/test_skill.py

脚本清单与对应需求（需求文档 15.3 需求追踪矩阵）：
    test_agent.py        create_agent + 工具 + 异步流式（FR-01~FR-03）
    test_vosk.py         Vosk + PyAudio 连续语音识别（FR-01 / AC-01、AC-02）
    test_influx.py       时序写入与近 7 天查询（FR-14 / AC-25）
    test_rag.py          切分 / 建库 / 检索（FR-09 / AC-14、AC-15）
    test_detect.py       三层检测 + 手动检测无副作用（FR-06 / AC-08~AC-10）
    test_graph.py        LangGraph 复合意图编排（FR-10 / AC-16~AC-18）
    test_memory.py       四层记忆 save/query（FR-13 / AC-23、AC-24）
    test_skill.py        Skill 渐进式加载（FR-12 / AC-22）
    test_security.py     JWT + RBAC 数据权限（FR-11 / AC-19、AC-20）
    test_notify.py       异步通知与本地兜底队列（FR-07 / AC-11、AC-12）
    test_report.py       日报/周报生成与 Word 导出（FR-08 / AC-13）
    test_health_check.py 健康检查与降级模式（FR-15 / AC-26）

共同约定：
    1. 缺依赖 / 缺服务时**打印降级说明并正常退出**，不抛异常（NFR-02）；
    2. 顶部注释写明推荐运行方式；
    3. 关键位置打印中文说明，便于课堂演示与理解。
"""
