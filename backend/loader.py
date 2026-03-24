#!/usr/bin/env python3
"""
SAP O2C data loader — PostgreSQL (Neon.tech).

Globs all JSONL part files in each entity folder and batch-inserts
into PostgreSQL. product_storage_locations is skipped by default.

Requires:
    pip install psycopg2-binary python-dotenv

Usage:
    python loader.py                               # core tables only
    python loader.py --include-storage             # also load storage locs
    python loader.py --data-dir /path/to/jsonl     # custom data root

Environment:
    DATABASE_URL  PostgreSQL connection string from Neon dashboard, e.g.
                  postgresql://user:pass@host/dbname?sslmode=require
"""

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

BATCH_SIZE = 500


# ──────────────────────────────────────────────────────────────
# Table configuration
#
# folders: one or more source folder names under --data-dir.
#          Multiple folders merge into one table
#          (e.g. billing headers + cancellations).
# columns: drives both INSERT column list and field extraction.
#          Fields absent in a JSON record become NULL.
# ──────────────────────────────────────────────────────────────

@dataclass
class TableConfig:
    folders: list[str]
    columns: list[str]


CORE_TABLES: dict[str, TableConfig] = {
    "sales_order_headers": TableConfig(
        folders=["sales_order_headers"],
        columns=[
            "salesOrder", "salesOrderType", "soldToParty", "creationDate",
            "totalNetAmount", "transactionCurrency", "overallDeliveryStatus",
            "overallOrdReltdBillgStatus", "requestedDeliveryDate",
            "headerBillingBlockReason", "deliveryBlockReason",
            "incotermsClassification", "customerPaymentTerms",
        ],
    ),
    "sales_order_items": TableConfig(
        folders=["sales_order_items"],
        columns=[
            "salesOrder", "salesOrderItem", "material", "requestedQuantity",
            "requestedQuantityUnit", "netAmount", "materialGroup",
            "productionPlant", "storageLocation", "salesDocumentRjcnReason",
            "itemBillingBlockReason",
        ],
    ),
    "sales_order_schedule_lines": TableConfig(
        folders=["sales_order_schedule_lines"],
        columns=[
            "salesOrder", "salesOrderItem", "scheduleLine",
            "confirmedDeliveryDate", "orderQuantityUnit",
            "confdOrderQtyByMatlAvailCheck",
        ],
    ),
    "outbound_delivery_headers": TableConfig(
        folders=["outbound_delivery_headers"],
        columns=[
            "deliveryDocument", "shippingPoint", "creationDate",
            "actualGoodsMovementDate", "overallGoodsMovementStatus",
            "overallPickingStatus", "hdrGeneralIncompletionStatus",
            "headerBillingBlockReason", "deliveryBlockReason",
        ],
    ),
    "outbound_delivery_items": TableConfig(
        folders=["outbound_delivery_items"],
        columns=[
            "deliveryDocument", "deliveryDocumentItem", "referenceSdDocument",
            "referenceSdDocumentItem", "plant", "storageLocation",
            "actualDeliveryQuantity", "deliveryQuantityUnit",
            "itemBillingBlockReason",
        ],
    ),
    # Both billing_document_headers and billing_document_cancellations
    # share the same structure and are merged into one table.
    # Differentiate at query time with "billingDocumentType":
    #   F2 = invoice, S1 = cancellation, G2 = credit memo
    "billing_document_headers": TableConfig(
        folders=["billing_document_headers", "billing_document_cancellations"],
        columns=[
            "billingDocument", "billingDocumentType", "soldToParty",
            "billingDocumentDate", "totalNetAmount", "transactionCurrency",
            "billingDocumentIsCancelled", "cancelledBillingDocument",
            "accountingDocument", "companyCode", "fiscalYear",
        ],
    ),
    "billing_document_items": TableConfig(
        folders=["billing_document_items"],
        columns=[
            "billingDocument", "billingDocumentItem", "material",
            "billingQuantity", "netAmount", "referenceSdDocument",
            "referenceSdDocumentItem",
        ],
    ),
    "journal_entry_items": TableConfig(
        folders=["journal_entry_items_accounts_receivable"],
        columns=[
            "accountingDocument", "accountingDocumentItem", "referenceDocument",
            "glAccount", "amountInTransactionCurrency", "postingDate",
            "customer", "companyCode", "fiscalYear",
            "clearingAccountingDocument", "accountingDocumentType",
            "financialAccountType",
        ],
    ),
    "payments_accounts_receivable": TableConfig(
        folders=["payments_accounts_receivable"],
        columns=[
            "accountingDocument", "accountingDocumentItem", "customer",
            "amountInTransactionCurrency", "postingDate", "clearingDate",
            "clearingAccountingDocument", "salesDocument", "invoiceReference",
            "glAccount", "profitCenter",
        ],
    ),
    "business_partners": TableConfig(
        folders=["business_partners"],
        columns=[
            "businessPartner", "customer", "businessPartnerFullName",
            "businessPartnerName", "businessPartnerIsBlocked",
            "isMarkedForArchiving",
        ],
    ),
    "business_partner_addresses": TableConfig(
        folders=["business_partner_addresses"],
        columns=[
            "businessPartner", "addressId", "cityName", "country",
            "postalCode", "region", "streetName",
        ],
    ),
    "customer_company_assignments": TableConfig(
        folders=["customer_company_assignments"],
        columns=[
            "customer", "companyCode", "paymentTerms", "reconciliationAccount",
            "deletionIndicator", "customerAccountGroup",
        ],
    ),
    "customer_sales_area_assignments": TableConfig(
        folders=["customer_sales_area_assignments"],
        columns=[
            "customer", "salesOrganization", "distributionChannel", "division",
            "currency", "customerPaymentTerms", "incotermsClassification",
            "shippingCondition",
        ],
    ),
    "products": TableConfig(
        folders=["products"],
        columns=[
            "product", "productType", "grossWeight", "netWeight",
            "productGroup", "baseUnit", "division", "isMarkedForDeletion",
        ],
    ),
    "product_descriptions": TableConfig(
        folders=["product_descriptions"],
        columns=["product", "language", "productDescription"],
    ),
    "product_plants": TableConfig(
        folders=["product_plants"],
        columns=[
            "product", "plant", "countryOfOrigin", "profitCenter",
            "availabilityCheckType", "mrpType",
        ],
    ),
    "plants": TableConfig(
        folders=["plants"],
        columns=[
            "plant", "plantName", "salesOrganization", "distributionChannel",
            "division", "addressId",
        ],
    ),
}

STORAGE_TABLE: dict[str, TableConfig] = {
    "product_storage_locations": TableConfig(
        folders=["product_storage_locations"],
        columns=["material", "plant", "storageLocation", "unrestrictedStock", "unitOfMeasure"],
    ),
}


# ──────────────────────────────────────────────────────────────
# Core helpers
# ──────────────────────────────────────────────────────────────

def iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def make_insert(table: str, columns: list[str]) -> str:
    # PostgreSQL requires quoted identifiers for camelCase column names.
    # execute_values replaces the single %s with the expanded VALUES block:
    #   (%s, %s, %s), (%s, %s, %s), ...  — one tuple per row in the batch.
    quoted_cols = ", ".join(f'"{c}"' for c in columns)
    return (
        f'INSERT INTO {table} ({quoted_cols}) VALUES %s'
        f' ON CONFLICT DO NOTHING'
    )


def load_table(
    cursor: psycopg2.extensions.cursor,
    table: str,
    config: TableConfig,
    data_dir: Path,
) -> int:
    sql = make_insert(table, config.columns)
    total = 0
    batch: list[tuple] = []

    for folder_name in config.folders:
        folder_path = data_dir / folder_name
        if not folder_path.exists():
            print(f"  WARNING  {folder_name}/ not found — skipping")
            continue

        files = sorted(folder_path.glob("*.jsonl"))
        if not files:
            print(f"  WARNING  no .jsonl files in {folder_name}/ — skipping")
            continue

        for file_path in files:
            for record in iter_jsonl(file_path):
                # psycopg2 maps Python bool → PostgreSQL BOOLEAN natively.
                # No coercion needed; missing fields become NULL.
                row = tuple(record.get(col) for col in config.columns)
                batch.append(row)
                if len(batch) >= BATCH_SIZE:
                    # execute_values builds one multi-row INSERT per batch —
                    # much faster than executemany (which sends N statements).
                    psycopg2.extras.execute_values(cursor, sql, batch)
                    total += len(batch)
                    batch.clear()

    if batch:
        psycopg2.extras.execute_values(cursor, sql, batch)
        total += len(batch)

    return total


def apply_schema(cursor: psycopg2.extensions.cursor, schema_sql: str) -> None:
    # Split on semicolons, skip blank/comment-only statements.
    for statement in schema_sql.split(";"):
        stmt = statement.strip()
        if stmt and not re.fullmatch(r"(--.*)?\s*", stmt):
            cursor.execute(stmt)


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Load SAP O2C JSONL data into PostgreSQL.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parent.parent / "data",
        help="Root directory containing one subfolder per entity (default: ../data)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).parent / "schema.sql",
        help="SQL schema file to initialise the DB (default: ./schema.sql)",
    )
    parser.add_argument(
        "--include-storage",
        action="store_true",
        help="Also load product_storage_locations (18 part files, slow)",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("ERROR: DATABASE_URL environment variable not set")
    if not args.data_dir.exists():
        raise SystemExit(f"ERROR: data directory not found: {args.data_dir}")
    if not args.schema.exists():
        raise SystemExit(f"ERROR: schema file not found: {args.schema}")

    print(f"Data root : {args.data_dir}")
    print()

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema(cur, args.schema.read_text(encoding="utf-8"))
            conn.commit()
            print("Schema applied.")
            print()

            tables_to_load = dict(CORE_TABLES)
            if args.include_storage:
                tables_to_load.update(STORAGE_TABLE)
            else:
                print("Skipping  : product_storage_locations (pass --include-storage to load)")
                print()

            total_rows = 0
            for table, config in tables_to_load.items():
                n = load_table(cur, table, config, args.data_dir)
                label = f"(from {', '.join(config.folders)})" if len(config.folders) > 1 else ""
                print(f"  {table:<45} {n:>6} rows  {label}")
                total_rows += n
                conn.commit()  # commit per table so a late failure doesn't lose everything

    print()
    print(f"Done. {total_rows} total rows loaded.")


if __name__ == "__main__":
    main()
