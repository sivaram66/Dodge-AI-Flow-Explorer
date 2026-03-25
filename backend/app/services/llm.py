"""
LLM service — plan-execute-stream architecture.

Flow per message:
  Round 0 — Planning call: LLM analyzes the question, returns a JSON execution
             plan (no tools, no streaming).
  Execute  — Python runs each plan step via query_tools, substituting results
             from earlier steps into later steps via {field} placeholders.
  Round 1  — Streaming call: LLM summarizes all results in natural language.
             NO tools parameter — the model never calls tools during streaming.

This eliminates multi-turn tool calling entirely, avoiding llama-3.3-70b-versatile's
regression where sequential tool calls fall back to XML/python-tag syntax.
"""

import json
import logging
from collections.abc import AsyncGenerator

import asyncpg
from groq import AsyncGroq

from app.config import settings
from app.services import query_tools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

_MODEL = "llama-3.3-70b-versatile"
_GUARDRAILS_MODEL = "llama-3.1-8b-instant"


_PLAN_SYSTEM_PROMPT = """
You are a query planner for a SAP Order-to-Cash database.
Analyze the user's question and output a JSON execution plan.

Output ONLY valid JSON. No prose, no markdown, no code fences.

You have access to conversation history. If the user's message is a bare ID or
short follow-up (e.g. a bare document ID, "that one", "the second document"), check the
previous messages to determine what they are referring to before building the plan.

═══════════════════════════════════════
AVAILABLE TOOLS
═══════════════════════════════════════

get_customer_by_name(name)
  Returns: businessPartner, businessPartnerFullName, customer
  "customer" is the soldToParty ID used in all transaction tables.
  Use when the question mentions a customer by name.

get_customer_by_id(customer_id)
  Returns: businessPartnerFullName, businessPartner, customer,
           businessPartnerIsBlocked, cityName, country, streetName
  Use when the user provides a numeric customer or business partner ID
  (e.g. "320000083", "tell me about customer 320000083").
  Do NOT use get_customer_by_name for numeric IDs — use this tool instead.

get_top_products_by_billing_count(limit?)
  Returns products ranked by number of billing documents.
  Use for "top products", "most billed", etc.

trace_document_flow(billing_document_id)
  Returns the full SO → Delivery → Billing → Journal chain.
  billing_document_id must be an 8-digit number starting with 90 or 91.
  NOT a 10-digit accounting document number (e.g. 9400635969).
  If given a 10-digit ID, first use execute_query to resolve it:
    SELECT DISTINCT "referenceDocument" FROM journal_entry_items
    WHERE "accountingDocument" = '<id>' LIMIT 1

  IMPORTANT: The billing_document_id MUST appear as an explicit number in the
  user's current message. Do NOT pull an ID from conversation history.
  If the user says "trace the flow" or "trace a billing document" with NO number
  in their message text, you MUST return:
  {"ask": "Please provide a billing document ID to trace. You can find IDs by clicking on any Billing Doc node in the graph."}

  Do NOT pick a default ID. Do NOT use example IDs from this prompt.
  Do NOT reuse IDs mentioned in earlier conversation turns.
  Only call trace_document_flow if the user's current message contains the ID.

get_broken_flows()
  Returns deliveries not billed + billings without delivery.
  Use for "gaps", "missing steps", "incomplete flows".

execute_query(sql)
  Runs a single PostgreSQL SELECT against the O2C database.
  Use when no fixed tool covers the question.

═══════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════

{
  "steps": [
    {"tool": "<name>", "params": {"<key>": "<value>"}},
    ...
  ]
}

For multi-step queries where a later step depends on an earlier result,
use {field_name} placeholders in params — Python replaces them with the
value of that field from the first row returned by the previous step.

EXAMPLE — "What products did Nelson buy?":
{
  "steps": [
    {"tool": "get_customer_by_name", "params": {"name": "Nelson"}},
    {"tool": "execute_query", "params": {"sql": "SELECT DISTINCT bdi.\"material\", COALESCE(pd.\"productDescription\", bdi.\"material\") AS \"productDescription\" FROM billing_document_headers bdh JOIN billing_document_items bdi ON bdi.\"billingDocument\" = bdh.\"billingDocument\" LEFT JOIN product_descriptions pd ON pd.\"product\" = bdi.\"material\" AND pd.\"language\" = 'EN' WHERE bdh.\"soldToParty\" = '{customer}' LIMIT 20"}}
  ]
}

Note: {customer} is substituted with the "customer" field from step 1's result row.

═══════════════════════════════════════
SQL RULES (for execute_query steps)
═══════════════════════════════════════

- All camelCase column names MUST be double-quoted: "salesOrder", "soldToParty"
- Headers never join directly — always through items tables:
    billing_document_items."referenceSdDocument" = outbound_delivery_headers."deliveryDocument"
    outbound_delivery_items."referenceSdDocument" = sales_order_headers."salesOrder"
    journal_entry_items."referenceDocument"       = billing_document_headers."billingDocument"
- Deduplicate journal entries: GROUP BY "accountingDocument", "companyCode", "fiscalYear"
- journal_entry_items has multiple rows per accountingDocument (debit + credit lines).
  Always count journal entries by DISTINCT "accountingDocument", never COUNT(*):
    WRONG: SELECT COUNT(*) FROM journal_entry_items          → returns 123 (line items)
    RIGHT: SELECT DISTINCT "accountingDocument" FROM journal_entry_items → returns 61 (entries)
- Always LIMIT 20 unless the question asks for more OR the question is a count
- Embed literal values directly in the SQL — no $1 parameters
- HIGHLIGHTING RULE: always include the primary ID column in every SELECT so
  graph nodes can be highlighted. Required ID columns per entity:
    customers       → "businessPartner"
    sales orders    → "salesOrder"
    billing docs    → "billingDocument"
    deliveries      → "deliveryDocument"
    journal entries → "accountingDocument"
    plants          → "plant"
    products        → "product" or "material"
  Include these even in aggregate/count queries — fetch the IDs alongside any counts.

- NEVER use COUNT(*) or any aggregate that collapses rows into a single summary
  row. It removes entity IDs and breaks graph highlighting entirely.
  Just return the individual rows — the summarizer counts them from the result.

  WRONG:  SELECT COUNT(*) FROM business_partners WHERE "businessPartnerIsBlocked" = true
  RIGHT:  SELECT "businessPartner", "businessPartnerFullName", "businessPartnerIsBlocked"
          FROM business_partners WHERE "businessPartnerIsBlocked" = true LIMIT 100

  Use LIMIT 100 (not 20) for counting questions so the full set is returned.

KEY TABLES AND COLUMNS:
  sales_order_headers:       "salesOrder" PK, "soldToParty", "creationDate", "totalNetAmount"
  sales_order_items:         "salesOrder", "material", "requestedQuantity", "netAmount"
  outbound_delivery_headers: "deliveryDocument" PK, "actualGoodsMovementDate",
                             "shippingPoint", "creationDate",
                             "overallGoodsMovementStatus" (A=not processed, B=partial, C=complete)
  outbound_delivery_items:   "deliveryDocument", "referenceSdDocument" (= salesOrder), "plant"
  plants:                    "plant" PK, "plantName", "salesOrganization"
                             Join: plants."plant" = outbound_delivery_items."plant"
                             NOT "deliveringPlant", NOT "plant_id" — the column is just "plant"
                             To rank plants by deliveries:
                               SELECT odi."plant", p."plantName",
                                      COUNT(DISTINCT odi."deliveryDocument") AS delivery_count
                               FROM outbound_delivery_items odi
                               JOIN plants p ON p."plant" = odi."plant"
                               GROUP BY odi."plant", p."plantName"
                               ORDER BY delivery_count DESC
  billing_document_headers:  "billingDocument" PK, "soldToParty", "billingDocumentDate", "totalNetAmount"
  billing_document_items:    "billingDocument", "material", "billingQuantity", "referenceSdDocument" (= deliveryDocument)
  journal_entry_items:       "accountingDocument", "referenceDocument" (= billingDocument), "postingDate"
  payments_accounts_receivable: "accountingDocument", "customer", "clearingDate", "amountInTransactionCurrency"
  business_partners:         "businessPartner" PK, "customer", "businessPartnerFullName"
  product_descriptions:      "product", "language", "productDescription" — always filter language = 'EN'

CANCELLED BILLING DOCUMENTS:
  Use: WHERE "billingDocumentIsCancelled" = true
  NOT "billingDocumentStatus" — that column does not exist.

DELIVERY SHIPPING STATUS:
  Table: outbound_delivery_headers
  Column: "actualGoodsMovementDate" — MUST be double-quoted (camelCase)
  Not shipped: WHERE "actualGoodsMovementDate" IS NULL
  Shipped:     WHERE "actualGoodsMovementDate" IS NOT NULL
  Also useful: "overallGoodsMovementStatus" (A=not processed, B=partial, C=complete)
  Primary key: "deliveryDocument"

  Example query for unshipped deliveries:
    SELECT "deliveryDocument", "creationDate",
           "shippingPoint", "overallGoodsMovementStatus"
    FROM outbound_delivery_headers
    WHERE "actualGoodsMovementDate" IS NULL
    LIMIT 20

CUSTOMER BLOCKING STATUS:
  Table: business_partners
  Column: "businessPartnerIsBlocked" (boolean) — MUST be double-quoted
  Blocked: WHERE "businessPartnerIsBlocked" = true
  Active:  WHERE "businessPartnerIsBlocked" = false
  Primary key: "businessPartner"

  NEVER use GROUP BY on business_partners for simple blocked/active counts —
  just filter with WHERE and return individual rows; the summarizer counts them.

  Example — blocked customers:
    SELECT "businessPartner", "businessPartnerFullName", "businessPartnerIsBlocked"
    FROM business_partners
    WHERE "businessPartnerIsBlocked" = true
    LIMIT 100

  Example — active customers:
    SELECT "businessPartner", "businessPartnerFullName", "businessPartnerIsBlocked"
    FROM business_partners
    WHERE "businessPartnerIsBlocked" = false
    LIMIT 100
""".strip()


SYSTEM_PROMPT = """
You are a data analyst assistant for a SAP Order-to-Cash (O2C) data explorer.
Query results are provided directly in the user message.
Summarize what the data shows — do not invent data not present in the results.
If the results are empty, say so clearly and suggest a likely reason.
Do not describe the SQL that was run — describe what the data shows.

You have access to conversation history. If a user provides just a number or
short follow-up, use the previous messages to understand what they are
referring to before answering.

If the query results contain an "ask" key, respond with that message verbatim
and nothing else. Example: {"ask": "Please provide a billing document ID..."}
→ respond: "Please provide a billing document ID..."

If the user needs guidance on billing document IDs, tell them:
"You can find billing document IDs by clicking on any Billing Doc node in the
graph. F2 invoices are standard invoices; S1 documents are cancellations."
Do not suggest specific ID numbers.

CRITICAL FORMATTING RULE:
Every numbered or bulleted list item MUST be on its own line, ending with a
newline character. Never run list items together on the same line.

Always use this exact structure — one item per line, blank line before the list:

The top products by billing document count are:

1. **SUNSCREEN GEL SPF50** (S8907367039280) — 22 billing documents
2. **FACESERUM 30ML VIT C** (S8907367008620) — 22 billing documents
3. **Destiny 100ml EDP** (S8907367042006) — 16 billing documents

WRONG — never concatenate items on one line:
1. **SUNSCREEN GEL SPF50** (S8907367039280) — 22 docs2. **FACESERUM** — 22 docs

Each item format:
  N. **Product Name** (PRODUCT_ID) — detail\n
  N. **Document ID** — detail\n

Additional formatting rules:
- If there is only one result, do not use a numbered list. Write it as a
  plain sentence: "The customer is **Name** (ID: 123) with 72 sales orders."
- If a customer result has businessPartnerIsBlocked = true, add a warning on
  a new line after the main answer: ⚠️ Note: This customer is currently blocked.
- Use numbered lists (1. 2. 3.) for ranked or sequential results only when
  there are two or more items.
- Use unordered lists (- item) for non-ranked items.
- Use **text** for product names, document IDs, amounts, and key figures.
- Use a blank line between the intro sentence and the list.
- Use a blank line between sections.
- Always write the COMPLETE document ID on the same line as the bold markers:
  CORRECT:  1. **80738054** — not yet shipped
  WRONG:    1. **8
            0738054** — not yet shipped
""".strip()


# Order matters: first match wins. More-specific table names listed first.
_FALLBACK_TABLE_QUERIES: list[tuple[str, str, str, str]] = [
    (
        "journal_entry_items",
        'SELECT DISTINCT "accountingDocument" FROM journal_entry_items',
        "accountingDocument",
        "journal_entry",
    ),
    (
        "payments_accounts_receivable",
        'SELECT DISTINCT "accountingDocument" FROM payments_accounts_receivable',
        "accountingDocument",
        "payment",
    ),
    (
        "billing_document_headers",
        'SELECT "billingDocument" FROM billing_document_headers',
        "billingDocument",
        "billing_doc",
    ),
    (
        "billing_document_items",
        'SELECT DISTINCT "billingDocument" FROM billing_document_items',
        "billingDocument",
        "billing_doc",
    ),
    (
        "outbound_delivery_headers",
        'SELECT "deliveryDocument" FROM outbound_delivery_headers',
        "deliveryDocument",
        "delivery",
    ),
    (
        "outbound_delivery_items",
        'SELECT DISTINCT "deliveryDocument" FROM outbound_delivery_items',
        "deliveryDocument",
        "delivery",
    ),
    (
        "sales_order_headers",
        'SELECT "salesOrder" FROM sales_order_headers',
        "salesOrder",
        "sales_order",
    ),
    (
        "sales_order_items",
        'SELECT DISTINCT "salesOrder" FROM sales_order_items',
        "salesOrder",
        "sales_order",
    ),
    (
        "business_partners",
        'SELECT "customer" FROM business_partners WHERE "customer" IS NOT NULL',
        "customer",
        "customer",
    ),
    (
        "products",
        'SELECT "product" FROM products WHERE "isMarkedForDeletion" = FALSE',
        "product",
        "product",
    ),
    (
        "plants",
        'SELECT "plant" FROM plants',
        "plant",
        "plant",
    ),
]


async def _fetch_fallback_node_ids(
    steps: list[dict],
    pool: asyncpg.Pool,
) -> list[str]:
    """
    Called when _execute_plan returns no node IDs (e.g. COUNT queries).

    Scans each execute_query step's SQL for known table names, then runs
    a simple SELECT to fetch all node IDs for that entity type.
    One table match per step — the first match in _FALLBACK_TABLE_QUERIES wins.
    """
    all_ids: list[str] = []

    for step in steps:
        if step.get("tool") != "execute_query":
            continue
        sql_lower = step.get("params", {}).get("sql", "").lower()
        print(f"DEBUG FALLBACK: scanning SQL = {sql_lower[:120]}", flush=True)

        for table_name, fetch_sql, id_col, node_type in _FALLBACK_TABLE_QUERIES:
            if table_name in sql_lower:
                print(f"DEBUG FALLBACK: matched table={table_name}, running {fetch_sql}", flush=True)
                try:
                    async with pool.acquire() as conn:
                        rows = await conn.fetch(fetch_sql, timeout=5.0)
                    for r in rows:
                        val = r[id_col]
                        if val is not None:
                            all_ids.append(f"{node_type}::{val}")
                    print(f"DEBUG FALLBACK: got {len(all_ids)} ids for {node_type}", flush=True)
                except Exception as exc:
                    logger.warning("Fallback highlight failed for %s: %s", table_name, exc)
                break  # one match per step

    return list(set(all_ids))


async def _build_plan(
    message: str,
    history: list,
) -> tuple[list[dict], str | None]:
    """
    Non-streaming call that returns (steps, ask_message).

    Normal case:  ([ {"tool": ..., "params": ...}, ... ], None)
    Clarification needed: ([], "<message to show the user>")
      — emitted when the planner returns {"ask": "..."} instead of {"steps": [...]}

    history is prepended so the planner can resolve follow-up messages like
    "90504208" (bare ID) by looking at the previous question for context.
    """
    response = await _client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
            *[{"role": h.role, "content": h.content} for h in history],
            {"role": "user",   "content": message},
        ],
        temperature=0,
        max_tokens=1024,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content
    print(f"DEBUG RAW PLAN RESPONSE: {raw}", flush=True)
    plan = json.loads(raw)
    ask = plan.get("ask")
    if ask:
        return [], str(ask)
    return plan.get("steps", []), None


async def _execute_plan(
    steps: list[dict],
    pool: asyncpg.Pool,
) -> tuple[list[dict], list[str]]:
    """
    Execute each step in order.

    After each step, the first result row is merged into a substitution
    context so that later steps can reference those values as {field_name}
    placeholders in their params (e.g. '{customer}' → '320000083').

    Returns (all_results, all_node_ids).
    all_results is a list of {"tool": ..., "rows": [...]} dicts passed to
    the final streaming call for summarization.
    """
    context: dict[str, str] = {}
    all_results: list[dict] = []
    all_node_ids: list[str] = []

    for step in steps:
        tool_name = step["tool"]
        raw_params = step.get("params", {})

        params: dict = {}
        for k, v in raw_params.items():
            if isinstance(v, str):
                try:
                    params[k] = v.format_map(context)
                except KeyError as exc:
                    raise ValueError(
                        f"Step '{tool_name}' references {{{exc.args[0]}}} but "
                        f"that field was not returned by the previous step."
                    ) from exc
            else:
                params[k] = v

        result = await query_tools.run_tool(tool_name, params, pool)
        all_node_ids.extend(result.node_ids)
        all_results.append({"tool": tool_name, "rows": result.rows})

        # All values are stringified so format_map works uniformly.
        if result.rows:
            context.update(
                {k: str(v) for k, v in result.rows[0].items() if v is not None}
            )

    return all_results, all_node_ids


async def stream_chat(
    message: str,
    history: list,
    pool: asyncpg.Pool,
) -> AsyncGenerator[dict, None]:
    """
    Async generator that yields SSE-ready event dicts:

      {"event": "token",     "data": "<text chunk>"}
      {"event": "highlight", "data": ["node_id", ...]}   (once, at end)
      {"event": "error",     "data": "<message>"}        (on failure)

    Three-phase flow:
      Round 0 — planning: LLM returns JSON step list, no tools, no streaming
      Execute  — Python runs each step, resolving {field} substitutions
      Round 1  — streaming: LLM summarizes injected results, NO tools parameter
    """
    all_node_ids: list[str] = []
    all_results: list[dict] = []

    steps: list[dict] = []
    ask_message: str | None = None
    try:
        steps, ask_message = await _build_plan(message, history)
        logger.info("Plan result: steps=%s ask=%s", steps, ask_message)
        print(f"DEBUG PLAN: steps={steps} ask={ask_message}", flush=True)
    except Exception as exc:
        logger.error("Planning failed: %s", exc)
        # steps and ask_message stay at their defaults above

    # The planner sometimes returns the ask as a raw JSON string like
    # '{"ask": "Please provide..."}' — unwrap it to plain text.
    if ask_message:
        try:
            parsed = json.loads(ask_message)
            if isinstance(parsed, dict) and "ask" in parsed:
                ask_message = parsed["ask"]
        except (json.JSONDecodeError, TypeError):
            pass
        yield {"event": "token", "data": ask_message}
        return

    # The planner sometimes invents a billing document ID rather than
    # returning {"ask": ...}. Verify any trace step ID actually exists
    # in the database before executing — catches hallucinated IDs.
    for step in steps:
        if step.get("tool") == "trace_document_flow":
            doc_id = step.get("params", {}).get("billing_document_id", "")
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    'SELECT "billingDocument" FROM billing_document_headers'
                    ' WHERE "billingDocument" = $1',
                    doc_id,
                )
            if not row:
                yield {
                    "event": "token",
                    "data": (
                        "I could not find that billing document. "
                        "Please provide a valid billing document ID. "
                        "You can find IDs by clicking any Billing Doc node in the graph."
                    ),
                }
                return

    if steps:
        try:
            all_results, node_ids = await _execute_plan(steps, pool)
            all_node_ids.extend(node_ids)
        except Exception as exc:
            logger.error("Plan execution failed: %s", exc)
            all_results = [{"error": str(exc)}]

        # Fallback: if the query returned no IDs (e.g. COUNT/aggregate),
        # detect the primary table and fetch all IDs for that entity type.
        if not all_node_ids:
            fallback_ids = await _fetch_fallback_node_ids(steps, pool)
            all_node_ids.extend(fallback_ids)

    # Results are injected directly into the user message.
    # The model summarizes them without being able to call any tools,
    # so function-call syntax cannot appear in the streamed output.
    if all_results:
        results_json = json.dumps(all_results, default=str)
        user_content = f"{message}\n\n[Database query results]\n{results_json}"
    else:
        user_content = (
            f"{message}\n\n"
            "[No database results were retrieved. "
            "If you cannot answer without data, say so clearly.]"
        )

    try:
        stream = await _client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *[{"role": h.role, "content": h.content} for h in history],
                {"role": "user",   "content": user_content},
            ],
            temperature=0.1,
            max_tokens=1024,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield {"event": "token", "data": delta.content}

    except Exception as exc:
        logger.error("Streaming failed: %s", exc)
        yield {"event": "error", "data": str(exc)}
        return

    print(f"DEBUG: all_node_ids = {all_node_ids}", flush=True)
    if all_node_ids:
        yield {"event": "highlight", "data": list(set(all_node_ids))}
