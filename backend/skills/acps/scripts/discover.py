"""
discover.py — ADP discovery tool

Queries the discovery server for agents matching a given query.
Caches the full ACS document locally; returns only a slim summary.

Usage:
    python discover.py --query "chess game" [--limit 5]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import httpx
import yaml


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.join(SCRIPT_DIR, "..")
STATE_DISCOVERY_DIR = os.path.join(SKILL_ROOT, "state", "discovery")
CONFIG_FILE = os.path.join(STATE_DISCOVERY_DIR, "discovery_config.yaml")


def _load_discovery_url() -> str:
    """Read discovery URL from discovery_config.yaml.
    Prefers custom_url; falls back to default_url if custom_url is empty.
    """
    default_url = "https://ioa.pub/discovery/acps-adp-v2/discover"
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        custom = (cfg.get("custom_url") or "").strip()
        if custom:
            return custom
        return cfg.get("default_url") or default_url
    except (FileNotFoundError, yaml.YAMLError):
        return default_url


def _extract_endpoint_url(acs: dict) -> str:
    """Extract the best RPC endpoint URL from an ACS document."""
    endpoints = acs.get("endPoints") or []
    for ep in endpoints:
        protocol = (ep.get("protocol") or "").lower()
        if "aip" in protocol or "rpc" in protocol:
            url = ep.get("url", "")
            if url:
                return url
    if endpoints:
        return endpoints[0].get("url", "")
    return ""


def _build_skills_summary(acs: dict) -> str:
    """Summarise all skills as a single string."""
    skills = acs.get("skills") or []
    parts = []
    for skill in skills:
        name = skill.get("name", "")
        desc = skill.get("description", "")
        if name or desc:
            parts.append(f"{name}: {desc}".strip(": "))
    return " | ".join(parts)


def _build_normalized_summary(acs: dict, ranking: int) -> dict:
    """Extract slim fields for returning to the main agent."""
    return {
        "aic": acs.get("aic", ""),
        "name": acs.get("name", ""),
        "description": acs.get("description", ""),
        "active": acs.get("active", True),
        "skills_summary": _build_skills_summary(acs),
        "endpoint_url": _extract_endpoint_url(acs),
        "protocol_version": acs.get("protocolVersion", ""),
        "ranking": ranking,
    }


def _cache_acs(acs: dict, ranking: int, source_url: str) -> None:
    """Write the full ACS plus metadata to state/discovery/<aic>.json."""
    aic = acs.get("aic", "unknown")
    os.makedirs(STATE_DISCOVERY_DIR, exist_ok=True)
    cache_path = os.path.join(STATE_DISCOVERY_DIR, f"{aic}.json")
    payload = {
        "raw_payload": acs,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "source": source_url,
        "normalized_summary": _build_normalized_summary(acs, ranking),
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def discover(query: str, limit: int = 5) -> dict:
    """Run discovery and return a slim summary list."""
    discovery_url = _load_discovery_url()

    last_error = None
    for attempt in range(3):
        try:
            response = httpx.post(
                discovery_url,
                json={"query": query, "limit": limit, "type": "explicit"},
                headers={"Content-Type": "application/json"},
                timeout=120.0, 
                verify=False,
            )
            response.raise_for_status()
            break
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_error = str(e)
            if attempt < 2:
                import time
                time.sleep(3)
            continue
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status >= 500 and attempt < 2:
                last_error = f"HTTP {status}"
                import time
                time.sleep(3)
                continue
            return {
                "success": False,
                "error": f"HTTP {status} from discovery server",
                "error_type": "discovery_error",
            }
    else:
        return {
            "success": False,
            "error": f"Discovery server unreachable: {last_error}",
            "error_type": "discovery_error",
        }

    data = response.json()
    result = data.get("result") or {}
    acs_map = result.get("acsMap") or {}

    # Collect and rank agent skills
    agent_skills = []
    for group in result.get("agents") or []:
        for skill in group.get("agentSkills") or []:
            agent_skills.append(skill)
    agent_skills.sort(key=lambda s: s.get("ranking", 999))

    if not agent_skills:
        return {
            "success": False,
            "error": f"No agents found for query: {query}",
            "error_type": "discovery_error",
        }

    # Build summaries and cache full ACS
    summaries = []
    for skill_entry in agent_skills:
        aic = skill_entry.get("aic", "")
        ranking = skill_entry.get("ranking", 99)
        acs = acs_map.get(aic)  # full ACS payload for the matched agent
        if not isinstance(acs, dict):
            continue
        _cache_acs(acs, ranking, discovery_url)
        summaries.append(_build_normalized_summary(acs, ranking))

    return {
        "success": True,
        "summary": f"Discovered {len(summaries)} agent(s) for query: {query}",
        "data": {
            "agents": summaries,
            "total": len(summaries),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover partner agents via ADP")
    parser.add_argument("--query", required=True, help="Natural-language capability query")
    parser.add_argument("--limit", type=int, default=5, help="Max number of results")
    args, _ = parser.parse_known_args()  # ignore extra args injected by run_python

    result = discover(args.query, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
