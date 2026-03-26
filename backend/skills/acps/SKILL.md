---
name: acps-protocol
description: Guides the Leader agent through ADP discovery and AIP task delegation (discover, start, poll, continue, complete, cancel). Use when the user asks to find a partner agent, delegate a task, poll task status, or manage an ACPS/AIP task lifecycle.
---

# ACPS Protocol Skill

## Overview

This skill enables the Leader Agent to:
1. **Discover** suitable Partner agents via the ADP(Agent Discovery Protocol)
2. **Delegate** tasks to discovered Partners using the AIP(Agent Interaction Protocol)
3. **Monitor** task execution by polling for state changes
4. **Interact** with the human user when Partners require input or confirmation
5. **Complete or cancel** tasks based on user decisions

Use this skill whenever the user asks you to find an agent, assign a task to an agent, or manage an ongoing agent task.

---

## Tools (Scripts)

All scripts are located in `scripts/`. Use the `run_python` tool to execute them.
All scripts output a JSON object to stdout. Always check the `"success"` field first.

Example as follows:
```python
run_python(script_name="discover.py", script_args={"query": "chess game", "limit": 3})
run_python(script_name="start_task.py", script_args={"aic": "...", "task_description": "...", "session_id": "..."})
run_python(script_name="get_task.py", script_args={"task_id": "task-..."})
run_python(script_name="continue_task.py", script_args={"task_id": "task-...", "user_input": "..."})
run_python(script_name="complete_task.py", script_args={"task_id": "task-..."})
run_python(script_name="cancel_task.py", script_args={"task_id": "task-..."})
```

### 1. `discover.py` — Find Partner Agents

```
python scripts/discover.py --query "<capability description>" [--limit 5]
```

| Argument | Required | Description |
|---|---|---|
| `--query` | yes | Natural-language description of the needed capability |
| `--limit` | no | Max number of results (default: 5) |

**Returns** (slim summary only — never the full ACS):
```json
{
  "success": true,
  "summary": "Discovered 2 agent(s) for query: chess game",
  "data": {
    "agents": [
      {
        "aic": "1.2.156...",
        "name": "Chess Game Agent",
        "description": "Professional chess game supporting human vs AI",
        "active": true,
        "skills_summary": "Chess Game: Human vs AI | Chess Game: Agent vs agent",
        "endpoint_url": "http://host:port/rpc",
        "protocol_version": "2.0.0",
        "ranking": 1
      }
    ],
    "total": 2
  }
}
```

After discovery, the full ACS documents are cached at `state/discovery/<aic>.json`.
**Present only `name`, `description`, and `skills_summary` for selection.**

---

### 2. `start_task.py` — Start an AIP Task

```
python scripts/start_task.py \
  --aic <partner_aic> \
  --task_description "what the partner should do" \
  --session_id <session_id> \
  [--task_id <task_id>] \
  [--leader_aic <leader_aic>]
```

| Argument | Required | Description |
|---|---|---|
| `--aic` | yes | Partner AIC from `discover.py` results |
| `--task_description` | yes | The task to delegate, in natural language |
| `--session_id` | yes | Unique session identifier (use a UUID or conversation ID) |
| `--task_id` | no | Auto-generated if omitted |
| `--leader_aic` | no | Your own AIC as Leader|

**Returns:**
```json
{ "success": true, "task_id": "task-...", "state": "accepted", "message": "...", "partner_url": "...", "aic": "..." }
```

---

### 3. `get_task.py` — Poll Task State

**IMPORTANT: This script handles polling internally.** Call it once — it will
block and keep querying the partner until the task leaves the `working`/`accepted`
state, then return. Do NOT call it repeatedly in a loop yourself.

```
python scripts/get_task.py --task_id <task_id> [--leader_aic <leader_aic>]
                            [--poll true|false] [--poll_interval 5]
                            [--poll_timeout 600]
```

| Argument | Required | Default | Description |
|---|---|---|---|
| `--task_id` | yes | — | Task ID returned by `start_task.py` |
| `--leader_aic` | no | `leader-acps-agent` | Your own AIC as Leader |
| `--poll` | no | `true` | Auto-poll until non-working state |
| `--poll_interval` | no | `5` | Seconds between polls |
| `--poll_timeout` | no | `600` | Max total wait time (seconds) |

**Returns:**
```json
{
  "success": true,
  "task_id": "task-...",
  "state": "awaiting-completion",
  "message": "",
  "needs_input": false,
  "awaiting_completion": true,
  "is_terminal": false,
  "products": [
    { "id": "product-...", "name": "Result", "content": "Here is the answer..." }
  ]
}
```

---

### 4. `continue_task.py` — Supply User Input to a Waiting Task

```
python scripts/continue_task.py \
  --task_id <task_id> \
  --user_input "the user's answer or acknowledgment" \
  [--leader_aic <leader_aic>]
```

| Argument | Required | Description |
|---|---|---|
| `--task_id` | yes | Task ID |
| `--user_input` | yes | User's response to the Partner's question |
| `--leader_aic` | no | Your own AIC as Leader |

Only valid when task state is `awaiting-input`. **Do not call this speculatively.**

---

### 5. `complete_task.py` — Confirm Partner Deliverables

```
python scripts/complete_task.py --task_id <task_id> [--leader_aic <leader_aic>]
```

Only valid when task state is `awaiting-completion`. Transitions the task to `completed`.
Call this **only after presenting the Partner's products to the user and getting explicit confirmation**.

---

### 6. `cancel_task.py` — Cancel an Active Task

```
python scripts/cancel_task.py --task_id <task_id> [--leader_aic <leader_aic>]
```

Valid from any non-terminal state. Transitions the task to `canceled`.
Call this when the user explicitly wants to abort, or when the user rejects the Partner's results.

---

## Standard Workflow

```
1. DISCOVER
   python scripts/discover.py --query "<user's need>"
   → Present agent list to user (name + skills_summary only)
   → Ask user to confirm which agent to use

2. START
   python scripts/start_task.py --aic <selected_aic> --task_description "..." --session_id <id>
   → The partner may respond synchronously (blocking) or asynchronously.
   → Check is_terminal in the returned result:
     is_terminal=true  → task already done in one round; read products, go to step 5
     is_terminal=false → partner is still working; continue to step 3
   NOTE: For synchronous partners (e.g. local LLM adapters) is_terminal will be
   true immediately. Do NOT call get_task unnecessarily.

3. WAIT FOR RESULT — only if start_task returned is_terminal=false
   python scripts/get_task.py --task_id <task_id>
   → The script blocks until the task leaves "working"/"accepted"
   → Check returned state:
     "awaiting-input"       → go to step 4a
     "awaiting-completion"  → go to step 4b
     terminal states        → go to step 5
   NOTE: Do NOT call get_task in a loop yourself.

4a. HUMAN INPUT REQUIRED (state = awaiting-input)
    → Read message from get_task result
    → Present Partner's question to the user in natural language
    → Wait for user's reply
    → python scripts/continue_task.py --task_id <id> --user_input "<reply>"
    → Call get_task again (it will poll internally again)

4b. HUMAN CONFIRMATION REQUIRED (state = awaiting-completion)
    → Extract products from get_task result
    → Present deliverables to the user in natural language
    → Ask: "Do you accept these results?"
      If YES → python scripts/complete_task.py --task_id <id>
      If NO  → python scripts/cancel_task.py --task_id <id>

5. DONE
   → Report final state and any product content to the user
```

---

## Plan & Human-in-the-Loop Rules

### Always confirm before acting:
- **Before calling `start_task.py`**: Show the user which agent you selected and what task description you will send. Wait for explicit approval.
- **Before calling `complete_task.py`**: Present the Partner's products to the user. Do not auto-accept.
- **Before calling `cancel_task.py`** (when user-initiated): Confirm the user really wants to cancel.

### Act autonomously (no confirmation needed):
- Calling `discover.py` with the user's stated query
- Calling `get_task.py` for status polling
- Retrying on transient network errors (up to 2 times)

### Never:
- Start a task without user confirmation of the selected agent
- Auto-complete a task without user approval of the results
- Return the full ACS document (`raw_payload`) to the user — show only `normalized_summary` fields
- Overwrite `state/discovery/discovery_config.yaml`

---

## Local Cache Rules

### ACS cache — `state/discovery/<aic>.json`

Written by `discover.py`. Contains:
```json
{
  "raw_payload":         { /* full ACS document — do not expose to user */ },
  "discovered_at":       "ISO 8601 timestamp",
  "source":              "discovery server URL",
  "normalized_summary":  { "aic", "name", "description", "active", "skills_summary", "endpoint_url", "protocol_version", "ranking" }
}
```

**Rule**: Never return `raw_payload` to the main agent. Only use `normalized_summary`.

### Task state cache — `state/tasks/<task_id>.json`

Written and updated by all AIP scripts. Contains:
```json
{
  "task_id":      "task-...",
  "aic":          "partner aic",
  "partner_url":  "http://...",
  "session_id":   "session-...",
  "state":        "current TaskState string",
  "last_result":  { /* last TaskResult from SDK — internal, do not expose */ },
  "error_context": null,
  "created_at":   "ISO 8601",
  "updated_at":   "ISO 8601"
}
```

**Rule**: Read `state` from this cache before calling any AIP command to validate it is allowed.

### Discovery URL config — `state/discovery/discovery_config.yaml`

User-editable. Never overwrite it from scripts.
```yaml
default_url: "https://ioa.pub/discovery/acps-adp-v2/discover"
custom_url: ""
```

---

## Error Handling Summary

| Error type | Symptom | Action |
|---|---|---|
| `cache_miss` | ACS or task JSON not found | Run `discover.py` first; verify task_id |
| `discovery_error` | No agents found or server unreachable | Retry 2x; suggest rephrasing query or checking config |
| `http_error` | HTTP 4xx/5xx from Partner | 4xx: don't retry, report; 5xx/408: retry 2x then report |
| `rpc_error` | JSON-RPC `error` object in response | Map code to action (see `references/error_handling_guide.md`) |
| `task_error` | State is `failed` or `rejected` | Read failure message; report to user; ask what to do next |
| `state_error` | Command not valid for current state | Check current state; use correct command |

For detailed error codes and decision trees, see `references/error_handling_guide.md`.

---

## Few-Shot Examples

### Example 1: Simple task, completed in one round

**User**: "Help me play chess against an AI agent."

```
Step 1 — Discover
python scripts/discover.py --query "chess game human vs AI"
→ Found: "Chess Game Agent" (aic: 1.2.156...)
→ Tell user: "I found 'Chess Game Agent' — supports human vs AI chess. Use this agent?"

Step 2 — User confirms → Start task
python scripts/start_task.py \
  --aic "1.2.156..." \
  --task_description "Start a human vs AI chess game. I am the human player." \
  --session_id "sess-abc123"
→ task_id: "task-xyz"
→ state: "accepted"

Step 3 — Poll
python scripts/get_task.py --task_id "task-xyz"
→ state: "awaiting-completion"
→ products: [{ "content": "Game started. It's your move. Board: ..." }]

Step 4 — Present to user, ask confirmation
"The chess agent has set up the board. Here is the initial position: [...]
Accept and start playing?"
→ User says yes

Step 5 — Complete
python scripts/complete_task.py --task_id "task-xyz"
→ state: "completed"
```

---

### Example 2: Partner asks a clarifying question

**User**: "Find an agent to help me with Beijing travel recommendations."

```
Step 1 — Discover
python scripts/discover.py --query "Beijing travel recommendations"

Step 2 — Start
python scripts/start_task.py \
  --aic "..." \
  --task_description "Recommend places to visit in Beijing." \
  --session_id "sess-def456"
→ task_id: "task-abc"
→ state: "working"

Step 3 — Poll
python scripts/get_task.py --task_id "task-abc"
→ state: "awaiting-input"
→ message: "What area of Beijing are you interested in? Urban center or suburbs?"

Step 4 — Ask user
"The agent needs more info: What area of Beijing are you interested in?"
→ User: "Urban center, near the Forbidden City."

Step 5 — Continue
python scripts/continue_task.py \
  --task_id "task-abc" \
  --user_input "Urban center, near the Forbidden City."
→ state: "working"

Step 6 — Poll again
python scripts/get_task.py --task_id "task-abc"
→ state: "awaiting-completion"
→ products: [{ "content": "Recommended places: Jingshan Park, Beihai Park..." }]

Step 7 — Present and confirm
"The agent recommends: Jingshan Park, Beihai Park... Accept?"
→ User confirms → complete_task.py
```

---

## Detailed Reference Documents

For complete protocol specifications and extended error handling guidance, read the files in `references/`:

- `references/adp_guide.md` — ADP discovery flow, ACS structure, endpoint extraction
- `references/aip_guide.md` — AIP task state machine, command types, RPC format, polling pattern
- `references/error_handling_guide.md` — All error types, retry rules, decision tree

**Always consult these files if you encounter an unexpected error or need protocol-level details not covered above.**
