# Dodge AI Flow Explorer

A graph-based data explorer for SAP Order-to-Cash (O2C) data with an LLM-powered conversational query interface.

**Live Demo:** https://dodge-ai-flow-explorer.vercel.app
> ⚠️ **Note:** Backend is hosted on Render free tier. 
> First load may take 30-60 seconds to wake up. 
> The app shows a loading screen while this happens.

---

## What It Does

The system ingests a real SAP O2C dataset, models it as a graph of interconnected business entities, and lets users explore it visually and query it in natural language.

- **Graph Visualization** — 674 nodes (Sales Orders, Deliveries, Billing Documents, Journal Entries, Payments, Customers, Products, Plants) and 1,066 edges rendered as a force-directed graph. Click any node to inspect its metadata and relationships.
- **Conversational Query Interface** — Ask questions in plain English. The system translates them to SQL, executes against the real database, and streams back grounded answers. Referenced nodes highlight in the graph.
- **Guardrails** — Out-of-scope queries (general knowledge, math, creative writing) are rejected before reaching the LLM.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React + Vite + Tailwind CSS + react-force-graph-2d |
| Backend | Python + FastAPI |
| Database | PostgreSQL on Neon (cloud, serverless) |
| LLM | Groq API (llama-3.3-70b-versatile) |
| Deployment | Render (backend) + Vercel (frontend) |

---

## Architecture

### Data Flow

```
JSONL Dataset (19 folders)
        ↓
loader.py (psycopg2 batch inserts)
        ↓
PostgreSQL on Neon
        ↓
graph_builder.py (startup, in-memory)
        ↓
FastAPI routes (/api/graph/*, /api/chat)
        ↓
React Frontend
```

### Backend Structure

```
backend/app/
├── main.py              # FastAPI app, lifespan, CORS
├── config.py            # pydantic-settings, reads .env
├── database.py          # asyncpg connection pool
├── routers/
│   ├── graph.py         # GET /api/graph/* endpoints
│   └── chat.py          # POST /api/chat endpoint
└── services/
    ├── graph_builder.py # builds in-memory graph at startup
    ├── llm.py           # Groq integration, streaming, tool execution
    ├── query_tools.py   # SQL tool definitions + dynamic SQL fallback
    └── guardrails.py    # in-scope/out-of-scope classifier
```

### Frontend Structure

```
frontend/src/
├── App.jsx                    # layout, highlight state
├── components/
│   ├── GraphCanvas.jsx        # react-force-graph-2d canvas
│   ├── NodeInspector.jsx      # node detail panel on click
│   ├── ChatPanel.jsx          # chat UI with SSE streaming
│   └── ChatMessage.jsx        # message renderer with markdown
├── hooks/
│   └── useHighlight.js        # shared highlight Set state
└── services/
    └── api.js                 # all fetch/SSE calls
```

---

## Database Choice

**PostgreSQL on Neon** was chosen over SQLite for two reasons.

First, deployment. Free hosting platforms (Render, Railway) use ephemeral filesystems — SQLite files get wiped on every redeploy. Neon persists data independently of the application server.

Second, correctness. PostgreSQL's `NUMERIC(15,2)` type handles financial amounts without floating-point precision loss. SQLite's `REAL` type would silently corrupt values like `249.15` INR.

**Key schema decisions:**

- Field names preserved as camelCase from the source JSONL to simplify the loader. Every camelCase column is double-quoted in PostgreSQL queries to prevent case folding (`"salesOrder"` not `salesOrder`).
- `statement_cache_size=0` on the asyncpg pool — required for Neon's PgBouncer connection pooler running in transaction mode, which does not support prepared statements.
- Indices on every join key used by the three core queries — `referenceSdDocument`, `referenceDocument`, `clearingAccountingDocument`, `soldToParty`, `billingDocumentType`.

**Critical join discovery:** The SO→Delivery link does not exist on delivery headers. It lives in `outbound_delivery_items.referenceSdDocument`. Similarly, the Delivery→Billing link goes through `billing_document_items.referenceSdDocument`. This required two additional item tables in the join chain that a naive schema design would miss.

---

## Graph Data Model

### Node/Edge Decision Rule

A row becomes a **node** if it is an entity a user would click on and ask "tell me about this thing." It becomes an **edge** if its job is to connect two nodes.

| Node Type | Source Table | Count |
|---|---|---|
| sales_order | sales_order_headers | 100 |
| delivery | outbound_delivery_headers | 86 |
| billing_doc | billing_document_headers | 163 |
| journal_entry | journal_entry_items (deduped by accountingDocument) | 123 |
| payment | payments_accounts_receivable | 120 |
| customer | business_partners | 8 |
| product | products | 69 |
| plant | plants (only those referenced by deliveries) | 5 |

**Total: 674 nodes, 1,066 edges**

### Two-Graph Architecture

The system maintains two graph objects in memory at startup:

- **Summary graph** — 8 nodes (one per entity type) with counts and flow edges. Served as the initial canvas render. Derived from the full graph in Python — no extra DB queries.
- **Full graph** — individual nodes keyed by ID (`O(1)` lookup for highlight), edge list, and a `by_type` index for expand-on-click.

This design avoids rendering 674 nodes on initial load while keeping the full graph available for chat-driven highlighting.

---

## LLM Prompting Strategy

### Two-Round Architecture

Every chat message goes through two LLM calls:

**Round 1 (planning):** A non-streaming call with `temperature=0` returns a JSON execution plan — a list of tool calls with parameters. If the user's message lacks required information (e.g. asks to trace a flow but provides no billing document ID), Round 1 returns `{"ask": "..."}` instead of steps, and the clarification is shown to the user without executing any SQL.

**Round 2 (streaming):** After tool results are collected, a streaming call with `temperature=0.1` and **no tools parameter** generates the final prose answer. Removing tools from Round 2 prevents the model from attempting a second tool call in XML format, which Groq rejects.

### Tool Design

Three fixed tools cover the required assignment queries with verified SQL:

- `get_top_products_by_billing_count` — verified against real data, returns top N products by billing document frequency
- `trace_document_flow` — full SO→Delivery→Billing→JournalEntry join using the correct item-table join chain
- `get_broken_flows` — UNION ALL of delivered-not-billed and billed-without-delivery using `NOT EXISTS` subqueries

A fourth tool, `execute_query`, handles everything else. It accepts LLM-generated SQL validated through a `sqlglot` AST parser before execution — checking statement type (SELECT only), table whitelist, and a 5-second `statement_timeout` for the dynamic path only.

### Schema Injection

The system prompt gives the LLM the full join chain explicitly because the correct joins go through item tables, not header tables. LLM-generated SQL that joins headers directly produces wrong results without error. The prompt also specifies that all camelCase columns must be double-quoted in PostgreSQL.

### Node Highlighting

After every query, `_extract_node_ids` inspects result rows for known ID columns (`businessPartner`, `salesOrder`, `billingDocument`, etc.) and maps them to graph node IDs. For COUNT queries that return no ID columns, a secondary query fetches the actual entity IDs from the same table. These are emitted as a final SSE `highlight` event after the streaming prose completes.

---

## Guardrails

The guardrail system uses a two-layer approach.

**Layer 1 — Deterministic keyword patterns** run before any LLM call. A set of compiled regex patterns (`re.IGNORECASE`) immediately rejects messages matching phrases like `poem`, `write me`, `capital of`, `how does * work`, `explain what`, and arithmetic expressions. This handles the most obvious off-topic requests with zero latency and zero token cost.

**Layer 2 — LLM classifier** handles ambiguous cases. A fast, cheap model (`llama-3.1-8b-instant`) with `temperature=0` and `max_tokens=80` returns `{"is_in_scope": bool, "reason": "..."}`. The classifier prompt uses a principle-based rule rather than a list of examples: a message is in scope only if all three conditions hold simultaneously — it requires a database query to answer, the data exists in this specific O2C dataset, and the answer cannot be given from general knowledge alone. On error or timeout, the classifier fails open (allows the message through) so a Groq outage does not kill the chat feature.

**Conversation history** is passed to the classifier so follow-up messages like a bare document ID (`91150216`) are not rejected as out-of-scope when they follow an assistant question asking for that ID.

---

## Example Queries

| Query | What It Tests |
|---|---|
| Which products have the most billing documents? | Aggregation, JOIN across billing_document_items and products |
| Trace billing document 91150216 | Four-table join chain through item tables |
| Are there any broken O2C flows? | UNION ALL with NOT EXISTS subqueries |
| Which customer has the most sales orders? | GROUP BY with business_partners join |
| Which plant handles the most deliveries? | Delivery→Plant edge via outbound_delivery_items |
| How many customers are blocked? | Boolean filter on businessPartnerIsBlocked |
| Tell me products purchased by Nelson, Fitzpatrick and Jordan | Multi-step: customer name → ID → order items → products |

---

## Running Locally

```bash
# Clone
git clone https://github.com/your-username/dodgeai-flow-explorer
cd dodgeai-flow-explorer

# Backend
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt

# Add .env
DATABASE_URL=postgresql://...
GROQ_API_KEY=gsk_...

# Apply schema and load data
python init_db.py
python loader.py

# Start backend
uvicorn app.main:app --reload

# Frontend (new terminal)
cd frontend
npm install
npm run dev
```

---

## Submission

- **Live Demo:** https://dodge-ai-flow-explorer.vercel.app
- **GitHub:** https://github.com/your-username/dodgeai-flow-explorer
- **AI Coding Logs:** Included in `/ai-logs/` directory
