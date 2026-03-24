"""
Query tools — fixed implementations + dynamic SQL fallback.

Fixed tools use verified SQL patterns tested against real Neon data.
Dynamic tool validates SQL via sqlglot AST before execution.

Each tool returns a ToolResult with:
  rows      — serialised data sent back to the LLM as the tool response
  node_ids  — graph node IDs to highlight, extracted server-side
              (never sent to the LLM; consumed by the chat router)
"""

import datetime
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal

import asyncpg
import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Tool result type
# ─────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    rows: list[dict]
    node_ids: list[str] = field(default_factory=list)

    def to_tool_message(self) -> str:
        return json.dumps(self.rows, default=str)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_top_products_by_billing_count",
            "description": (
                "Returns the products associated with the most billing documents. "
                "Use for questions like 'which products appear most in billing docs', "
                "'top products by invoice count', or 'most billed materials'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of products to return (default 10, max 50).",
                        "default": 10,
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_document_flow",
            "description": (
                "Traces the full O2C flow for a billing document: "
                "Sales Order → Delivery → Billing Document → Journal Entry. "
                "Use when the user asks to trace, show, or explain the flow of a "
                "specific billing document."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "billing_document_id": {
                        "type": "string",
                        "description": "The billing document number to trace (e.g. '9000000001').",
                    }
                },
                "required": ["billing_document_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_broken_flows",
            "description": (
                "Identifies incomplete or broken O2C flows: deliveries that were "
                "shipped but never billed, and billing documents with no matching "
                "delivery record. Use for questions about gaps, missing steps, "
                "incomplete flows, or unbilled deliveries."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_by_name",
            "description": (
                "Looks up a customer by partial name in the business_partners table. "
                "ALWAYS call this first when the user mentions a customer by name. "
                "Returns businessPartner, businessPartnerFullName, and customer (the ID "
                "used in sales_order_headers.soldToParty and billing_document_headers.soldToParty)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Partial customer name to search for (case-insensitive).",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_query",
            "description": (
                "Executes a custom SQL SELECT against the SAP O2C database. "
                "Use this ONLY when none of the three fixed tools can answer the question. "
                "The query must be a single SELECT statement. "
                "All camelCase column names must be double-quoted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A single valid PostgreSQL SELECT statement.",
                    }
                },
                "required": ["sql"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────
# SQL validation (dynamic path only)
# ─────────────────────────────────────────────────────────────



ALLOWED_TABLES = {
    "sales_order_headers",
    "sales_order_items",
    "sales_order_schedule_lines",
    "outbound_delivery_headers",
    "outbound_delivery_items",
    "billing_document_headers",
    "billing_document_items",
    "journal_entry_items",
    "payments_accounts_receivable",
    "business_partners",
    "business_partner_addresses",
    "customer_company_assignments",
    "customer_sales_area_assignments",
    "products",
    "product_descriptions",
    "product_plants",
    "plants",
    "product_storage_locations",
}


def validate_sql(sql: str) -> tuple[bool, str]:
    """
    Validate LLM-generated SQL before execution.
    Returns (is_valid, error_reason).

    Checks (in order):
      1. Parseable PostgreSQL syntax
      2. Single statement only
      3. Must be a SELECT (CTEs included)
      4. All table references within ALLOWED_TABLES
         — find_all(exp.Table) is recursive: catches subqueries and CTEs
    """
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.ParseError as exc:
        return False, f"SQL syntax error: {exc}"

    if not statements:
        return False, "Empty query"
    if len(statements) > 1:
        return False, "Only a single statement is allowed"

    stmt = statements[0]

    if not isinstance(stmt, exp.Select):
        return False, f"Only SELECT is permitted — got {type(stmt).__name__}"

    referenced = {table.name.lower() for table in stmt.find_all(exp.Table)}
    unknown = referenced - ALLOWED_TABLES
    if unknown:
        return False, f"References tables not in the allowed set: {unknown}"

    return True, ""


def _serialise_row(record) -> dict:
    result = {}
    for key, value in dict(record).items():
        if isinstance(value, (datetime.date, datetime.datetime)):
            result[key] = value.isoformat()
        elif isinstance(value, Decimal):
            result[key] = float(value)
        else:
            result[key] = value
    return result


# Keys must be lowercase — the lookup uses col.lower().
_COLUMN_NODE_TYPE: dict[str, str] = {
    "salesorder":          "sales_order",
    "deliverydocument":    "delivery",
    "billingdocument":     "billing_doc",
    "accountingdocument":  "journal_entry",
    "material":            "product",
    "product":             "product",
    # Three SAP column names all represent the same entity
    "customer":            "customer",
    "businesspartner":     "customer",
    "soldtoparty":         "customer",
}


def _extract_node_ids(rows: list[dict]) -> list[str]:
    """
    Extract graph node IDs from every row in a result set.

    Checks every column in every row against _COLUMN_NODE_TYPE.
    Values may arrive as str, int, or float (asyncpg returns numeric
    PK columns as int), so we stringify non-None values before building
    the node ID.  The result set is deduplicated via a set.
    """
    seen: set[str] = set()
    for row in rows:
        for col, val in row.items():
            if val is None:
                continue
            node_type = _COLUMN_NODE_TYPE.get(col.lower())
            if node_type:
                seen.add(f"{node_type}::{val}")
    return list(seen)


# Order matters: more-specific tables first so the first match wins.
_TABLE_ID_MAP: list[tuple[str, str, str]] = [
    ("journal_entry_items",          "accountingDocument", "journal_entry"),
    ("payments_accounts_receivable", "accountingDocument", "payment"),
    ("sales_order_headers",          "salesOrder",         "sales_order"),
    ("sales_order_items",            "salesOrder",         "sales_order"),
    ("sales_order_schedule_lines",   "salesOrder",         "sales_order"),
    ("outbound_delivery_headers",    "deliveryDocument",   "delivery"),
    ("outbound_delivery_items",      "deliveryDocument",   "delivery"),
    ("billing_document_headers",     "billingDocument",    "billing_doc"),
    ("billing_document_items",       "billingDocument",    "billing_doc"),
    ("business_partners",            "customer",           "customer"),
    ("business_partner_addresses",   "businessPartner",    "customer"),
    ("customer_company_assignments", "customer",           "customer"),
    ("customer_sales_area_assignments", "customer",        "customer"),
    ("plants",                       "plant",              "plant"),
    ("product_plants",               "product",            "product"),
    ("product_descriptions",         "product",            "product"),
    ("products",                     "product",            "product"),
]


async def get_highlight_ids(
    sql: str,
    rows: list[dict],
    pool: asyncpg.Pool,
) -> list[str]:
    """
    Guarantee node IDs for graph highlighting after any query.

    Fast path — rows already contain a recognised ID column:
      Return immediately without touching the DB.

    Slow path — aggregate / count query returns no ID columns:
      1. Parse the SQL with sqlglot.
      2. Find the first table reference that matches _TABLE_ID_MAP.
      3. Re-run a lightweight SELECT DISTINCT "{id_col}" … preserving the
         original FROM, JOINs, and WHERE clauses so the filter still applies.
      4. Return the resulting node IDs.
    """
    ids = _extract_node_ids(rows)
    if ids:
        return ids

    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except Exception:
        return []

    if not statements:
        return []

    stmt = statements[0]

    referenced_tables = [t.name.lower() for t in stmt.find_all(exp.Table)]

    matched: tuple[str, str, str] | None = None
    for table_name, id_col, node_type in _TABLE_ID_MAP:
        if table_name in referenced_tables:
            matched = (table_name, id_col, node_type)
            break

    if matched is None:
        return []

    _, id_col, node_type = matched

    from_clause = stmt.args.get("from")
    joins       = stmt.args.get("joins") or []
    where       = stmt.args.get("where")

    if from_clause is None:
        return []

    from_sql  = from_clause.sql(dialect="postgres")
    joins_sql = " ".join(j.sql(dialect="postgres") for j in joins)
    where_sql = where.sql(dialect="postgres") if where else ""

    secondary = (
        f'SELECT DISTINCT "{id_col}" '
        f"{from_sql} "
        f"{joins_sql} "
        f"{where_sql} "
        f"LIMIT 500"
    ).strip()

    try:
        async with pool.acquire() as conn:
            sec_rows = await conn.fetch(secondary, timeout=5.0)
        return [
            f"{node_type}::{r[id_col]}"
            for r in sec_rows
            if r[id_col] is not None
        ]
    except Exception as exc:
        logger.warning("get_highlight_ids secondary query failed: %s — %s", secondary, exc)
        return []


async def _get_top_products(
    conn: asyncpg.Connection,
    limit: int = 10,
) -> ToolResult:
    limit = min(int(limit), 50)  # cap at 50 regardless of what the LLM passes
    rows = await conn.fetch(
        """
        SELECT
            bdi."material",
            COALESCE(pd."productDescription", bdi."material") AS "productDescription",
            COUNT(DISTINCT bdi."billingDocument")              AS "billingDocCount"
        FROM billing_document_items bdi
        LEFT JOIN product_descriptions pd
               ON pd."product"  = bdi."material"
              AND pd."language" = 'EN'
        WHERE bdi."material" IS NOT NULL
        GROUP BY bdi."material", pd."productDescription"
        ORDER BY "billingDocCount" DESC
        LIMIT $1
        """,
        limit,
    )
    serialised = [_serialise_row(r) for r in rows]
    node_ids = [f"product::{r['material']}" for r in serialised if r.get("material")]
    return ToolResult(rows=serialised, node_ids=node_ids)


async def _trace_document_flow(
    conn: asyncpg.Connection,
    billing_document_id: str,
) -> ToolResult:
    rows = await conn.fetch(
        """
        SELECT
            soh."salesOrder",
            soh."soldToParty",
            soh."creationDate"               AS "soCreationDate",
            soh."overallDeliveryStatus",
            soh."overallOrdReltdBillgStatus",

            odh."deliveryDocument",
            odh."actualGoodsMovementDate",
            odh."overallGoodsMovementStatus",

            bdh."billingDocument",
            bdh."billingDocumentType",
            bdh."billingDocumentDate",
            bdh."totalNetAmount",
            bdh."transactionCurrency",
            bdh."billingDocumentIsCancelled",

            je."accountingDocument",
            je."postingDate"                 AS "journalPostingDate",
            je."companyCode",
            je."fiscalYear"

        FROM billing_document_headers bdh

        JOIN (
            SELECT DISTINCT "billingDocument",
                            "referenceSdDocument" AS "deliveryDocument"
            FROM billing_document_items
        ) bdi ON bdi."billingDocument" = bdh."billingDocument"

        JOIN outbound_delivery_headers odh
          ON odh."deliveryDocument" = bdi."deliveryDocument"

        JOIN (
            SELECT DISTINCT "deliveryDocument",
                            "referenceSdDocument" AS "salesOrder"
            FROM outbound_delivery_items
        ) odi ON odi."deliveryDocument" = odh."deliveryDocument"

        JOIN sales_order_headers soh
          ON soh."salesOrder" = odi."salesOrder"

        LEFT JOIN (
            SELECT "referenceDocument",
                   "accountingDocument",
                   MIN("postingDate") AS "postingDate",
                   "companyCode",
                   "fiscalYear"
            FROM journal_entry_items
            GROUP BY "referenceDocument", "accountingDocument",
                     "companyCode", "fiscalYear"
        ) je ON je."referenceDocument" = bdh."billingDocument"

        WHERE bdh."billingDocument" = $1
        """,
        billing_document_id,
    )
    serialised = [_serialise_row(r) for r in rows]

    node_ids: list[str] = []
    for r in serialised:
        if r.get("salesOrder"):
            node_ids.append(f"sales_order::{r['salesOrder']}")
        if r.get("deliveryDocument"):
            node_ids.append(f"delivery::{r['deliveryDocument']}")
        if r.get("billingDocument"):
            node_ids.append(f"billing_doc::{r['billingDocument']}")
        if r.get("accountingDocument"):
            node_ids.append(f"journal_entry::{r['accountingDocument']}")

    return ToolResult(rows=serialised, node_ids=list(set(node_ids)))


async def _get_broken_flows(conn: asyncpg.Connection) -> ToolResult:
    rows = await conn.fetch(
        """
        SELECT
            'delivered_not_billed'            AS "breakType",
            odh."deliveryDocument"            AS "documentId",
            odh."actualGoodsMovementDate"     AS "eventDate",
            odh."overallGoodsMovementStatus"  AS "status",
            NULL::text                        AS "billingDocumentType"
        FROM outbound_delivery_headers odh
        WHERE odh."actualGoodsMovementDate" IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM billing_document_items bdi
              WHERE bdi."referenceSdDocument" = odh."deliveryDocument"
          )

        UNION ALL

        SELECT
            'billed_without_delivery'         AS "breakType",
            bdh."billingDocument"             AS "documentId",
            bdh."billingDocumentDate"         AS "eventDate",
            NULL::text                        AS "status",
            bdh."billingDocumentType"         AS "billingDocumentType"
        FROM billing_document_headers bdh
        WHERE bdh."billingDocumentType" = 'F2'
          AND NOT EXISTS (
              SELECT 1
              FROM billing_document_items bdi
              JOIN outbound_delivery_headers odh
                ON odh."deliveryDocument" = bdi."referenceSdDocument"
              WHERE bdi."billingDocument" = bdh."billingDocument"
          )

        ORDER BY "eventDate" DESC NULLS LAST
        """
    )
    serialised = [_serialise_row(r) for r in rows]

    node_ids: list[str] = []
    for r in serialised:
        doc_id = r.get("documentId")
        if doc_id:
            if r["breakType"] == "delivered_not_billed":
                node_ids.append(f"delivery::{doc_id}")
            else:
                node_ids.append(f"billing_doc::{doc_id}")

    return ToolResult(rows=serialised, node_ids=node_ids)


async def _get_customer_by_name(
    conn: asyncpg.Connection,
    name: str,
) -> ToolResult:
    rows = await conn.fetch(
        """
        SELECT "businessPartner", "businessPartnerFullName", "customer"
        FROM business_partners
        WHERE LOWER("businessPartnerFullName") LIKE LOWER('%' || $1 || '%')
        """,
        name,
    )
    serialised = [_serialise_row(r) for r in rows]
    node_ids = [f"customer::{r['customer']}" for r in serialised if r.get("customer")]
    return ToolResult(rows=serialised, node_ids=node_ids)


async def _get_customer_by_id(
    conn: asyncpg.Connection,
    customer_id: str,
) -> ToolResult:
    rows = await conn.fetch(
        """
        SELECT
            bp."businessPartnerFullName",
            bp."businessPartner",
            bp."customer",
            bp."businessPartnerIsBlocked",
            bpa."cityName",
            bpa."country",
            bpa."streetName"
        FROM business_partners bp
        LEFT JOIN business_partner_addresses bpa
               ON bpa."businessPartner" = bp."businessPartner"
        WHERE bp."businessPartner" = $1
           OR bp."customer" = $1
        """,
        customer_id,
    )
    serialised = [_serialise_row(r) for r in rows]
    node_ids = [f"customer::{r['customer']}" for r in serialised if r.get("customer")]
    return ToolResult(rows=serialised, node_ids=node_ids)


async def _execute_query(pool: asyncpg.Pool, sql: str) -> ToolResult:
    valid, reason = validate_sql(sql)
    if not valid:
        raise ValueError(f"SQL validation failed: {reason}")

    # Fresh connection + client-side timeout — no session-level settings bleed into the pool.
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, timeout=5.0)

    serialised = [_serialise_row(r) for r in rows]
    return ToolResult(rows=serialised, node_ids=_extract_node_ids(serialised))


async def run_tool(
    name: str,
    args: dict,
    pool: asyncpg.Pool,
) -> ToolResult:
    if name == "get_customer_by_name":
        async with pool.acquire() as conn:
            return await _get_customer_by_name(conn, **args)

    if name == "get_customer_by_id":
        async with pool.acquire() as conn:
            return await _get_customer_by_id(conn, **args)

    if name == "get_top_products_by_billing_count":
        async with pool.acquire() as conn:
            return await _get_top_products(conn, **args)

    if name in ("trace_document_flow", "trace_billing_document_flow"):
        async with pool.acquire() as conn:
            return await _trace_document_flow(conn, **args)

    if name == "get_broken_flows":
        async with pool.acquire() as conn:
            return await _get_broken_flows(conn)

    if name == "execute_query":
        return await _execute_query(pool, args["sql"])

    raise ValueError(f"Unknown tool: {name!r}")
