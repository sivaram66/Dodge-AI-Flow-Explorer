# Dodge AI Flow Explorer

A graph-based data explorer for SAP Order-to-Cash (O2C) data with an LLM-powered conversational query interface.

**Notion Deep Dive:** https://www.notion.so/Dodge-AI-Flow-Explorer-Deep-Dive-32e29415e9a38030b2eee219bf0a2577

**Live Demo:** https://dodge-ai-flow-explorer.vercel.app

> **Note:** Backend is hosted on Render free tier. 
> First load may take 30-60 seconds to wake up. 
> The app shows a loading screen while this happens.

---

## What It Does

The system ingests a real SAP O2C dataset, models it as a graph of interconnected business entities, and lets users explore it visually and query it in natural language.

- **Graph Visualization** — 674 nodes (Sales Orders, Deliveries, Billing Documents, Journal Entries, Payments, Customers, Products, Plants) and 1,066 edges rendered as a force-directed graph. Click any node to inspect its metadata and relationships.
- **Conversational Query Interface** — Ask questions in plain English. The system translates them to SQL, executes against the real database, and streams back grounded answers. Referenced nodes highlight in the graph.
- **Guardrails** — Out-of-scope queries (general knowledge, math, creative writing) are rejected before reaching the LLM.

---
## Graph Navigation

One of the core features of this system is that every node 
is connected to related entities. You are not just viewing 
data — you are navigating a live graph. Clicking any 
connected item in the inspector opens that entity, letting 
you trace the complete business flow from one end to the 
other without typing a single query.
Letting you trace the complete O2C business flow from Customer → Sales Order → Delivery → Billing → Journal Entry without typing a single query.

→ [See the complete O2C flow walkthrough with screenshots](https://www.notion.so/32e29415e9a38030b2eee219bf0a2577?source=copy_link#32e29415e9a380fbb57dc2e555345ae3)

---

## Node Highlighting

When you ask a question in the chat, every node referenced 
in the answer lights up in the graph in real time.

Ask "Which products have the most billing documents?" — 
the product nodes highlight gold.
The graph and the chat work together, not separately.

→ [See node highlighting in action with screenshots](https://www.notion.so/32e29415e9a38030b2eee219bf0a2577?source=copy_link#32e29415e9a380eeabb9cafc614103b3)

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

→ [Architecture explained in detail](https://www.notion.so/Dodge-AI-Flow-Explorer-Deep-Dive-32e29415e9a38030b2eee219bf0a2577?source=copy_link#32e29415e9a380b0b693f4e4a222bf47)

---

## Database Choice

**PostgreSQL on Neon** was chosen over SQLite for two reasons.

First, deployment. Free hosting platforms (Render, Railway) use ephemeral filesystems — SQLite files get wiped on every redeploy. Neon persists data independently of the application server.

Second, correctness. PostgreSQL's `NUMERIC(15,2)` type handles financial amounts without floating-point precision loss. SQLite's `REAL` type would silently corrupt values like `249.15` INR.

→ [Database decisions explained in detail](https://www.notion.so/Dodge-AI-Flow-Explorer-Deep-Dive-32e29415e9a38030b2eee219bf0a2577?source=copy_link#32e29415e9a3804facccd34b621db8c6)
---

## Graph Data Model

A row becomes a **node** if a user would click on it and ask 
"tell me about this." It becomes an **edge** if its job is to 
connect two nodes.

Two graph objects in memory — a summary graph (8 nodes, fast 
initial render) and a full graph (674 nodes keyed by ID for 
O(1) highlight lookup).

---

## LLM Prompting Strategy

### Two-Round Architecture

Every chat message goes through two LLM calls:

**Round 1 (planning)** — `temperature=0`, returns a JSON 
execution plan. If required info is missing, returns a 
clarification request instead. No SQL runs until the user 
provides what's needed.

**Round 2 (streaming)** — `temperature=0.1`, no tools 
parameter. Streams the prose answer. Removing tools from 
Round 2 prevents Groq's 400 error on XML-format second 
tool calls.

Three fixed tools cover the required queries with verified SQL. 
A fourth dynamic tool validates LLM-generated SQL through 
sqlglot AST parsing before execution.

→ [LLM prompting strategy explained in detail](https://www.notion.so/Dodge-AI-Flow-Explorer-Deep-Dive-32e29415e9a38030b2eee219bf0a2577?source=copy_link#32e29415e9a3802bbf61f7e1ead5d36e)
---

## Guardrails

Two layers — regex patterns reject obvious cases (poems, math, 
general knowledge) with zero latency before any LLM call.

A principle-based LLM classifier handles ambiguous cases using 
three conditions that must ALL be true for in-scope: requires 
a database query, data exists in this dataset, cannot be 
answered from general knowledge alone.

On error the classifier fails open — a Groq outage should not 
kill the chat feature.

→ [Guardrails design explained in detail](https://www.notion.so/Dodge-AI-Flow-Explorer-Deep-Dive-32e29415e9a38030b2eee219bf0a2577?source=copy_link#32e29415e9a380e09bc2f16fb7b1c00a)
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
