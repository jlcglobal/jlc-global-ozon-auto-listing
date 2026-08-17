export type FinanceStore = {
  id: string;
  name: string;
  status: string;
  sync_status: string;
  last_sync_at?: string | null;
  sync_error?: string | null;
};

export type FinanceOverview = {
  period: { date_from: string; date_to: string };
  store_id: string;
  stores: FinanceStore[];
  summary: {
    sales: string;
    ad_spend: string;
    ozon_fees: string;
    logistics: string;
    expected_profit: string;
    expected_margin: number;
    effective_order_lines: number;
    fully_covered_order_lines: number;
  };
  coverage: { purchase: number; finance: number; logistics: number; ads: number };
  advertising: {
    total: string;
    source: string;
    source_label: string;
    api_record_count: number;
    imported_record_count: number;
    attributed_to_orders: boolean;
  };
  reconciliation: { unmatched_finance_rows: number; unmatched_ads_rows: number };
  warnings: string[];
  ozon_write_api_calls: number;
  inventory_api_calls: number;
};

export type FinanceOrder = {
  id: string;
  store_id: string;
  order_number: string;
  posting_number: string;
  sku: string;
  offer_id?: string;
  product_name?: string;
  image_url?: string;
  order_date?: string;
  buyer_paid_cny: string;
  purchase_cost_cny: string;
  finance_fee_cny: string;
  logistics_cny: string;
  ad_spend_cny: string;
  profit_cny: string;
  profit_margin: number;
  cost_sources: {
    finance: string;
    logistics: string;
    ads: string;
  };
  has_estimates: boolean;
  coverage: { purchase: boolean; finance: boolean; logistics: boolean; ads: boolean };
  fully_covered: boolean;
};

export type FinanceProduct = {
  store_id: string;
  sku: string;
  offer_id?: string;
  product_name?: string;
  image_url?: string;
  order_lines: number;
  missing_purchase_lines: number;
  sales_cny: string;
  profit_cny: string;
  profit_margin: number;
};

export type FinanceReconciliationItem = {
  id: string;
  file_type: string;
  source_row_number: number;
  occurred_at?: string;
  posting_number?: string;
  order_number?: string;
  sku?: string;
  amount_cny: string;
  reason: string;
  resolution_status: string;
  store_id: string;
};

export type FinanceSyncStatus = {
  last_successful_sync_date?: string | null;
  last_successful_sync_at?: string | null;
  runs: Array<{
    id: string;
    store_id: string;
    status: string;
    started_at: string;
    finished_at?: string | null;
    changed_rows: number;
    write_api_calls: number;
    error?: string | null;
  }>;
  ozon_write_api_calls: number;
  inventory_api_calls: number;
};

export type FinanceImportPreview = {
  file_name: string;
  file_kind: string;
  headers: string[];
  row_count: number;
  mapping_candidates: Array<{
    source_header: string;
    target_field?: string | null;
    confidence: number;
  }>;
};

export type FinanceImportResult = {
  batch_id: string;
  status: string;
  row_count: number;
  matched_count: number;
  unmatched_count: number;
  rollback_available: boolean;
};
