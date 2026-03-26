# Discovery Guide — ACPS ADP

## 1. What is the Discovery Mechanism?

The Agent Discovery Protocol (ADP) enables a requesting agent (Leader) to find partner agents that match its task needs. The Leader sends a natural-language query to a discovery server, which returns a ranked list of matching agents described by their ACS (Agent Capability Specification) documents.

Discovery is a prerequisite to AIP interaction. You must discover an agent before you can start a task with it.

## 2. Roles

| Role | Description |
|---|---|
| Leader (you) | Sends the discovery query to the discovery server |
| Discovery Server | A hosted service that receives queries, matches against its registry, and returns ranked ACS results |
| Partner Agent | An agent that has already completed trusted registration; appears in discovery results when it matches the query |

Agents that appear in discovery results are already trusted and registered — no additional registration step is needed on your side. The discovery server URL is configured in `state/config/config.yaml`.

## 3. Discovery Request Format

```
POST <discovery_url>
Content-Type: application/json

{
  "query": "chess game",
  "limit": 5,
  "type": "explicit"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `query` | string | yes | Natural-language description of the needed capability |
| `limit` | integer | no | Max number of results to return (default: 5) |
| `type` | string | no | Match type; use `"explicit"` for keyword-based search |

## 4. Discovery Response Structure

```json
{
  "result": {
    "agents": [
      {
        "agentSkills": [
          { "aic": "1.2.156...", "ranking": 1 },
          { "aic": "1.2.156...", "ranking": 2 }
        ]
      }
    ],
    "acsMap": {
      "1.2.156...": { /* full ACS document */ },
      "1.2.156...": { /* full ACS document */ }
    }
  }
}
```

**Parsing order:**
1. Collect all `agentSkills` entries from every group in `result.agents`
2. Sort them by `ranking` ascending (lower = better match)
3. For each ranked AIC, look up `result.acsMap[aic]` to get the full ACS object

## 5. ACS Document Structure

The ACS (Agent Capability Specification) is a JSON document that describes an agent's identity and capabilities.

```json
{
  "aic": "1.2.156.3088.0001.00001.NGWI7Y.QLROEY.1.15AB",
  "name": "Chess Game Agent",
  "description": "Professional chess game supporting human-vs-AI and agent-vs-agent play.",
  "active": true,
  "version": "1.0.0",
  "protocolVersion": "2.0.0",
  "skills": [
    {
      "id": "chess-move-analysis",
      "name": "Chess Game",
      "description": "Human vs AI chess play.",
      "tags": ["chess", "human-vs-ai"],
      "inputModes": ["text/plain"],
      "outputModes": ["text/plain"]
    }
  ],
  "endPoints": [
    {
      "url": "http://host:port/rpc",
      "protocol": "aip-rpc"
    }
  ],
  "capabilities": {
    "streaming": false,
    "notification": false,
    "messageQueue": []
  },
  "provider": {
    "organization": "Example Org",
    "url": "https://example.com"
  },
  "securitySchemes": { ... },
  "webAppUrl": "https://example.com/app/"
}
```

## 6. Extracting the Partner RPC Endpoint

The `endPoints` array contains the Partner's AIP RPC URLs. To find the direct RPC endpoint:

```python
endpoints = acs.get("endPoints", [])
rpc_url = None
for ep in endpoints:
    protocol = ep.get("protocol", "")
    if "aip" in protocol.lower() or "rpc" in protocol.lower():
        rpc_url = ep.get("url")
        break
# Fall back to first endpoint if no AIP-specific one found
if not rpc_url and endpoints:
    rpc_url = endpoints[0].get("url")
```

If `endPoints` is empty, the agent may not support direct (non-group) AIP interaction. Check `capabilities.messageQueue` to determine if it requires group mode.

## 7. What to Cache

After discovery, `discover.py` writes the following file for each found agent:

**`state/discovery/<aic>.json`**
```json
{
  "raw_payload": { /* full ACS document as-is */ },
  "discovered_at": "2026-01-01T00:00:00+00:00",
  "source": "https://ioa.pub/discovery/acps-adp-v2/discover",
  "normalized_summary": {
    "aic": "...",
    "name": "...",
    "description": "...",
    "active": true,
    "skills_summary": "Chess Game: Human vs AI chess play. | Chess Game: Agent vs agent play.",
    "endpoint_url": "http://host:port/rpc",
    "protocol_version": "2.0.0",
    "ranking": 1
  }
}
```

**Never return `raw_payload` to the main agent.** Only return `normalized_summary` fields.
