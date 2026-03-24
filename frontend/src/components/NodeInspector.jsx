import { useEffect, useState } from 'react'
import { fetchNode } from '../services/api'

const ENTITY_LABELS = {
  billing_doc:   'BILLING DOCUMENT',
  sales_order:   'SALES ORDER',
  delivery:      'DELIVERY',
  journal_entry: 'JOURNAL ENTRY',
  payment:       'PAYMENT',
  customer:      'CUSTOMER',
  product:       'PRODUCT',
}

const DOC_TYPE_LABELS = {
  F2: 'F2 — Standard Invoice',
  S1: 'S1 — Cancellation',
  G2: 'G2 — Credit Memo',
  OR: 'OR — Standard Order',
  RE: 'RE — Returns',
  L2: 'L2 — Intercompany',
}

const O2C_STAGE = {
  sales_order:   0,
  delivery:      1,
  billing_doc:   2,
  journal_entry: 3,
  payment:       3,
}
const O2C_STAGES = ['Order', 'Delivery', 'Billing', 'Payment']

const RELATION_LABEL = {
  customer:      'sold to customer',
  product:       'contains product',
  delivery:      'billed from delivery',
  journal_entry: 'posted to journal',
  payment:       'cleared by payment',
  sales_order:   'originates from order',
  billing_doc:   'billed as invoice',
}

// Fields rendered in header badges — omit from the details list
const BADGE_FIELDS = new Set([
  'docType', 'billingDocumentType', 'orderType',
  'isCancelled', 'billingDocumentIsCancelled',
])

// react-force-graph simulation internals — skip these in the detail view
const SIMULATION_FIELDS = new Set([
  'id', 'type', 'label',
  'x', 'y', 'vx', 'vy', 'fx', 'fy', '__indexColor', 'index',
])

const FIELD_CONFIG = {
  soldToParty:                   { label: 'Sold To' },
  billingDate:                   { label: 'Billing Date' },
  billingDocumentDate:           { label: 'Billing Date' },
  totalNetAmount:                { label: 'Amount',         format: 'currency' },
  netAmount:                     { label: 'Amount',         format: 'currency' },
  currency:                      { label: 'Currency' },
  transactionCurrency:           { label: 'Currency' },
  cancelledBy:                   { label: 'Cancelled By',   skipEmpty: true },
  cancelledBillingDocument:      { label: 'Cancels Invoice', skipEmpty: true },
  creationDate:                  { label: 'Created' },
  deliveryStatus:                { label: 'Delivery Status' },
  billingStatus:                 { label: 'Billing Status' },
  overallDeliveryStatus:         { label: 'Delivery Status' },
  overallOrdReltdBillgStatus:    { label: 'Billing Status' },
  billingBlock:                  { label: 'Billing Block',  skipEmpty: true },
  deliveryBlock:                 { label: 'Delivery Block', skipEmpty: true },
  shippingPoint:                 { label: 'Shipping Point' },
  goodsMovementDate:             { label: 'Shipped' },
  actualGoodsMovementDate:       { label: 'Shipped' },
  movementStatus:                { label: 'Movement Status' },
  pickingStatus:                 { label: 'Picking Status' },
  companyCode:                   { label: 'Company Code' },
  fiscalYear:                    { label: 'Fiscal Year' },
  postingDate:                   { label: 'Posting Date' },
  referenceDocument:             { label: 'Reference Doc',  skipEmpty: true },
  customer:                      { label: 'Customer' },
  clearingDate:                  { label: 'Cleared On' },
  amount:                        { label: 'Amount',         format: 'currency' },
  amountInTransactionCurrency:   { label: 'Amount',         format: 'currency' },
  clearsJournal:                 { label: 'Clears Journal', skipEmpty: true },
  partner:                       { label: 'Partner ID' },
  businessPartnerFullName:       { label: 'Full Name' },
  isBlocked:                     { label: 'Blocked',        format: 'boolean' },
  businessPartnerIsBlocked:      { label: 'Blocked',        format: 'boolean' },
  cityName:                      { label: 'City' },
  country:                       { label: 'Country' },
  streetName:                    { label: 'Street' },
  product:                       { label: 'Product Code' },
  productType:                   { label: 'Type' },
  productGroup:                  { label: 'Group' },
  productDescription:            { label: 'Description' },
}

const CURRENCY_SYMBOLS = { INR: '₹', USD: '$', EUR: '€', GBP: '£' }

function formatAmount(value, currency) {
  const num = parseFloat(value)
  if (isNaN(num)) return String(value)
  const sym = CURRENCY_SYMBOLS[currency] ?? (currency ? `${currency} ` : '')
  return `${sym}${num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

// Handles both normalised and raw SAP field names (e.g. isCancelled vs billingDocumentIsCancelled)
function resolveDocType(nodeData) {
  return nodeData.docType ?? nodeData.billingDocumentType ?? nodeData.orderType ?? null
}

function resolveIsCancelled(nodeData) {
  const v = nodeData.isCancelled ?? nodeData.billingDocumentIsCancelled
  return v === true || v === 'true'
}

function resolveIsBlocked(nodeData) {
  const v = nodeData.isBlocked ?? nodeData.businessPartnerIsBlocked
  return v === true || v === 'true'
}

function Badge({ children, color = 'gray' }) {
  const palette = {
    gray:  'bg-gray-100 text-gray-600',
    green: 'bg-green-50 text-green-700 border border-green-100',
    red:   'bg-red-50 text-red-600 border border-red-100',
    blue:  'bg-blue-50 text-blue-700',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium ${palette[color]}`}>
      {children}
    </span>
  )
}

function O2CFlow({ nodeType }) {
  const currentStage = O2C_STAGE[nodeType]
  if (currentStage === undefined) return null

  return (
    <div className="px-4 py-2.5 border-b border-gray-100">
      <p className="text-[9px] font-semibold uppercase tracking-widest text-gray-400 mb-1.5">
        O2C Flow
      </p>
      <div className="flex items-center">
        {O2C_STAGES.map((stage, i) => {
          const isActive = i === currentStage
          const isPast   = i < currentStage
          return (
            <div key={stage} className="flex items-center flex-1 last:flex-none">
              <div
                className={`flex-1 text-center text-[9px] font-semibold py-1 rounded ${
                  isActive ? 'bg-blue-600 text-white'
                  : isPast  ? 'bg-blue-100 text-blue-500'
                  :           'bg-gray-100 text-gray-400'
                }`}
              >
                {stage}
              </div>
              {i < O2C_STAGES.length - 1 && (
                <span className={`mx-0.5 text-[9px] leading-none ${
                  isPast || isActive ? 'text-blue-300' : 'text-gray-200'
                }`}>›</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function NodeInspector({ node, onClose, onNodeClick }) {
  const [detail,  setDetail]  = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

  useEffect(() => {
    if (!node) return
    setLoading(true)
    setDetail(null)
    setError(null)
    fetchNode(node.id)
      .then(setDetail)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [node?.id])

  if (!node) return null

  const nodeData    = detail?.node ?? {}
  const entityLabel = ENTITY_LABELS[node.type] ?? node.type.replace(/_/g, ' ').toUpperCase()
  const docType     = resolveDocType(nodeData)
  const cancelled   = resolveIsCancelled(nodeData)
  const blocked     = resolveIsBlocked(nodeData)
  const currency    = nodeData.transactionCurrency ?? nodeData.currency ?? ''

  const fields = Object.entries(nodeData)
    .filter(([k]) => !SIMULATION_FIELDS.has(k) && !BADGE_FIELDS.has(k))
    .flatMap(([k, v]) => {
      if (v === null || v === undefined || v === '') return []
      const cfg = FIELD_CONFIG[k]
      if (!cfg) return []
      if (cfg.skipEmpty && !v) return []
      let display
      if (cfg.format === 'currency') display = formatAmount(v, currency)
      else if (cfg.format === 'boolean') display = v ? 'Yes' : 'No'
      else display = String(v)
      return [[cfg.label, display]]
    })
    // First occurrence of each label wins (handles dual-named SAP fields)
    .filter(([label], i, arr) => arr.findIndex(([l]) => l === label) === i)

  const showBadges = docType || node.type === 'billing_doc' || node.type === 'sales_order'
    || node.type === 'customer'

  return (
    <div className="absolute top-4 left-4 w-[290px] bg-white rounded-2xl shadow-xl border border-gray-100 overflow-hidden z-10 flex flex-col max-h-[calc(100vh-8rem)]">

      <div className="px-4 pt-4 pb-3 border-b border-gray-100">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-[9px] font-semibold uppercase tracking-widest text-gray-400 mb-0.5">
              {entityLabel}
            </p>
            <h2
              className="text-gray-900 font-bold text-base leading-tight truncate"
              title={node.label}
            >
              {node.label}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="mt-0.5 w-6 h-6 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100 text-xs shrink-0 transition-colors"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {showBadges && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {docType && (
              <Badge color="blue">
                {DOC_TYPE_LABELS[docType] ?? docType}
              </Badge>
            )}
            {node.type === 'customer' ? (
              <Badge color={blocked ? 'red' : 'green'}>
                {blocked ? '● Blocked' : '● Active'}
              </Badge>
            ) : (
              <Badge color={cancelled ? 'red' : 'green'}>
                {cancelled ? '● Cancelled' : '● Active'}
              </Badge>
            )}
          </div>
        )}
      </div>

      <O2CFlow nodeType={node.type} />

      <div className="overflow-y-auto flex-1">

        {loading && (
          <div className="px-4 py-6 text-center text-gray-400 text-xs">Loading…</div>
        )}

        {error && (
          <div className="px-4 py-4 text-center text-red-500 text-xs">{error}</div>
        )}

        {!loading && detail && (
          <>
            {fields.length > 0 && (
              <div className="px-4 py-3">
                {fields.map(([label, value]) => (
                  <div
                    key={label}
                    className="flex justify-between items-baseline py-1.5 border-b border-gray-50 last:border-0 gap-3"
                  >
                    <span className="text-[11px] text-gray-400 shrink-0 leading-4">
                      {label}
                    </span>
                    <span
                      className="text-[11px] text-gray-800 font-medium text-right truncate max-w-[150px] leading-4"
                      title={value}
                    >
                      {value}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {detail.neighbors?.length > 0 && (
              <div className="px-4 py-3 border-t border-gray-100">
                <p className="text-[9px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
                  Connected · {detail.neighbors.length}
                </p>
                <div className="space-y-0.5">
                  {detail.neighbors.map(n => (
                    <button
                      key={n.id}
                      onClick={() => onNodeClick?.(n)}
                      className="w-full flex items-center gap-2 py-1.5 px-2 -mx-2 rounded-lg hover:bg-gray-50 active:bg-gray-100 transition-colors text-left group"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="text-[10px] text-gray-400 leading-tight">
                          {RELATION_LABEL[n.type] ?? n.type.replace(/_/g, ' ')}
                        </p>
                        <p
                          className="text-[11px] text-gray-700 font-medium truncate leading-snug mt-0.5"
                          title={n.label}
                        >
                          {n.label}
                        </p>
                      </div>
                      <span className="text-gray-300 group-hover:text-blue-400 text-sm shrink-0 transition-colors">
                        ›
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
