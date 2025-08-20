# System Architectures

## Supervisor Mode Architecture

The Supervisor orchestrates multiple Codex instances for parallel security testing with intelligent task distribution and vulnerability triage.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              SUPERVISOR PROCESS                                 │
│                                                                                 │
│  ┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────────────┐ │
│  │ SupervisorTools │    │  Orchestrator    │    │    Context Manager          │ │
│  │                 │    │                  │    │                             │ │
│  │ • spawn_codex   │    │ • Task planning  │    │ • 200K token limit          │ │
│  │ • list_instances│────┤ • Instance coord │────┤ • Auto-summarization        │ │
│  │ • read_logs     │    │ • Progress track │    │ • Context preservation      │ │
│  │ • submit()      │    │ • Strategic mgmt │    │                             │ │
│  │ • web_search    │    │                  │    │ Summarization Model:        │ │
│  │ • todo mgmt     │    │ Benchmark Mode:  │    │ openai/o4-mini              │ │
│  │                 │    │ ├─► Normal:      │    │                             │ │
│  │                 │    │ │   Route to     │    │                             │ │
│  │                 │    │ │   Triage       │    │                             │ │
│  │                 │    │ └─► Benchmark:   │    │                             │ │
│  │                 │    │     Direct Slack│    │                             │ │
│  └─────────────────┘    └──────────────────┘    └─────────────────────────────┘ │
│           │                       │                                             │
│           │                       │                                             │
│           ▼                       ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                        INSTANCE MANAGER                                     │ │
│  │                                                                             │ │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │ │
│  │  │ Codex Instance  │    │ Codex Instance  │    │ Codex Instance  │   ...   │ │
│  │  │   ID: web-1     │    │   ID: privesc-2 │    │   ID: enum-3    │         │ │
│  │  │                 │    │                 │    │                 │         │ │
│  │  │ Specialist:     │    │ Specialist:     │    │ Specialist:     │         │ │
│  │  │ web             │    │ linux-privesc   │    │ enumeration     │         │ │
│  │  │                 │    │                 │    │                 │         │ │
│  │  │ Workspace:      │    │ Workspace:      │    │ Workspace:      │         │ │
│  │  │ /workspaces/    │    │ /workspaces/    │    │ /workspaces/    │         │ │
│  │  │ web-1/          │    │ privesc-2/      │    │ enum-3/         │         │ │
│  │  └─────────────────┘    └─────────────────┘    └─────────────────┘         │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   │ submit() calls
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              TRIAGE SYSTEM                                     │
│                                                                                 │
│  ┌─────────────────┐         ┌─────────────────────────────────────────────────┐ │
│  │ TriageManager   │         │            TriagerInstance Pool                 │ │
│  │                 │         │                                                 │ │
│  │ • Queue mgmt    │────────▶│  ┌─────────────┐  ┌─────────────┐             │ │
│  │ • Instance      │         │  │TriagerInst  │  │TriagerInst  │   ...       │ │
│  │   spawning      │         │  │   abc123    │  │   def456    │             │ │
│  │ • Feedback      │         │  │             │  │             │             │ │
│  │   tracking      │         │  │ 3-Phase     │  │ 3-Phase     │             │ │
│  │                 │         │  │ Workflow    │  │ Workflow    │             │ │
│  │                 │         │  │ + TaskRouter│  │ + TaskRouter│             │ │
│  │                 │         │  └─────────────┘  └─────────────┘             │ │
│  │                 │         │                                                 │ │
│  └─────────────────┘         └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   │ validated findings
                                   ▼
                          ┌─────────────────┐
                          │   SLACK WEBHOOK │
                          │                 │
                          │ Critical/High/  │
                          │ Medium/Low      │
                          │ + CVSS scores   │
                          └─────────────────┘
```

## Autonomous Mode Architecture

Autonomous mode runs a single persistent Codex instance with external LLM driver for extended unattended operation.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           AUTONOMOUS SESSION                                    │
│                                                                                 │
│  ┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────────────┐ │
│  │  Config YAML    │    │   Session Mgmt   │    │       Work Hours            │ │
│  │                 │    │                  │    │                             │ │
│  │ • task_desc     │────┤ • Timestamped    │────┤ • Pacific timezone          │ │
│  │ • target_info   │    │   logs directory │    │ • Configurable hours        │ │
│  │ • scope         │    │ • Resume support │    │ • Pause/resume logic        │ │
│  │ • objectives    │    │ • Heartbeat      │    │ • --ignore-work-hours       │ │
│  │ • constraints   │    │ • Iteration      │    │                             │ │
│  │                 │    │   tracking       │    │                             │ │
│  └─────────────────┘    └──────────────────┘    └─────────────────────────────┘ │
│                                  │                                              │
│                                  ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                        SINGLE CODEX INSTANCE                               │ │
│  │                                                                             │ │
│  │  ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐ │ │
│  │  │ External Driver │         │  Codex Binary   │         │ Session Logs    │ │ │
│  │  │                 │         │                 │         │                 │ │ │
│  │  │ Model:          │◀───────▶│ • Security tools│────────▶│ • iteration_*.  │ │ │
│  │  │ o3/gpt-4o/etc   │         │ • Shell access  │         │   json          │ │ │
│  │  │                 │         │ • File ops      │         │ • context_log.  │ │ │
│  │  │ • Strategic     │         │ • Network tools │         │   txt           │ │ │
│  │  │   planning      │         │ • Specialists:  │         │ • heartbeat.    │ │ │
│  │  │ • Adaptation    │         │   - web         │         │   json          │ │ │
│  │  │ • Persistence   │         │   - linux-privesc│        │ • session_info. │ │ │
│  │  │                 │         │   - enumeration │         │   json          │ │ │
│  │  │                 │         │   - etc.        │         │                 │ │ │
│  │  └─────────────────┘         └─────────────────┘         └─────────────────┘ │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                          │                                      │
│                                          ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                         SANDBOX MODES                                      │ │
│  │                                                                             │ │
│  │  ┌─────────────────┐                    ┌─────────────────┐                 │ │
│  │  │ Normal Mode     │                    │ Full-Auto Mode  │                 │ │
│  │  │                 │                    │                 │                 │ │
│  │  │ • Approvals     │                    │ • No approvals  │                 │ │
│  │  │   required      │                    │ • Workspace-    │                 │ │
│  │  │ • Safety checks │                    │   write sandbox │                 │ │
│  │  │ • User confirm  │                    │ • Continuous    │                 │ │
│  │  │                 │                    │   operation     │                 │ │
│  │  └─────────────────┘                    └─────────────────┘                 │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Key Differences

| Aspect | Supervisor Mode | Autonomous Mode |
|--------|-----------------|-----------------|
| **Instances** | Multiple parallel Codex instances | Single persistent instance |
| **Orchestration** | Strategic task distribution | External LLM driver planning |
| **Triage** | Built-in vulnerability validation | Direct output (no triage) |
| **Duration** | Configurable (typically 60-120 min) | Extended sessions (hours/days) |
| **Specialization** | Automatic specialist routing | Manual mode selection |
| **Resumption** | Session-level resume | Iteration-level resume |
| **Use Case** | Comprehensive parallel testing | Focused extended research |

## Data Flow

### Supervisor Mode
```
Config → Supervisor → [Instance1, Instance2, ...] → Triage → Slack
```

### Autonomous Mode  
```
Config → External LLM → Single Codex → Direct Output
```