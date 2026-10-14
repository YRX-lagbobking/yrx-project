"""
Web 接入层包

包含八个子路由包（均导出与模块同名的 Router 实例）：
    chat_router        /create_session、/chat（SSE）、/chat/skill
    voice_router       /voice/ws（WS）、/voice/chat、/voice/tts
    websocker_router   /ws/notify（WS）+ ws_manager 连接管理器
    report_router      /report/daily、/report/weekly、/report/download
    alert_router       /alert/list、/alert/{alert_id}/confirm、/alert/detect
    user_router        /user/register、/user/login、/user/me、/user/bind_family
    system_router      /health
    default_page_router / → /static/login.html

约定：图智能体从 request.app.state.health_agent（WebSocket 用 websocket.app.state.health_agent）取用。
本文件保持为空包声明，不在此处导入子模块，避免 app/web 变成隐式聚合入口。
"""
