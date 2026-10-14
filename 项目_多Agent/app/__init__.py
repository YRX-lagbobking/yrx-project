"""
智能健康管家多智能体系统 —— 应用包

分层结构：
    app.core        配置、安全、日志、降级、检测、异步任务
    app.dao         数据访问层（MySQL，参数化查询 + 数据权限）
    app.ai          AI 层：模型、提示词、工具、智能体（编排/记忆/语音）
    app.skills      Skill 技能包（渐进式加载）
    app.web         FastAPI 路由层（HTTP / SSE / WebSocket）
    app.html        前端静态页面
"""
