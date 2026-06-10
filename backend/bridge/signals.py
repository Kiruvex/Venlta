"""信号名常量集中管理

信号实际定义在 VenltaBridge 中，此文件保留是为了：
1) 集中管理信号名常量供测试使用
2) 未来信号抽取到独立类时减少迁移成本
"""

# 信号名称常量（与 VenltaBridge 中 Signal 定义一一对应）
SIGNAL_PROXY_STATE_CHANGED = "proxyStateChanged"
SIGNAL_LOG_EMITTED = "logEmitted"
SIGNAL_TRAFFIC_STATS_UPDATED = "trafficStatsUpdated"
SIGNAL_CONNECTIONS_UPDATED = "connectionsUpdated"
SIGNAL_LATENCY_RESULT = "latencyResult"
SIGNAL_SUBSCRIPTION_UPDATED = "subscriptionUpdated"
SIGNAL_SPEED_RESULT = "speedResult"
SIGNAL_CONNECTION_CLOSED = "connectionClosed"
