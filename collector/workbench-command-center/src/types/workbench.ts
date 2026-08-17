export type SystemStatus = {
  state: string;
  label: string;
  message: string;
  codex_ready: boolean;
  active_worker_count: number;
  batch_running: boolean;
  image_slot_concurrency: number;
  automatic_retry_limit: number;
};

export type WorkbenchSettings = {
  schema_version?: string;
  auto_mode_enabled?: boolean;
  default_review_mode?: string;
  learning_threshold?: number;
  fixed_cny_to_rub?: number;
  rub_rounding?: number;
  updated_at?: string;
  can_manage_settings?: boolean;
};

export type ProductCard = {
  product_id: string;
  title_cn: string;
  title_ru?: string;
  source_url?: string;
  state: string;
  workflow_bucket: string;
  raw_status: string;
  current_step: string;
  progress: number;
  sku_count: number;
  image_count: number;
  selected_store_count?: number;
  batch_id?: string;
  batch_running?: boolean;
  thumbnail_url?: string;
  search_terms?: string;
  pipeline_progress?: {
    step?: string;
    step_label?: string;
    is_running?: boolean;
    progress?: number;
  };
  attention_required?: boolean;
  risk?: { level: string; items?: Array<{ level: string; title: string; message: string }> };
};

export type RiskItem = {
  product_id: string;
  title?: string;
  level?: string;
  message?: string;
  category?: string;
  type?: string;
  step?: string;
  occurred_at?: string;
  at?: string;
  sku_count?: number;
};

export type CategoryCandidate = {
  category_id: number | string;
  type_id: number | string;
  name?: string;
  name_zh?: string;
  path?: string[] | string;
  path_zh?: string[] | string;
};

export type CategorySearchResponse = {
  query: string;
  items: CategoryCandidate[];
  count: number;
  ozon_write_api_calls?: number;
  inventory_api_calls?: number;
};

export type CategoryRulesResponse = {
  required_attribute_ids?: Array<number | string>;
  aspect_attribute_ids?: Array<number | string>;
  [key: string]: unknown;
};

export type CategoryUpdateResponse = {
  saved?: boolean;
  invalidated?: string[];
  message?: string;
  ozon_write_api_calls?: number;
  inventory_api_calls?: number;
};

export type RisksResponse = {
  items: RiskItem[];
  rules?: Array<{ id: string; name: string; action: string; immutable?: boolean }>;
};

export type ProductDetail = {
  product_id: string;
  raw_status: string;
  current_step: string;
  batch_running?: boolean;
  source?: {
    title_cn?: string;
    source_url?: string;
    captured_at?: string;
  };
  status?: {
    status?: string;
    progress?: number;
    current_step?: string;
    last_run_at?: string;
    active_step?: {
      name?: string;
      started_at?: string;
    } | null;
    failed_step?: string;
    completed_steps?: string[];
    pending_steps?: string[];
    active_image_slots?: string[];
    image_slot_service_wait_count_by_slot?: Record<string, number>;
    steps?: Array<{
      name: string;
      status: string;
      started_at?: string;
      finished_at?: string;
      error?: string | null;
    }>;
    ozon?: {
      upload_status?: string;
      task_id?: string;
      offer_id?: string;
      shop_name?: string;
      issue_summary?: OzonIssueSummary;
    };
    ozon_issue_summary?: OzonIssueSummary;
  };
  content?: {
    title_ru?: string;
    description_ru?: string;
    tags?: string[];
  };
  analysis?: {
    product_type?: string;
    category?: string;
    target_customer?: string;
    selling_points?: Array<string | { text?: string; title?: string; value?: string }>;
    risks?: Array<{ area?: string; message?: string; level?: string }>;
    variant_decision?: string;
    grouping_decision?: string;
    is_aspect_basis?: string;
    [key: string]: unknown;
  };
  attributes?: {
    summary?: {
      total?: number;
      filled?: number;
      missing_required?: number;
      estimated?: number;
      unknown?: number;
    };
    missing_required_attributes?: Array<{
      attribute_id?: number | string;
      attribute_name?: string;
      attribute_name_zh?: string;
      required?: boolean;
    }>;
    attributes?: Array<{
      attribute_id?: number | string;
      attribute_name?: string;
      attribute_name_zh?: string;
      required?: boolean;
      value?: unknown;
      source?: string;
      validation_status?: string;
      dictionary_value_id?: number | string;
      confidence?: number;
    }>;
  };
  risk?: { level?: string; items?: RiskItem[] };
  ai_suggestions?: Array<{
    id?: string;
    title?: string;
    message?: string;
    detail?: string;
    category?: string;
    status?: string;
  }>;
  skus?: Array<{
    sku_id?: string;
    offer_id?: string;
    name?: string;
    title?: string;
    selected?: boolean;
    price_cny?: number;
    selling_price_cny?: number;
    selling_price_rub?: number;
    purchase_price_cny?: number;
    profit_cny?: number;
    profit_rate?: number;
    weight_g?: number;
    package_weight_g?: number;
    capacity_ml?: number | string | null;
    dimensions_cm?: { length?: number; width?: number; height?: number };
    package_dimensions_cm?: { length?: number; width?: number; height?: number };
    variant_decision?: string;
    aspect_basis?: string;
    sku_row?: {
      dynamic_attributes?: Record<string, {
        attribute_id?: number | string;
        attribute_name?: string;
        attribute_name_zh?: string;
        name?: string;
        canonical_value?: unknown;
        canonical_unit?: string;
        target_value?: unknown;
        target_unit?: string;
        value?: unknown;
      }>;
      [key: string]: unknown;
    };
    image_url?: string;
    binding_required?: boolean;
    binding_status?: string;
    image_missing?: boolean;
    image_binding?: {
      selected_image_path?: string;
      source_type?: string;
      bound_at?: string;
    } | null;
  }>;
  images?: Array<{
    slot: string;
    url?: string;
    download_url?: string;
    state?: string;
    image_type?: string;
    type?: string;
    role?: string;
    status?: string;
    purpose?: string;
    score?: number;
    issues?: string[];
    russian_text?: string[];
    retry_count?: number;
  }>;
  image_assets?: Record<string, Array<{
    path?: string;
    url?: string;
    state?: string;
    slot?: string;
    type?: string;
  }>>;
  image_contract?: {
    expected_total_count?: number;
    actual_main_count?: number;
    actual_shared_detail_count?: number;
  };
  pipeline_progress?: {
    step?: string;
    step_label?: string;
    is_running?: boolean;
    progress?: number;
    status_note?: string;
    active_step?: {
      name?: string;
      started_at?: string;
    } | null;
    active_step_elapsed_seconds?: number | null;
    active_step_attempt?: number;
    ai_service_state?: string;
    ai_service_reason?: string;
    ai_service_retry_after?: string;
    worker_pid?: number | null;
    worker_last_heartbeat_at?: string | null;
    worker_last_progress_at?: string | null;
    active_image_slots?: string[];
    planned_image_slots?: number;
    generated_image_slots?: number;
    completed_image_slots?: number;
  };
  attention_required?: boolean;
  category?: {
    category_id?: number | string;
    type_id?: number | string;
    category_name?: string;
    category_name_zh?: string;
    category_path?: string;
    category_path_zh?: string;
    match_status?: string;
    confidence?: number;
  };
  stores?: ShopCard[];
  publications?: {
    stores?: Record<string, {
      selected?: boolean;
      status?: string;
      upload_status?: string;
      sku_publications?: Array<{
        sku_id?: string;
        offer_id?: string;
        task_id?: string;
        action?: string;
        errors?: string[];
        warnings?: string[];
      }>;
    }>;
  };
  publication_summary?: {
    selected?: number;
    success?: number;
    pending?: number;
    failed?: number;
    skipped?: number;
  };
  pricing?: {
    recommendation?: string;
    exchange_rate?: { rub_per_cny?: number; source?: string };
    sku_pricing?: Array<{
      sku_id?: string;
      purchase_cost_cny?: number;
      selling_price_cny?: number;
      selling_price_rub?: number;
      profit_cny?: number;
      profit_rate?: number;
      profit_rate_markup?: number;
      estimated_profit_cny?: number;
      route_name?: string;
      shipping?: { route_name?: string; shipping_cost_cny?: number };
    }>;
  };
  package_check?: {
    available?: boolean;
    passed?: boolean;
    message?: string;
    product_weight_g?: number | null;
    package_weight_g?: number | null;
    product_dimensions_cm?: { length?: number; width?: number; height?: number } | null;
    package_dimensions_cm?: { length?: number; width?: number; height?: number } | null;
  };
  production_readiness?: {
    blocking?: boolean;
    state?: string;
    message?: string;
    errors?: string[];
    manual_image_confirmation_required?: boolean;
    terminal_publication?: boolean;
  };
  ui_state?: {
    schema_version?: string;
    kind?: string;
    state?: string;
    tone?: "ok" | "warning" | "danger" | "running" | "idle" | string;
    title?: string;
    message?: string;
    progress_label?: string;
    blocking?: boolean;
    primary_action?: {
      id?: string;
      label?: string;
      enabled?: boolean;
      reason?: string;
    };
    secondary_actions?: Array<{
      id?: string;
      label?: string;
      enabled?: boolean;
      reason?: string;
    }>;
  };
  pending_question?: {
    question_id?: string;
    question?: string;
    title?: string;
    message?: string;
    field?: string;
  };
  sku_image_binding_candidates?: Array<{
    id?: string;
    label?: string;
    path?: string;
    url?: string;
    display_source?: string;
    image_type?: string;
  }>;
  ozon?: {
    upload_status?: string;
    product_id?: string;
    offer_id?: string;
    task_id?: string;
    shop_name?: string;
    errors?: string[];
  };
  prelisting_assessment?: {
    overall_score?: number;
    advice?: string;
    profit_potential?: number;
    russia_fit?: number;
    image_sales_potential?: number;
    competition_risk?: number;
    return_risk?: number;
  };
  rich_content?: Record<string, unknown>;
  error?: {
    title?: string;
    message?: string;
    suggestion?: string;
  } | null;
  visual_preference?: {
    set_hint?: string;
    slot_hints?: Record<string, string>;
    updated_at?: string;
  };
};

export type OzonIssueSummary = {
  has_issues?: boolean;
  primary_bucket?: string;
  primary_label?: string;
  primary_action?: string;
  message?: string;
  counts?: Record<string, number>;
  error_count?: number;
  warning_count?: number;
  total?: number;
  samples?: Array<Record<string, unknown>>;
};

export type ProductsResponse = {
  items: ProductCard[];
  total: number;
  execution_plan?: {
    label?: string;
    summary?: string;
    image_slot_concurrency?: number;
  };
  queue_summary?: {
    active_product_count?: number;
    queued_product_count?: number;
    message?: string;
  };
};

export type ShopCard = {
  id: string;
  display_name: string;
  notes?: string;
  enabled?: boolean;
  connection_status?: string;
  client_id_configured?: boolean;
  api_key_configured?: boolean;
  credentials_display?: string;
  currency?: string;
  last_validated_at?: string | null;
  last_validation_error?: string | null;
  associated_product_count?: number;
  pending_task_count?: number;
};

export type ShopsResponse = {
  items: ShopCard[];
  default_shop?: string;
};

export type ShopPayload = {
  display_name: string;
  client_id?: string;
  api_key?: string;
  currency: string;
  notes?: string;
  enabled?: boolean;
};

export type ShopMutationResponse = {
  created?: boolean;
  saved?: boolean;
  deleted?: boolean;
  store_id?: string;
  item?: ShopCard;
  connection_status?: string;
  last_validation_error?: string | null;
  remote_ozon_unchanged?: boolean;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type StoreSelectionResponse = {
  saved: boolean;
  store_ids: string[];
  publications?: ProductDetail["publications"];
  summary?: ProductDetail["publication_summary"];
};

export type SuggestionActionResponse = {
  saved: boolean;
  suggestion_id: string;
  action: string;
};

export type QuestionAnswerResponse = {
  saved: boolean;
  product_id: string;
  next_action?: string;
};

export type DeletePreviewResponse = {
  product_id: string;
  title?: string;
  thumbnail_url?: string;
  sku_count?: number;
  status?: string;
  public_state?: string;
  current_step?: string;
  submitted_to_ozon?: boolean;
  associated_shops?: string[];
  remote_ids?: {
    task_ids?: string[];
    offer_ids?: string[];
    product_ids?: string[];
  };
  remote_warning_required?: boolean;
};

export type DeleteProductResponse = {
  status: string;
  product_id?: string;
  message?: string;
  remote_ozon_unchanged?: boolean;
};

export type ProductDraftPayload = {
  title_ru?: string;
  short_title?: string;
  description_ru?: string;
  bullets_ru?: string[];
  tags?: string[];
  attributes?: Record<string, unknown>;
  sku_overrides?: Record<string, Record<string, unknown>>;
  image_prompts?: Record<string, string>;
  selected_store_ids?: string[];
  notes?: string;
};

export type DraftSaveResponse = {
  saved: boolean;
  version: number;
  saved_at?: string;
  locked_fields?: string[];
  learning?: unknown;
};

export type VisualPreferenceResponse = {
  saved: boolean;
  preference: NonNullable<ProductDetail["visual_preference"]>;
  invalidated_steps?: string[];
};

export type BatchCard = {
  batch_id: string;
  status?: string;
  display_status?: string;
  product_count?: number;
  sku_count?: number;
  processing_count?: number;
  target_store_ids?: string[];
  auto_upload?: boolean;
  review_mode?: string;
  manual_upload_required?: boolean;
  inventory_submission_enabled?: boolean;
  success_count?: number;
  failed_count?: number;
  incomplete_count?: number;
  submitted_count?: number;
  pending_remote_count?: number;
  progress?: number;
  queue_position?: number;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  ready_product_ids?: string[];
  products?: Array<{
    product_id: string;
    selected_sku_count?: number;
    status?: string;
    current_step?: string;
    started_at?: string;
    completed_at?: string;
    warnings?: string[];
    errors?: string[];
  }>;
  result?: Record<string, unknown>;
  execution_plan?: {
    label?: string;
    summary?: string;
  };
};

export type BatchesResponse = {
  items: BatchCard[];
  running_pid?: number | null;
  queued_count?: number;
  execution_plan?: {
    label?: string;
    summary?: string;
  };
};

export type CreateBatchResponse = {
  status: string;
  batch_id?: string;
  product_count?: number;
  target_store_ids?: string[];
  auto_upload?: boolean;
  queue_position?: number;
  message?: string;
  existing_batch_ids?: string[];
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type OzonReferenceTask = {
  task_id: string;
  source_url: string;
  status: string;
  display_status?: string;
  target_store_ids?: string[];
  created_at?: string;
  updated_at?: string;
  mode?: string;
  inventory_submission_enabled?: boolean;
  message?: string;
  reference_title?: string;
  captured_image_count?: number;
  fitkun_image_count?: number;
  capture_artifact_path?: string;
  brief_artifact_path?: string;
  generation_artifact_path?: string;
  designer_input_artifact_path?: string;
  ai_design_request_artifact_path?: string;
  listing_draft_artifact_path?: string;
  created_product_id?: string;
  created_product_path?: string;
  missing_fields?: string[];
  manual_inputs?: Record<string, unknown>;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type OzonReferenceImportedImage = {
  url?: string;
  data_url?: string;
  content_type?: string;
  byte_size?: number;
  name?: string;
};

export type OzonReferenceManualInputs = {
  length_mm?: number | string;
  width_mm?: number | string;
  height_mm?: number | string;
  weight_g?: number | string;
  selling_price_cny?: number | string;
  ozon_category_selection?: {
    category_id: number | string;
    type_id: number | string;
    category_path?: string[] | string;
    category_name_zh?: string;
    category_path_zh?: string[] | string;
    selected_at?: string;
    rules_snapshot: CategoryRulesResponse;
  };
};

export type OzonReferenceTaskInput = OzonReferenceManualInputs & {
  url: string;
  fitkun_images?: OzonReferenceImportedImage[];
};

export type OzonReferenceTasksResponse = {
  items: OzonReferenceTask[];
  total: number;
  message?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type CreateOzonReferenceTasksResponse = {
  status: string;
  created_count: number;
  duplicate_count?: number;
  target_store_ids?: string[];
  items: OzonReferenceTask[];
  duplicates?: string[];
  message?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type ProcessOzonReferenceTasksResponse = {
  status: string;
  processed_count: number;
  failed_count: number;
  items: OzonReferenceTask[];
  message?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type ImportOzonReferenceImagesResponse = {
  status: string;
  task: OzonReferenceTask;
  imported_count: number;
  total_fitkun_image_count: number;
  message?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type UpdateOzonReferenceTaskResponse = {
  status: string;
  task: OzonReferenceTask;
  message?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type SearchVisibilityAction = {
  product_id: string;
  current_title?: string;
  offer_ids?: string[];
  sku?: string | number;
  image_url?: string;
  images?: Array<string | { url?: string; file_name?: string; src?: string }>;
  price?: string | number;
  currency?: string;
  stock?: string | number;
  created_at?: string;
  updated_at?: string;
  order_count?: string | number;
  category_name?: string;
  brand?: string;
  source_url?: string;
  measurements?: {
    weight_g?: string | number;
    length_mm?: string | number;
    width_mm?: string | number;
    height_mm?: string | number;
    unit?: string;
    source?: string;
  };
  product_attributes?: Array<{
    id?: string | number;
    name?: string;
    values?: string[];
  }>;
  risk_layer: "stable_seller" | "title_optimization_candidate" | "tag_only_candidate" | "insufficient_data" | string;
  allowed_changes?: string[];
  blocked_changes?: string[];
  title_locked?: boolean;
  title_terms?: string[];
  intro_terms?: string[];
  current_intro?: string;
  recommended_intro?: string;
  intro_supplement?: string;
  intro_update_available?: boolean;
  subject_tags?: string[];
  last_upload?: {
    status?: string;
    task_id?: string | number;
    remote_status?: string;
    import_status?: string;
    last_checked_at?: string;
    uploaded_changes?: string[];
    applied_subject_tags_count?: number;
    applied_subject_tags?: string[];
    new_subject_tags_count?: number;
    intro_update_status?: string;
    applied_at?: string;
    report_path?: string;
  };
  last_upload_status_check?: SearchVisibilityUploadStatusResponse;
  existing_subject_tags?: string[] | null;
  existing_subject_tag_count?: number | null;
  missing_subject_tag_count?: number | null;
  subject_tag_strategy?: "fill_missing" | "replace_low_search" | string;
  subject_tags_to_remove?: string[];
  subject_tag_replacement_count?: number;
  subject_tag_update_required?: boolean;
  subject_tag_suggestion_available?: boolean;
  data_source_status?: "search_source" | "trial_source" | "title_inference_only" | string;
  last_yandex_wordstat_import?: {
    status?: string;
    imported_at?: string;
    imported_count?: number;
    period_days?: number;
    report_path?: string;
  };
  last_seerfar_keyword_import?: {
    status?: string;
    imported_at?: string;
    imported_count?: number;
    period_days?: number;
    report_path?: string;
    source_label?: string;
  };
  reason_cn?: string;
  evidence?: {
    totals?: {
      impressions?: number;
      clicks?: number;
      orders?: number;
      revenue_rub?: number;
      query_count?: number;
    };
    reference_totals?: {
      yandex_wordstat_searches?: number;
      yandex_wordstat_query_count?: number;
      seerfar_keyword_mining_search_heat?: number;
      seerfar_keyword_mining_query_count?: number;
      seerfar_keyword_reverse_searches?: number;
      seerfar_keyword_reverse_query_count?: number;
      trial_reference_searches?: number;
      trial_reference_query_count?: number;
    };
    data_source_status?: "search_source" | "trial_source" | "title_inference_only" | string;
    top_queries?: Array<{
      query?: string;
      value_score?: number;
      metrics?: {
        impressions?: number;
        search_count?: number;
        clicks?: number;
        orders?: number;
        revenue_rub?: number;
      };
    }>;
    top_trial_terms?: Array<{
      query?: string;
      source?: string;
      source_label?: string;
      count?: number;
      value_score?: number;
      metrics?: {
        search_count?: number;
      };
    }>;
    top_yandex_wordstat?: Array<{
      query?: string;
      count?: number;
      period_days?: number;
      source?: string;
      value_score?: number;
      metrics?: {
        search_count?: number;
      };
    }>;
    top_seerfar_keyword_mining?: Array<{
      query?: string;
      count?: number;
      period_days?: number;
      source?: string;
      source_label?: string;
      value_score?: number;
      updated_frequency?: string;
      related_product_urls?: string[];
      metrics?: {
        search_count?: number;
        monthly_search_heat?: number;
        monthly_growth_percent?: number;
        relevance?: number;
        cart_add_count?: number;
        cart_conversion_percent?: number;
        title_density_percent?: number;
        average_price_rub?: number;
        competitor_count?: number;
        product_count?: number;
        competitor_seller_count?: number;
        ad_competitor_count?: number;
        product_visibility?: number;
        market_space?: number;
        conversion_concentration_percent?: number;
        return_cancel_rate_percent?: number;
      };
    }>;
    top_seerfar_keyword_reverse?: Array<{
      query?: string;
      count?: number;
      period_days?: number;
      source?: string;
      source_label?: string;
      value_score?: number;
      metrics?: {
        search_count?: number;
        seerfar_reverse_search_count?: number;
      };
    }>;
  };
};

export type SearchVisibilityPlan = {
  schema_version?: string;
  mode?: string;
  source?: string;
  shop_id?: string;
  period_days?: number;
  order_period_days?: number;
  order_date_from?: string;
  order_date_to?: string;
  recommended_schedule_days?: number;
  generated_at?: string;
  available?: boolean;
  notice?: string;
  summary?: {
    products?: number;
    stable_tag_only?: number;
    title_optimization_candidates?: number;
    tag_only_candidates?: number;
    insufficient_data?: number;
  };
  batches?: Array<{
    batch_id?: string;
    risk_layer?: string;
    product_count?: number;
    product_ids?: string[];
    allowed_changes?: string[];
  }>;
  actions?: SearchVisibilityAction[];
  last_upload?: SearchVisibilityAction["last_upload"];
  last_yandex_wordstat_import?: SearchVisibilityAction["last_yandex_wordstat_import"] & {
    product_id?: string;
  };
  last_seerfar_keyword_import?: SearchVisibilityAction["last_seerfar_keyword_import"] & {
    product_id?: string;
  };
  safety?: {
    dry_run_only?: boolean;
    write_api_calls?: number;
    inventory_api_calls?: number;
    stable_seller_title_locked?: boolean;
    requires_explicit_write_scope_before_ozon_update?: boolean;
  };
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type SearchVisibilityYandexImportResponse = SearchVisibilityPlan & {
  imported_count?: number;
  import_path?: string;
};

export type SearchVisibilitySeerfarQueueResponse = {
  status?: string;
  notice?: string;
  job?: {
    job_id?: string;
    product_id?: string;
    seed_keyword?: string;
    status?: string;
  };
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type SearchVisibilityApplyResponse = {
  status: string;
  shop_id: string;
  product_id: string;
  uploaded_changes?: string[];
  applied_subject_tags_count?: number;
  new_subject_tags_count?: number;
  title_update_status?: string;
  title_update_reason?: string;
  applied_at?: string;
  report_path?: string;
  notice?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type SearchVisibilityUploadStatusResponse = {
  status: "verified" | "needs_review" | string;
  shop_id?: string;
  product_id?: string;
  offer_id?: string;
  task_id?: string | number;
  import_status?: string;
  warnings?: Array<Record<string, unknown>>;
  errors?: Array<Record<string, unknown>>;
  has_subject_tags?: boolean;
  subject_tag_value_count?: number;
  subject_tag_sample?: string;
  subject_tag_values?: string[];
  has_intro?: boolean;
  intro_sample?: string;
  checked_at?: string;
  notice?: string;
  read_api_calls?: number;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type SearchVisibilityBatchApplyResponse = {
  status: string;
  shop_id: string;
  uploaded_changes?: string[];
  uploaded_product_count?: number;
  applied_subject_tags_count?: number;
  new_subject_tags_count?: number;
  uploaded_products?: Array<{
    product_id?: string;
    applied_subject_tags_count?: number;
    new_subject_tags_count?: number;
  }>;
  skipped?: Array<{ product_id?: string; reason?: string }>;
  applied_at?: string;
  report_path?: string;
  notice?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type BatchControlResponse = {
  status: string;
  pid?: number;
  batch_id?: string;
  message?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type BatchConfirmationProduct = {
  product_id: string;
  title_cn?: string;
  source_url?: string;
  category_path_zh?: string[];
  sku_count?: number;
  uncertain_count?: number;
  thumbnail_url?: string;
  skus?: Array<{
    sku_id: string;
    name?: string;
    option_text?: string;
    purchase_price_cny?: number | string | null;
  }>;
  fields?: {
    product_dimensions?: {
      value?: { length?: number; width?: number; height?: number };
      unit?: string;
      confidence?: number;
      source?: string;
      estimated?: boolean;
    };
    product_weight_g?: {
      value?: number;
      unit?: string;
      confidence?: number;
      source?: string;
      estimated?: boolean;
    };
    package_dimensions?: {
      value?: { length?: number; width?: number; height?: number };
      unit?: string;
      confidence?: number;
      source?: string;
      estimated?: boolean;
    };
    package_weight_g?: {
      value?: number;
      unit?: string;
      confidence?: number;
      source?: string;
      estimated?: boolean;
    };
    material?: {
      value?: string;
      confidence?: number;
      source?: string;
      estimated?: boolean;
      needs_input?: boolean;
    };
  };
  omitted_without_evidence?: string[];
};

export type TrafficPerformanceAction = {
  product_id: string;
  offer_ids?: string[];
  title?: string;
  traffic_layer: "recommendation_led" | "search_led" | "ad_spend_risk" | "click_no_order" | "exposure_no_click" | "insufficient_data" | string;
  title_locked?: boolean;
  allowed_changes?: string[];
  blocked_changes?: string[];
  focus?: string[];
  reason_cn?: string;
  evidence?: {
    search?: TrafficMetrics;
    recommendation?: TrafficMetrics;
    ads?: TrafficMetrics;
    totals?: TrafficMetrics;
    shares?: {
      search_orders?: number;
      recommendation_orders?: number;
      ads_orders?: number;
      search_revenue?: number;
      recommendation_revenue?: number;
      ads_revenue?: number;
    };
  };
};

export type TrafficMetrics = {
  impressions?: number;
  clicks?: number;
  orders?: number;
  revenue_rub?: number;
  spend_rub?: number;
  ctr?: number;
  conversion?: number;
  acos?: number;
};

export type TrafficPerformancePlan = {
  schema_version?: string;
  mode?: string;
  source?: string;
  shop_id?: string;
  period_days?: number;
  recommended_schedule_days?: number;
  generated_at?: string;
  available?: boolean;
  notice?: string;
  summary?: {
    products?: number;
    recommendation_led?: number;
    search_led?: number;
    ad_spend_risk?: number;
    click_no_order?: number;
    exposure_no_click?: number;
    insufficient_data?: number;
  };
  batches?: Array<{
    batch_id?: string;
    traffic_layer?: string;
    product_count?: number;
    product_ids?: string[];
    allowed_changes?: string[];
  }>;
  actions?: TrafficPerformanceAction[];
  safety?: {
    dry_run_only?: boolean;
    write_api_calls?: number;
    inventory_api_calls?: number;
    ad_budget_api_calls?: number;
    requires_explicit_write_scope_before_ozon_update?: boolean;
  };
};

export type BatchConfirmationResponse = {
  schema_version?: string;
  batch_id: string;
  status?: string;
  mode?: string;
  target_store_ids?: string[];
  product_count?: number;
  sku_count?: number;
  uncertain_count?: number;
  estimated_seconds?: number;
  products?: BatchConfirmationProduct[];
  created_at?: string;
  confirmed_at?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type LogEntry = {
  product_id?: string;
  at?: string;
  message: string;
  level?: string;
  step?: string;
  status?: string;
};

export type LogsResponse = {
  items: LogEntry[];
};

export type RunProductResponse = {
  status: string;
  batch_id?: string;
  pid?: number;
  queue_position?: number;
  message?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};

export type StoreRetryResponse = {
  status: string;
  batch_id?: string;
  product_id?: string;
  store_id?: string;
  store_ids?: string[];
  blocked?: Record<string, string>;
  queue_position?: number;
  message?: string;
  write_api_calls?: number;
  inventory_api_calls?: number;
};
