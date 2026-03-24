-- ══════════════════════════════════════════════════════════════
-- SAP Order-to-Cash Schema — PostgreSQL
--
-- Join chain:
--   sales_order_headers
--     ← outbound_delivery_items.referenceSdDocument          (1)
--     ← outbound_delivery_headers.deliveryDocument
--     ← billing_document_items.referenceSdDocument           (2)
--     ← billing_document_headers.billingDocument
--     ← journal_entry_items.referenceDocument                (3)
--     ← journal_entry_items.accountingDocument
--     ← payments_accounts_receivable.clearingAccountingDocument (4)
-- ══════════════════════════════════════════════════════════════


-- ─────────────────────────────────────────────────────────────
-- Sales Orders
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sales_order_headers (
    "salesOrder"                    TEXT PRIMARY KEY,
    "salesOrderType"                TEXT,
    "soldToParty"                   TEXT,
    "creationDate"                  DATE,
    "totalNetAmount"                NUMERIC(15,2),
    "transactionCurrency"           TEXT,
    "overallDeliveryStatus"         TEXT,
    "overallOrdReltdBillgStatus"    TEXT,
    "requestedDeliveryDate"         DATE,
    "headerBillingBlockReason"      TEXT,
    "deliveryBlockReason"           TEXT,
    "incotermsClassification"       TEXT,
    "customerPaymentTerms"          TEXT
);

CREATE TABLE IF NOT EXISTS sales_order_items (
    "salesOrder"                    TEXT            NOT NULL,
    "salesOrderItem"                TEXT            NOT NULL,
    "material"                      TEXT,
    "requestedQuantity"             NUMERIC(15,3),
    "requestedQuantityUnit"         TEXT,
    "netAmount"                     NUMERIC(15,2),
    "materialGroup"                 TEXT,
    "productionPlant"               TEXT,
    "storageLocation"               TEXT,
    "salesDocumentRjcnReason"       TEXT,
    "itemBillingBlockReason"        TEXT,
    PRIMARY KEY ("salesOrder", "salesOrderItem")
);

CREATE TABLE IF NOT EXISTS sales_order_schedule_lines (
    "salesOrder"                    TEXT            NOT NULL,
    "salesOrderItem"                TEXT            NOT NULL,
    "scheduleLine"                  TEXT            NOT NULL,
    "confirmedDeliveryDate"         DATE,
    "orderQuantityUnit"             TEXT,
    "confdOrderQtyByMatlAvailCheck" NUMERIC(15,3),
    PRIMARY KEY ("salesOrder", "salesOrderItem", "scheduleLine")
);


-- ─────────────────────────────────────────────────────────────
-- Outbound Deliveries
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS outbound_delivery_headers (
    "deliveryDocument"              TEXT PRIMARY KEY,
    "shippingPoint"                 TEXT,
    "creationDate"                  DATE,
    "actualGoodsMovementDate"       DATE,
    "overallGoodsMovementStatus"    TEXT,
    "overallPickingStatus"          TEXT,
    "hdrGeneralIncompletionStatus"  TEXT,
    "headerBillingBlockReason"      TEXT,
    "deliveryBlockReason"           TEXT
);

-- Join (1): "referenceSdDocument" → sales_order_headers."salesOrder"
CREATE TABLE IF NOT EXISTS outbound_delivery_items (
    "deliveryDocument"              TEXT            NOT NULL,
    "deliveryDocumentItem"          TEXT            NOT NULL,
    "referenceSdDocument"           TEXT,
    "referenceSdDocumentItem"       TEXT,
    "plant"                         TEXT,
    "storageLocation"               TEXT,
    "actualDeliveryQuantity"        NUMERIC(15,3),
    "deliveryQuantityUnit"          TEXT,
    "itemBillingBlockReason"        TEXT,
    PRIMARY KEY ("deliveryDocument", "deliveryDocumentItem")
);


-- ─────────────────────────────────────────────────────────────
-- Billing Documents
-- billing_document_headers and billing_document_cancellations
-- share this table, differentiated by "billingDocumentType":
--   F2 = standard invoice
--   S1 = cancellation ("cancelledBillingDocument" → the F2 it reverses)
--   G2 = credit memo
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS billing_document_headers (
    "billingDocument"               TEXT PRIMARY KEY,
    "billingDocumentType"           TEXT,
    "soldToParty"                   TEXT,
    "billingDocumentDate"           DATE,
    "totalNetAmount"                NUMERIC(15,2),
    "transactionCurrency"           TEXT,
    "billingDocumentIsCancelled"    BOOLEAN         DEFAULT FALSE,
    "cancelledBillingDocument"      TEXT,
    "accountingDocument"            TEXT,
    "companyCode"                   TEXT,
    "fiscalYear"                    TEXT
);

-- Join (2): "referenceSdDocument" → outbound_delivery_headers."deliveryDocument"
CREATE TABLE IF NOT EXISTS billing_document_items (
    "billingDocument"               TEXT            NOT NULL,
    "billingDocumentItem"           TEXT            NOT NULL,
    "material"                      TEXT,
    "billingQuantity"               NUMERIC(15,3),
    "netAmount"                     NUMERIC(15,2),
    "referenceSdDocument"           TEXT,
    "referenceSdDocumentItem"       TEXT,
    PRIMARY KEY ("billingDocument", "billingDocumentItem")
);


-- ─────────────────────────────────────────────────────────────
-- Finance
-- ─────────────────────────────────────────────────────────────

-- Join (3): "referenceDocument" → billing_document_headers."billingDocument"
CREATE TABLE IF NOT EXISTS journal_entry_items (
    "accountingDocument"            TEXT            NOT NULL,
    "accountingDocumentItem"        TEXT            NOT NULL,
    "referenceDocument"             TEXT,
    "glAccount"                     TEXT,
    "amountInTransactionCurrency"   NUMERIC(15,2),
    "postingDate"                   DATE,
    "customer"                      TEXT,
    "companyCode"                   TEXT            NOT NULL,
    "fiscalYear"                    TEXT            NOT NULL,
    "clearingAccountingDocument"    TEXT,
    "accountingDocumentType"        TEXT,
    "financialAccountType"          TEXT,
    PRIMARY KEY ("accountingDocument", "accountingDocumentItem", "companyCode", "fiscalYear")
);

-- Join (4): "clearingAccountingDocument" → journal_entry_items."accountingDocument"
CREATE TABLE IF NOT EXISTS payments_accounts_receivable (
    "accountingDocument"            TEXT            NOT NULL,
    "accountingDocumentItem"        TEXT            NOT NULL,
    "customer"                      TEXT,
    "amountInTransactionCurrency"   NUMERIC(15,2),
    "postingDate"                   DATE,
    "clearingDate"                  DATE,
    "clearingAccountingDocument"    TEXT,
    "salesDocument"                 TEXT,
    "invoiceReference"              TEXT,
    "glAccount"                     TEXT,
    "profitCenter"                  TEXT,
    PRIMARY KEY ("accountingDocument", "accountingDocumentItem")
);


-- ─────────────────────────────────────────────────────────────
-- Business Partners & Customers
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS business_partners (
    "businessPartner"               TEXT PRIMARY KEY,
    "customer"                      TEXT,
    "businessPartnerFullName"       TEXT,
    "businessPartnerName"           TEXT,
    "businessPartnerIsBlocked"      BOOLEAN         DEFAULT FALSE,
    "isMarkedForArchiving"          BOOLEAN         DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS business_partner_addresses (
    "businessPartner"               TEXT            NOT NULL,
    "addressId"                     TEXT            NOT NULL,
    "cityName"                      TEXT,
    "country"                       TEXT,
    "postalCode"                    TEXT,
    "region"                        TEXT,
    "streetName"                    TEXT,
    PRIMARY KEY ("businessPartner", "addressId")
);

CREATE TABLE IF NOT EXISTS customer_company_assignments (
    "customer"                      TEXT            NOT NULL,
    "companyCode"                   TEXT            NOT NULL,
    "paymentTerms"                  TEXT,
    "reconciliationAccount"         TEXT,
    "deletionIndicator"             BOOLEAN         DEFAULT FALSE,
    "customerAccountGroup"          TEXT,
    PRIMARY KEY ("customer", "companyCode")
);

CREATE TABLE IF NOT EXISTS customer_sales_area_assignments (
    "customer"                      TEXT            NOT NULL,
    "salesOrganization"             TEXT            NOT NULL,
    "distributionChannel"           TEXT            NOT NULL,
    "division"                      TEXT            NOT NULL,
    "currency"                      TEXT,
    "customerPaymentTerms"          TEXT,
    "incotermsClassification"       TEXT,
    "shippingCondition"             TEXT,
    PRIMARY KEY ("customer", "salesOrganization", "distributionChannel", "division")
);


-- ─────────────────────────────────────────────────────────────
-- Products
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS products (
    "product"                       TEXT PRIMARY KEY,
    "productType"                   TEXT,
    "grossWeight"                   NUMERIC(15,3),
    "netWeight"                     NUMERIC(15,3),
    "productGroup"                  TEXT,
    "baseUnit"                      TEXT,
    "division"                      TEXT,
    "isMarkedForDeletion"           BOOLEAN         DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS product_descriptions (
    "product"                       TEXT            NOT NULL,
    "language"                      TEXT            NOT NULL,
    "productDescription"            TEXT,
    PRIMARY KEY ("product", "language")
);

CREATE TABLE IF NOT EXISTS product_plants (
    "product"                       TEXT            NOT NULL,
    "plant"                         TEXT            NOT NULL,
    "countryOfOrigin"               TEXT,
    "profitCenter"                  TEXT,
    "availabilityCheckType"         TEXT,
    "mrpType"                       TEXT,
    PRIMARY KEY ("product", "plant")
);

CREATE TABLE IF NOT EXISTS plants (
    "plant"                         TEXT PRIMARY KEY,
    "plantName"                     TEXT,
    "salesOrganization"             TEXT,
    "distributionChannel"           TEXT,
    "division"                      TEXT,
    "addressId"                     TEXT
);


-- ─────────────────────────────────────────────────────────────
-- Optional — load separately (18 part files, not in O2C flow)
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS product_storage_locations (
    id                              BIGSERIAL PRIMARY KEY,
    "material"                      TEXT            NOT NULL,
    "plant"                         TEXT            NOT NULL,
    "storageLocation"               TEXT            NOT NULL,
    "unrestrictedStock"             NUMERIC(15,3),
    "unitOfMeasure"                 TEXT,
    UNIQUE ("material", "plant", "storageLocation")
);


-- ══════════════════════════════════════════════════════════════
-- Indices
-- ══════════════════════════════════════════════════════════════

-- Flow trace + broken flow: the four join keys
CREATE INDEX IF NOT EXISTS idx_odi_referenceSdDocument  ON outbound_delivery_items("referenceSdDocument");
CREATE INDEX IF NOT EXISTS idx_bdi_referenceSdDocument  ON billing_document_items("referenceSdDocument");
CREATE INDEX IF NOT EXISTS idx_je_referenceDocument     ON journal_entry_items("referenceDocument");
CREATE INDEX IF NOT EXISTS idx_pay_clearingAcctDoc      ON payments_accounts_receivable("clearingAccountingDocument");

-- Broken flow: cancellation detection
CREATE INDEX IF NOT EXISTS idx_bdh_billingDocType       ON billing_document_headers("billingDocumentType");
CREATE INDEX IF NOT EXISTS idx_bdh_isCancelled          ON billing_document_headers("billingDocumentIsCancelled");
CREATE INDEX IF NOT EXISTS idx_bdh_cancelledBillingDoc  ON billing_document_headers("cancelledBillingDocument");

-- Query 3: products with most billing docs
CREATE INDEX IF NOT EXISTS idx_bdi_material             ON billing_document_items("material");

-- NL→SQL tools: customer lookups, status filters, date ranges
CREATE INDEX IF NOT EXISTS idx_soh_soldToParty          ON sales_order_headers("soldToParty");
CREATE INDEX IF NOT EXISTS idx_soh_deliveryStatus       ON sales_order_headers("overallDeliveryStatus");
CREATE INDEX IF NOT EXISTS idx_soh_billingStatus        ON sales_order_headers("overallOrdReltdBillgStatus");
CREATE INDEX IF NOT EXISTS idx_soh_creationDate         ON sales_order_headers("creationDate");
CREATE INDEX IF NOT EXISTS idx_bdh_soldToParty          ON billing_document_headers("soldToParty");
CREATE INDEX IF NOT EXISTS idx_bdh_billingDate          ON billing_document_headers("billingDocumentDate");
CREATE INDEX IF NOT EXISTS idx_je_postingDate           ON journal_entry_items("postingDate");
CREATE INDEX IF NOT EXISTS idx_pay_clearingDate         ON payments_accounts_receivable("clearingDate");
CREATE INDEX IF NOT EXISTS idx_bp_customer              ON business_partners("customer");
