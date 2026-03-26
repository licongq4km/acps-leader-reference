"""
config.py — Central configuration for the ACPS Leader Agent backend.

Loads settings from .env in the backend directory.
All modules should import from this single config file.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Backend root directory
# ---------------------------------------------------------------------------
BACKEND_DIR: Path = Path(__file__).parent.resolve()

load_dotenv(BACKEND_DIR / ".env")

# Ensure both backend/ and backend/agent/ are importable
for _p in [str(BACKEND_DIR), str(BACKEND_DIR / "agent")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------
AGENT_DIR: Path = BACKEND_DIR / "agent"
SKILL_ROOT: Path = BACKEND_DIR / "skills" / "acps"
SCRIPTS_DIR: Path = SKILL_ROOT / "scripts"
SKILL_MD: Path = SKILL_ROOT / "SKILL.md"
LOG_FILE: Path = BACKEND_DIR / "service.log"

# ---------------------------------------------------------------------------
# LLM settings
# ---------------------------------------------------------------------------
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME: str = os.getenv("MODEL_NAME", "gpt-4o")

# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------
LEADER_AIC: str = os.getenv("LEADER_AIC", "leader-acps-agent")

# ---------------------------------------------------------------------------
# MCP server settings
# ---------------------------------------------------------------------------
MCP_SERVER_PORT: int = int(os.getenv("MCP_SERVER_PORT", "7004"))
MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", f"http://localhost:{MCP_SERVER_PORT}/sse")

# ---------------------------------------------------------------------------
# Service settings
# ---------------------------------------------------------------------------
SERVICE_HOST: str = os.getenv("SERVICE_HOST", "0.0.0.0")
SERVICE_PORT: int = int(os.getenv("SERVICE_PORT", "7002"))
