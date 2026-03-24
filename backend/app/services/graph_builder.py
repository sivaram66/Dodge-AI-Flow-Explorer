from decimal import Decimal

import asyncpg

graph: dict | None = None

_TYPE_LABELS: dict[str, str] = {
    "sales_order":   "Sales Orders",
    "delivery":      "Deliveries",
    "billing_doc":   "Billing Docs",
    "journal_entry": "Journal Entries",
    "payment":       "Payments",
    "customer":      "Customers",
    "product":       "Products",
    "plant":         "Plants",
}


def _nid(node_type: str, raw_id: str) -> str:
    """Canonical node ID: 'sales_order::1000000001'"""
    return f"{node_type}::{raw_id}"


def _date(value) -> str | None:
    return str(value) if value is not None else None


def _amount(value) -> float | None:
    """asyncpg returns Decimal for NUMERIC columns; convert for JSON."""
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal) else float(value)


async def _fetch_sales_orders(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("""
        SELECT "salesOrder", "salesOrderType", "soldToParty",
               "overallDeliveryStatus", "overallOrdReltdBillgStatus",
               "creationDate", "totalNetAmount", "transactionCurrency",
               "headerBillingBlockReason", "deliveryBlockReason"
        FROM sales_order_headers
    """)
    return [
        {
            "id":             _nid("sales_order", r["salesOrder"]),
            "type":           "sales_order",
            "label":          r["salesOrder"],
            "orderType":      r["salesOrderType"],
            "soldToParty":    r["soldToParty"],
            "deliveryStatus": r["overallDeliveryStatus"],
            "billingStatus":  r["overallOrdReltdBillgStatus"],
            "creationDate":   _date(r["creationDate"]),
            "netAmount":      _amount(r["totalNetAmount"]),
            "currency":       r["transactionCurrency"],
            "billingBlock":   r["headerBillingBlockReason"],
            "deliveryBlock":  r["deliveryBlockReason"],
        }
        for r in rows
    ]


async def _fetch_deliveries(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("""
        SELECT "deliveryDocument", "shippingPoint", "creationDate",
               "actualGoodsMovementDate", "overallGoodsMovementStatus",
               "overallPickingStatus", "headerBillingBlockReason"
        FROM outbound_delivery_headers
    """)
    return [
        {
            "id":               _nid("delivery", r["deliveryDocument"]),
            "type":             "delivery",
            "label":            r["deliveryDocument"],
            "shippingPoint":    r["shippingPoint"],
            "creationDate":     _date(r["creationDate"]),
            "goodsMovementDate": _date(r["actualGoodsMovementDate"]),
            "movementStatus":   r["overallGoodsMovementStatus"],
            "pickingStatus":    r["overallPickingStatus"],
            "billingBlock":     r["headerBillingBlockReason"],
        }
        for r in rows
    ]


async def _fetch_billing_docs(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("""
        SELECT "billingDocument", "billingDocumentType", "soldToParty",
               "billingDocumentDate", "totalNetAmount", "transactionCurrency",
               "billingDocumentIsCancelled", "cancelledBillingDocument"
        FROM billing_document_headers
    """)
    return [
        {
            "id":            _nid("billing_doc", r["billingDocument"]),
            "type":          "billing_doc",
            "label":         r["billingDocument"],
            "docType":       r["billingDocumentType"],   # F2=invoice S1=cancel G2=credit
            "soldToParty":   r["soldToParty"],
            "billingDate":   _date(r["billingDocumentDate"]),
            "netAmount":     _amount(r["totalNetAmount"]),
            "currency":      r["transactionCurrency"],
            "isCancelled":   r["billingDocumentIsCancelled"],
            "cancelledBy":   r["cancelledBillingDocument"],
        }
        for r in rows
    ]


async def _fetch_journal_entries(conn: asyncpg.Connection) -> list[dict]:
    # Deduplicate: multiple rows per accountingDocument (debit + credit sides).
    # MIN(postingDate) is safe — all line items share the same posting date.
    # MAX(referenceDocument) gives the billing doc this entry was posted from;
    # it's consistent across line items for a given accounting document.
    rows = await conn.fetch("""
        SELECT
            "accountingDocument",
            "companyCode",
            "fiscalYear",
            MIN("postingDate")       AS "postingDate",
            MAX("referenceDocument") AS "referenceDocument"
        FROM journal_entry_items
        GROUP BY "accountingDocument", "companyCode", "fiscalYear"
    """)
    return [
        {
            "id":              _nid("journal_entry", r["accountingDocument"]),
            "type":            "journal_entry",
            "label":           r["accountingDocument"],
            "companyCode":     r["companyCode"],
            "fiscalYear":      r["fiscalYear"],
            "postingDate":     _date(r["postingDate"]),
            "referenceDocument": r["referenceDocument"],
        }
        for r in rows
    ]


async def _fetch_payments(conn: asyncpg.Connection) -> list[dict]:
    # One node per payment accountingDocument.
    # DISTINCT ON picks a single representative row (lowest accountingDocumentItem).
    rows = await conn.fetch("""
        SELECT DISTINCT ON ("accountingDocument")
            "accountingDocument", "customer", "postingDate", "clearingDate",
            "amountInTransactionCurrency", "clearingAccountingDocument"
        FROM payments_accounts_receivable
        ORDER BY "accountingDocument", "accountingDocumentItem"
    """)
    return [
        {
            "id":            _nid("payment", r["accountingDocument"]),
            "type":          "payment",
            "label":         r["accountingDocument"],
            "customer":      r["customer"],
            "postingDate":   _date(r["postingDate"]),
            "clearingDate":  _date(r["clearingDate"]),
            "amount":        _amount(r["amountInTransactionCurrency"]),
            "clearsJournal": r["clearingAccountingDocument"],
        }
        for r in rows
    ]


async def _fetch_customers(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("""
        SELECT "businessPartner", "customer",
               "businessPartnerFullName", "businessPartnerName",
               "businessPartnerIsBlocked"
        FROM business_partners
        WHERE "customer" IS NOT NULL AND "customer" <> ''
    """)
    return [
        {
            "id":        _nid("customer", r["customer"]),
            "type":      "customer",
            "label":     r["businessPartnerFullName"] or r["businessPartnerName"] or r["customer"],
            "customer":  r["customer"],
            "partner":   r["businessPartner"],
            "isBlocked": r["businessPartnerIsBlocked"],
        }
        for r in rows
    ]


async def _fetch_products(conn: asyncpg.Connection) -> list[dict]:
    # LEFT JOIN for English description; fall back to product ID if absent.
    rows = await conn.fetch("""
        SELECT p."product", p."productType", p."productGroup",
               pd."productDescription"
        FROM products p
        LEFT JOIN product_descriptions pd
               ON pd."product" = p."product"
              AND pd."language" = 'EN'
        WHERE p."isMarkedForDeletion" = FALSE
    """)
    return [
        {
            "id":           _nid("product", r["product"]),
            "type":         "product",
            "label":        r["productDescription"] or r["product"],
            "product":      r["product"],
            "productType":  r["productType"],
            "productGroup": r["productGroup"],
        }
        for r in rows
    ]


async def _fetch_plants(conn: asyncpg.Connection) -> list[dict]:
    # Only plants that actually appear in delivery items
    rows = await conn.fetch("""
        SELECT p."plant", p."plantName", p."salesOrganization"
        FROM plants p
        WHERE p."plant" IN (
            SELECT DISTINCT "plant"
            FROM outbound_delivery_items
            WHERE "plant" IS NOT NULL
        )
    """)
    return [
        {
            "id":               _nid("plant", r["plant"]),
            "type":             "plant",
            "label":            r["plantName"] or r["plant"],
            "plant":            r["plant"],
            "salesOrganization": r["salesOrganization"],
        }
        for r in rows
    ]


async def _fetch_edges(
    conn: asyncpg.Connection,
    node_ids: set[str],
) -> list[dict]:
    """
    Build all edges. Skips any edge whose source or target node does not
    exist in node_ids — guards against orphaned item references in the data.
    Deduplicates by (source, target, type).
    """
    edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(source: str, target: str, edge_type: str) -> None:
        if source in node_ids and target in node_ids:
            key = (source, target, edge_type)
            if key not in seen:
                seen.add(key)
                edges.append({"source": source, "target": target, "type": edge_type})

    # (1) Sales Order → Delivery
    #     Join key: outbound_delivery_items.referenceSdDocument = salesOrder
    rows = await conn.fetch("""
        SELECT DISTINCT "referenceSdDocument" AS so_id,
                        "deliveryDocument"    AS del_id
        FROM outbound_delivery_items
        WHERE "referenceSdDocument" IS NOT NULL
    """)
    for r in rows:
        add(_nid("sales_order", r["so_id"]), _nid("delivery", r["del_id"]), "fulfills")

    # (2) Delivery → Billing Document
    #     Join key: billing_document_items.referenceSdDocument = deliveryDocument
    rows = await conn.fetch("""
        SELECT DISTINCT "referenceSdDocument" AS del_id,
                        "billingDocument"     AS bill_id
        FROM billing_document_items
        WHERE "referenceSdDocument" IS NOT NULL
    """)
    for r in rows:
        add(_nid("delivery", r["del_id"]), _nid("billing_doc", r["bill_id"]), "billed_as")

    # (3) Billing Document → Journal Entry
    #     Join key: journal_entry_items.referenceDocument = billingDocument
    rows = await conn.fetch("""
        SELECT DISTINCT "referenceDocument"   AS bill_id,
                        "accountingDocument"  AS journal_id
        FROM journal_entry_items
        WHERE "referenceDocument" IS NOT NULL
    """)
    for r in rows:
        add(_nid("billing_doc", r["bill_id"]), _nid("journal_entry", r["journal_id"]), "posts_to")

    # (4) Journal Entry → Payment
    #     payments.clearingAccountingDocument points to the journal it clears
    rows = await conn.fetch("""
        SELECT DISTINCT "clearingAccountingDocument" AS journal_id,
                        "accountingDocument"         AS payment_id
        FROM payments_accounts_receivable
        WHERE "clearingAccountingDocument" IS NOT NULL
    """)
    for r in rows:
        add(_nid("journal_entry", r["journal_id"]), _nid("payment", r["payment_id"]), "cleared_by")

    # (5) Sales Order → Customer
    rows = await conn.fetch("""
        SELECT DISTINCT soh."salesOrder" AS so_id,
                        bp."customer"   AS cust_id
        FROM sales_order_headers soh
        JOIN business_partners bp ON bp."customer" = soh."soldToParty"
        WHERE soh."soldToParty" IS NOT NULL
    """)
    for r in rows:
        add(_nid("sales_order", r["so_id"]), _nid("customer", r["cust_id"]), "ordered_by")

    # (6) Billing Document → Customer
    rows = await conn.fetch("""
        SELECT DISTINCT bdh."billingDocument" AS bill_id,
                        bp."customer"         AS cust_id
        FROM billing_document_headers bdh
        JOIN business_partners bp ON bp."customer" = bdh."soldToParty"
        WHERE bdh."soldToParty" IS NOT NULL
    """)
    for r in rows:
        add(_nid("billing_doc", r["bill_id"]), _nid("customer", r["cust_id"]), "billed_to")

    # (7) Billing Document → Product
    rows = await conn.fetch("""
        SELECT DISTINCT bdi."billingDocument" AS bill_id,
                        bdi."material"        AS product_id
        FROM billing_document_items bdi
        WHERE bdi."material" IS NOT NULL
    """)
    for r in rows:
        add(_nid("billing_doc", r["bill_id"]), _nid("product", r["product_id"]), "includes")

    # (8) Delivery → Plant
    rows = await conn.fetch("""
        SELECT DISTINCT "deliveryDocument" AS del_id,
                        "plant"            AS plant_id
        FROM outbound_delivery_items
        WHERE "plant" IS NOT NULL
    """)
    for r in rows:
        add(_nid("delivery", r["del_id"]), _nid("plant", r["plant_id"]), "ships_from")

    return edges


def _build_summary(by_type: dict[str, list[str]], edges: list[dict]) -> dict:
    summary_nodes = [
        {
            "id":         f"type::{node_type}",
            "type":       "summary",
            "entityType": node_type,
            "label":      _TYPE_LABELS.get(node_type, node_type),
            "count":      len(ids),
        }
        for node_type, ids in by_type.items()
    ]

    edge_counts: dict[tuple[str, str, str], int] = {}
    for edge in edges:
        src_type = edge["source"].split("::")[0]
        tgt_type = edge["target"].split("::")[0]
        key = (src_type, tgt_type, edge["type"])
        edge_counts[key] = edge_counts.get(key, 0) + 1

    summary_edges = [
        {
            "source": f"type::{src}",
            "target": f"type::{tgt}",
            "type":   etype,
            "count":  count,
        }
        for (src, tgt, etype), count in edge_counts.items()
    ]

    return {"nodes": summary_nodes, "edges": summary_edges}


async def build_graph(pool: asyncpg.Pool) -> dict:
    async with pool.acquire() as conn:
        node_lists = [
            await _fetch_sales_orders(conn),
            await _fetch_deliveries(conn),
            await _fetch_billing_docs(conn),
            await _fetch_journal_entries(conn),
            await _fetch_payments(conn),
            await _fetch_customers(conn),
            await _fetch_products(conn),
            await _fetch_plants(conn),
        ]

        full_nodes: dict[str, dict] = {}
        by_type: dict[str, list[str]] = {}
        for node_list in node_lists:
            for node in node_list:
                full_nodes[node["id"]] = node
                by_type.setdefault(node["type"], []).append(node["id"])

        edges = await _fetch_edges(conn, set(full_nodes.keys()))

    return {
        "summary": _build_summary(by_type, edges),
        "full": {
            "nodes":   full_nodes,
            "edges":   edges,
            "by_type": by_type,
        },
    }
