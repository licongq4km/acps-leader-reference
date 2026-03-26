"""
logger.py — Logging helper for the ACPS Leader Agent.

当通过 service 启动时，根 logger 已由 service/main.py 配置。
当通过 CLI (agent/main.py) 独立运行时，自动配置基础日志到 stderr。
"""
import logging
import sys

_fallback_configured = False


def _ensure_fallback():
    """如果根 logger 尚未配置（CLI 模式），设置基础日志输出。"""
    global _fallback_configured
    if _fallback_configured:
        return
    root = logging.getLogger()
    if root.handlers:
        _fallback_configured = True
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    _fallback_configured = True


def get_logger(name: str) -> logging.Logger:
    """返回 acps_leader 命名空间下的子 logger。"""
    _ensure_fallback()
    return logging.getLogger(f"acps_leader.{name}")
