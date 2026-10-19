-- ARTEX standalone SQLite schema (移植自 db/schema.sql 的 PostgreSQL 双图 schema)
-- 类型约定: BIGSERIAL->INTEGER PK AUTOINCREMENT; TIMESTAMPTZ->TEXT(ISO8601 UTC);
--          JSONB/数组->TEXT(JSON); BOOLEAN->INTEGER(0/1); CIDR/DOUBLE 原样。
-- 幂等: 全部 IF NOT EXISTS。

-- =====================================================================
-- A. 资产层
-- =====================================================================
CREATE TABLE IF NOT EXISTS companies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    nkey       TEXT NOT NULL UNIQUE,
    logo       TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL CHECK (type IN ('root_domain','ip','subdomain','app','service','endpoint')),
    company_id      INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    company_source  TEXT NOT NULL DEFAULT 'explicit' CHECK (company_source IN ('explicit','scope')),
    task_ids        TEXT NOT NULL DEFAULT '[]',
    domain          TEXT,
    root_domain     TEXT,
    ip              TEXT,
    c_segment       TEXT,
    port            INTEGER,
    icp             TEXT,
    bound_domains   TEXT NOT NULL DEFAULT '[]',
    open_ports      TEXT NOT NULL DEFAULT '[]',
    record_type     TEXT,
    record_value    TEXT NOT NULL DEFAULT '[]',
    bundle_id       TEXT,
    app_name        TEXT,
    category        TEXT,
    app_description TEXT,
    app_icp         TEXT,
    url             TEXT,
    service_type    TEXT CHECK (service_type IN ('http','other')),
    service_name    TEXT,
    favicon_mmh3    TEXT,
    status_code     INTEGER,
    content_length  INTEGER,
    page_title      TEXT,
    technologies    TEXT NOT NULL DEFAULT '[]',
    auth            TEXT NOT NULL DEFAULT '[]',
    method          TEXT,
    params          TEXT NOT NULL DEFAULT '[]',
    extra           TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_av2_root_domain  ON assets(domain) WHERE type = 'root_domain';
CREATE UNIQUE INDEX IF NOT EXISTS uq_av2_ip           ON assets(ip)     WHERE type = 'ip';
CREATE UNIQUE INDEX IF NOT EXISTS uq_av2_subdomain    ON assets(domain, IFNULL(record_type,'')) WHERE type = 'subdomain';
CREATE UNIQUE INDEX IF NOT EXISTS uq_av2_app_bundle   ON assets(bundle_id) WHERE type = 'app' AND bundle_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_av2_app_name     ON assets(app_name)  WHERE type = 'app' AND bundle_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_av2_service_http ON assets(url) WHERE type = 'service' AND service_type = 'http';
CREATE UNIQUE INDEX IF NOT EXISTS uq_av2_endpoint     ON assets(url, IFNULL(method,'')) WHERE type = 'endpoint';
CREATE INDEX IF NOT EXISTS idx_av2_company      ON assets(company_id)       WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_av2_company_type ON assets(company_id, type) WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_av2_domain       ON assets(domain)      WHERE domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_av2_root_domain  ON assets(root_domain) WHERE root_domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_av2_ip           ON assets(ip)          WHERE ip IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_av2_last_seen    ON assets(last_seen DESC);

CREATE TABLE IF NOT EXISTS company_scope (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('domain','ip','cidr','icp','keyword')),
    domain     TEXT,
    net        TEXT,
    value      TEXT,
    raw        TEXT NOT NULL,
    reason     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sv2_domain ON company_scope(domain) WHERE kind = 'domain';
CREATE INDEX IF NOT EXISTS idx_sv2_company ON company_scope(company_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sv2_value ON company_scope(company_id, kind, value) WHERE kind IN ('icp','keyword');

-- =====================================================================
-- B. 推理探索层
-- =====================================================================
CREATE TABLE IF NOT EXISTS explorations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT,
    goal        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','achieved','failed')),
    round_no    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exploration_nodes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    exploration_id INTEGER NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    payload        TEXT NOT NULL DEFAULT '{}',
    priority       INTEGER NOT NULL DEFAULT 0,
    state          TEXT NOT NULL DEFAULT 'open',
    origin         TEXT,
    owner          TEXT,
    blocked_reason TEXT,
    delete_reason  TEXT,
    content_version  INTEGER NOT NULL DEFAULT 0,
    cold_since_round INTEGER,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    completed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_expnodes_part     ON exploration_nodes(exploration_id, kind);
CREATE INDEX IF NOT EXISTS idx_expnodes_frontier ON exploration_nodes(exploration_id, priority DESC) WHERE kind='intent' AND state='open';

CREATE TABLE IF NOT EXISTS exploration_edges (
    exploration_id INTEGER NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
    src_id         INTEGER NOT NULL REFERENCES exploration_nodes(id) ON DELETE CASCADE,
    dst_id         INTEGER NOT NULL REFERENCES exploration_nodes(id) ON DELETE CASCADE,
    rel            TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (exploration_id, src_id, rel, dst_id)
);
CREATE INDEX IF NOT EXISTS idx_expedges_src ON exploration_edges(src_id, rel);
CREATE INDEX IF NOT EXISTS idx_expedges_dst ON exploration_edges(dst_id, rel);

CREATE TABLE IF NOT EXISTS exploration_anchors (
    node_id   INTEGER NOT NULL REFERENCES exploration_nodes(id) ON DELETE CASCADE,
    asset_id  INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id, asset_id)
);
CREATE INDEX IF NOT EXISTS idx_anchor_asset ON exploration_anchors(asset_id);

CREATE TABLE IF NOT EXISTS task_constraints (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    exploration_id INTEGER NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('allow','deny')),
    text           TEXT NOT NULL,
    origin         TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_constraints_exp ON task_constraints(exploration_id);

CREATE TABLE IF NOT EXISTS activity (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    exploration_id     INTEGER NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
    node_id            INTEGER REFERENCES exploration_nodes(id) ON DELETE SET NULL,
    worker             TEXT,
    kind               TEXT,
    tool               TEXT,
    tool_use_id        TEXT,
    is_error           INTEGER NOT NULL DEFAULT 0,
    summary            TEXT,
    detail             TEXT,
    metadata           TEXT NOT NULL DEFAULT '{}',
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    cache_read_tokens  INTEGER,
    cache_write_tokens INTEGER,
    main_seg           INTEGER,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_node      ON activity(exploration_id, node_id, id);
CREATE INDEX IF NOT EXISTS idx_act_since     ON activity(exploration_id, id);
CREATE INDEX IF NOT EXISTS idx_act_worker    ON activity(exploration_id, worker, id);
CREATE INDEX IF NOT EXISTS idx_act_tool_call ON activity(exploration_id, tool_use_id, id) WHERE kind IN ('tool_use','tool_result');
CREATE INDEX IF NOT EXISTS idx_act_main_seg  ON activity(exploration_id, main_seg, id) WHERE worker='mainagent';

CREATE TABLE IF NOT EXISTS main_sessions (
    exploration_id INTEGER NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (exploration_id, seq)
);

-- =====================================================================
-- C. LLM profiles / settings
-- =====================================================================
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_profiles (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL UNIQUE,
    format                TEXT NOT NULL CHECK (format IN ('openai','anthropic','openai-responses')),
    base_url              TEXT,
    proxy                 TEXT,
    model                 TEXT NOT NULL,
    api_key               TEXT,
    api_key_hint          TEXT,
    rate_per_second       REAL NOT NULL DEFAULT 0,
    rate_per_minute       REAL NOT NULL DEFAULT 0,
    context_window_k      INTEGER NOT NULL DEFAULT 0,
    reasoning_effort      TEXT NOT NULL DEFAULT '',
    thinking_type         TEXT NOT NULL DEFAULT '',
    is_default            INTEGER NOT NULL DEFAULT 0,
    priority              INTEGER NOT NULL DEFAULT 0,
    pool_exclude          INTEGER NOT NULL DEFAULT 0,
    streaming             INTEGER NOT NULL DEFAULT 1,
    max_tokens            INTEGER NOT NULL DEFAULT 0,
    max_tokens_field      TEXT NOT NULL DEFAULT '',
    session_header_key    TEXT NOT NULL DEFAULT '',
    retry_connect_attempts    INTEGER NOT NULL DEFAULT 0,
    retry_connect_interval_ms INTEGER NOT NULL DEFAULT 0,
    retry_empty_attempts      INTEGER NOT NULL DEFAULT 0,
    retry_empty_interval_ms   INTEGER NOT NULL DEFAULT 0,
    retry_stream_attempts     INTEGER NOT NULL DEFAULT 0,
    retry_stream_interval_ms  INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_profile_health (
    profile_id INTEGER PRIMARY KEY REFERENCES llm_profiles(id) ON DELETE CASCADE,
    fails      INTEGER NOT NULL DEFAULT 0,
    trips      INTEGER NOT NULL DEFAULT 0,
    open_until TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    last_at    TEXT NOT NULL
);

-- =====================================================================
-- D. 任务层
-- =====================================================================
CREATE TABLE IF NOT EXISTS task_categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    nkey       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL DEFAULT '',
    category_id    INTEGER REFERENCES task_categories(id) ON DELETE SET NULL,
    description    TEXT NOT NULL,
    goal           TEXT NOT NULL,
    exploration_id INTEGER NOT NULL UNIQUE REFERENCES explorations(id) ON DELETE RESTRICT,
    status         TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created','running','paused','done','failed','timeout')),
    paused         INTEGER NOT NULL DEFAULT 0,
    queued         INTEGER NOT NULL DEFAULT 0,
    queued_at      TEXT,
    queue_mode     TEXT NOT NULL DEFAULT '',
    llm_profile_id INTEGER REFERENCES llm_profiles(id) ON DELETE SET NULL,
    active_llm_profile_id INTEGER REFERENCES llm_profiles(id) ON DELETE SET NULL,
    llm_chain_revision INTEGER NOT NULL DEFAULT 0,
    company_id     INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    parent_ref     TEXT,
    timeout_seconds INTEGER NOT NULL DEFAULT 0,
    plan_heartbeat_seconds INTEGER NOT NULL DEFAULT 300,
    coverage_enabled INTEGER NOT NULL DEFAULT 1,
    pinned_at      TEXT,
    first_run_at   TEXT,
    deadline_at    TEXT,
    archived_at    TEXT,
    deleted_at     TEXT,
    completed_at   TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_alive  ON tasks(created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)          WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_pinned ON tasks(pinned_at DESC) WHERE deleted_at IS NULL AND pinned_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_archived ON tasks(archived_at DESC) WHERE archived_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS task_archives (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id                    INTEGER NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    state                      TEXT NOT NULL DEFAULT 'archive_queued',
    phase                      TEXT NOT NULL DEFAULT 'queued',
    progress                   INTEGER NOT NULL DEFAULT 0,
    error                      TEXT NOT NULL DEFAULT '',
    warnings                   TEXT NOT NULL DEFAULT '[]',
    format_version             INTEGER NOT NULL DEFAULT 2,
    archive_path               TEXT NOT NULL DEFAULT '',
    sha256                     TEXT NOT NULL DEFAULT '',
    original_size              INTEGER NOT NULL DEFAULT 0,
    compressed_size            INTEGER NOT NULL DEFAULT 0,
    task_name                  TEXT NOT NULL DEFAULT '',
    task_description           TEXT NOT NULL DEFAULT '',
    task_goal                  TEXT NOT NULL DEFAULT '',
    original_status            TEXT NOT NULL DEFAULT '',
    category_id_snapshot       INTEGER,
    category_name_snapshot     TEXT NOT NULL DEFAULT '',
    source_task_ids            TEXT NOT NULL DEFAULT '[]',
    remaining_timeout_seconds  INTEGER NOT NULL DEFAULT 0,
    data_counts                TEXT NOT NULL DEFAULT '{}',
    aggregate_stats            TEXT NOT NULL DEFAULT '{}',
    archived_at                TEXT,
    requested_at               TEXT NOT NULL,
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_templates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    nkey           TEXT NOT NULL UNIQUE,
    description    TEXT NOT NULL,
    goal           TEXT NOT NULL,
    category_id    INTEGER REFERENCES task_categories(id) ON DELETE SET NULL,
    intercept_rules TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_relations (
    task_id        INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    source_task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (task_id, source_task_id)
);

CREATE TABLE IF NOT EXISTS task_asset_links (
    task_id        INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    asset_id       INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    source         TEXT NOT NULL DEFAULT 'system',
    source_summary TEXT NOT NULL DEFAULT '',
    source_node_id INTEGER REFERENCES exploration_nodes(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (task_id, asset_id)
);
CREATE INDEX IF NOT EXISTS idx_task_asset_links_asset ON task_asset_links(asset_id, task_id);

CREATE TABLE IF NOT EXISTS task_llm_profiles (
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    profile_id INTEGER NOT NULL REFERENCES llm_profiles(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL CHECK (position >= 0),
    status     TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','quota_exhausted')),
    last_error TEXT,
    exhausted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, profile_id),
    UNIQUE (task_id, position)
);

CREATE TABLE IF NOT EXISTS task_scope (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('company','root_domain','subdomain','ip','cidr','icp','keyword')),
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    domain     TEXT,
    net        TEXT,
    value      TEXT,
    source     TEXT NOT NULL DEFAULT 'auto' CHECK (source IN ('auto','agent','manual')),
    reason     TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_task_scope_v2 ON task_scope(task_id, kind, IFNULL(domain,''), IFNULL(net,''), IFNULL(company_id,0), IFNULL(value,''));

-- =====================================================================
-- E. Agents
-- =====================================================================
CREATE TABLE IF NOT EXISTS agents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    key               TEXT NOT NULL UNIQUE CHECK (key GLOB '[a-z][a-z0-9_]*'),
    name              TEXT NOT NULL,
    description       TEXT,
    role              TEXT NOT NULL CHECK (role IN ('goals','main','planner','worker','assistant')),
    builtin           INTEGER NOT NULL DEFAULT 1,
    enabled           INTEGER NOT NULL DEFAULT 1,
    llm_profile_id    INTEGER REFERENCES llm_profiles(id) ON DELETE SET NULL,
    current_prompt_id INTEGER,
    max_turns         INTEGER NOT NULL DEFAULT 0,
    run_seconds       INTEGER NOT NULL DEFAULT 1200,
    web_search        INTEGER NOT NULL DEFAULT 0,
    interactive_shell INTEGER NOT NULL DEFAULT 0,
    wrapup_prompt     TEXT NOT NULL DEFAULT '',
    wrapup_max_turns  INTEGER NOT NULL DEFAULT 0,
    task_timeout_wrapup_prompt    TEXT NOT NULL DEFAULT '',
    task_timeout_wrapup_max_turns INTEGER NOT NULL DEFAULT 0,
    trigger_run_mode   TEXT NOT NULL DEFAULT 'serial' CHECK (trigger_run_mode IN ('serial','parallel')),
    trigger_merge_mode TEXT NOT NULL DEFAULT 'all'   CHECK (trigger_merge_mode IN ('by_task','all','none')),
    trigger_max_parallel INTEGER NOT NULL DEFAULT 5,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_prompts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version       INTEGER NOT NULL,
    template_text TEXT NOT NULL,
    note          TEXT,
    updated_by    TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (agent_id, version)
);

CREATE TABLE IF NOT EXISTS agent_prompt_vars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    var_name    TEXT NOT NULL,
    description TEXT,
    example     TEXT,
    source      TEXT NOT NULL CHECK (source IN ('exploration','runtime','distilled')),
    UNIQUE (agent_id, var_name)
);

-- =====================================================================
-- F. MCP
-- =====================================================================
CREATE TABLE IF NOT EXISTS mcp_servers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    transport  TEXT NOT NULL CHECK (transport IN ('stdio','http','sse')),
    command    TEXT,
    args       TEXT NOT NULL DEFAULT '[]',
    env        TEXT NOT NULL DEFAULT '{}',
    url        TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    insecure   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO mcp_servers (name, transport, url, env, enabled, created_at, updated_at)
VALUES ('ScopeSentry', 'http', NULL, '{"X-API-Key":""}', 0, '2000-01-01T00:00:00', '2000-01-01T00:00:00');

CREATE TABLE IF NOT EXISTS mcp_tools_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id     INTEGER NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    tool_name     TEXT NOT NULL,
    description   TEXT,
    schema        TEXT,
    discovered_at TEXT NOT NULL,
    UNIQUE (server_id, tool_name)
);

CREATE TABLE IF NOT EXISTS agent_visibility (
    agent_id      INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    resource_kind TEXT   NOT NULL CHECK (resource_kind IN ('mcp')),
    resource_id   INTEGER NOT NULL,
    mcp_tool_name TEXT   NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (agent_id, resource_kind, resource_id, mcp_tool_name)
);

CREATE TABLE IF NOT EXISTS agent_skill_visibility (
    agent_id   INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    skill_name TEXT   NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, skill_name)
);

CREATE TABLE IF NOT EXISTS skill_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    skill          TEXT NOT NULL,
    agent_key      TEXT,
    task_id        INTEGER,
    exploration_id INTEGER,
    intent_id      INTEGER,
    session_id     TEXT,
    args_len       INTEGER NOT NULL DEFAULT 0,
    found          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tool_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    tool_key       TEXT NOT NULL,
    agent_key      TEXT,
    task_id        INTEGER,
    exploration_id INTEGER,
    intent_id      INTEGER,
    session_id     TEXT
);

CREATE TABLE IF NOT EXISTS tools (
    key         TEXT PRIMARY KEY,
    system      INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    schema      TEXT NOT NULL DEFAULT '{}',
    agents      TEXT NOT NULL DEFAULT '[]',
    enabled     INTEGER NOT NULL DEFAULT 1,
    kind        TEXT NOT NULL DEFAULT 'builtin',
    exec        TEXT NOT NULL DEFAULT '{}',
    deferred    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);

-- =====================================================================
-- I. 会话
-- =====================================================================
CREATE TABLE IF NOT EXISTS conversations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key      TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    llm_profile_id INTEGER REFERENCES llm_profiles(id) ON DELETE SET NULL,
    pinned_at      TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_activities (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id    INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    worker             TEXT,
    kind               TEXT,
    tool               TEXT,
    tool_use_id        TEXT,
    is_error           INTEGER NOT NULL DEFAULT 0,
    summary            TEXT,
    detail             TEXT,
    metadata           TEXT NOT NULL DEFAULT '{}',
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    cache_read_tokens  INTEGER,
    cache_write_tokens INTEGER,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_act ON conversation_activities(conversation_id, id);

CREATE TABLE IF NOT EXISTS agent_triggers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key           TEXT NOT NULL,
    enabled             INTEGER NOT NULL DEFAULT 1,
    interval_sec        INTEGER NOT NULL DEFAULT 0,
    on_finding          INTEGER NOT NULL DEFAULT 0,
    on_goal_met         INTEGER NOT NULL DEFAULT 0,
    on_task_timeout     INTEGER NOT NULL DEFAULT 0,
    on_tool_call        INTEGER NOT NULL DEFAULT 0,
    on_task_create      INTEGER NOT NULL DEFAULT 0,
    interval_message    TEXT NOT NULL DEFAULT '',
    finding_message     TEXT NOT NULL DEFAULT '',
    goal_message        TEXT NOT NULL DEFAULT '',
    task_timeout_message TEXT NOT NULL DEFAULT '',
    tool_call_message   TEXT NOT NULL DEFAULT '',
    task_create_message TEXT NOT NULL DEFAULT '',
    tool_names          TEXT NOT NULL DEFAULT '',
    last_fire           TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduler_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- =====================================================================
-- K. 拦截规则
-- =====================================================================
CREATE TABLE IF NOT EXISTS intercept_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    priority        INTEGER NOT NULL DEFAULT 0,
    match_target    TEXT NOT NULL CHECK (match_target IN ('tool_name','tool_input')),
    match_type      TEXT NOT NULL CHECK (match_type IN ('string','regex')),
    pattern         TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('allow','deny','ask')),
    message         TEXT NOT NULL DEFAULT '',
    timeout_enabled INTEGER NOT NULL DEFAULT 1,
    timeout_seconds INTEGER NOT NULL DEFAULT 60,
    timeout_action  TEXT NOT NULL DEFAULT 'deny',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intercept_pending (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         INTEGER REFERENCES intercept_rules(id) ON DELETE SET NULL,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
    task_id         TEXT,
    agent_name      TEXT NOT NULL DEFAULT '',
    tool_name       TEXT NOT NULL,
    tool_input      TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','allowed','denied','timeout')),
    reason          TEXT NOT NULL DEFAULT '',
    decision_source TEXT NOT NULL DEFAULT '',
    audit           TEXT,
    decided_at      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intercept_pending_status ON intercept_pending(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intercept_pending_task   ON intercept_pending(task_id, created_at DESC);

-- =====================================================================
-- L. 漏洞发现
-- =====================================================================
CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    node_id     INTEGER REFERENCES exploration_nodes(id) ON DELETE SET NULL,
    vulnclass   TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    severity    TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '',
    worker      TEXT NOT NULL DEFAULT '',
    asset_ids   TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'pending',
    report      TEXT NOT NULL DEFAULT '',
    evidence_version      INTEGER NOT NULL DEFAULT 0,
    report_evidence_version INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_task   ON findings(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_findings_time   ON findings(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status, created_at DESC);

CREATE TABLE IF NOT EXISTS finding_retests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id      INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    conversation_id INTEGER UNIQUE REFERENCES conversations(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','stopped')),
    verdict         TEXT NOT NULL DEFAULT '' CHECK (verdict IN ('','reproduced','fixed','inconclusive')),
    notes           TEXT NOT NULL DEFAULT '',
    snapshot        TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    evidence        TEXT NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_finding_retests_history ON finding_retests(finding_id, id DESC);

CREATE TABLE IF NOT EXISTS traffic_evidence_snapshots (
    id TEXT PRIMARY KEY,
    source_traffic_id TEXT NOT NULL,
    captured_at INTEGER NOT NULL,
    url TEXT NOT NULL,
    method TEXT NOT NULL,
    status INTEGER NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    req_head TEXT NOT NULL,
    resp_head TEXT NOT NULL,
    req_hash TEXT NOT NULL,
    resp_hash TEXT NOT NULL,
    req_len INTEGER NOT NULL,
    resp_len INTEGER NOT NULL,
    unreferenced_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding_traffic_bindings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id  INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL REFERENCES traffic_evidence_snapshots(id),
    role        TEXT NOT NULL DEFAULT 'supporting',
    note        TEXT NOT NULL DEFAULT '',
    position    INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(finding_id, snapshot_id)
);

-- =====================================================================
-- M. 日志
-- =====================================================================
CREATE TABLE IF NOT EXISTS server_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'info',
    tag        TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS side_question_sessions (
    session_key    TEXT PRIMARY KEY,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
    task_id        INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    exploration_id INTEGER REFERENCES explorations(id) ON DELETE CASCADE,
    intent_id      INTEGER REFERENCES exploration_nodes(id) ON DELETE CASCADE,
    run_id         INTEGER NOT NULL,
    version        INTEGER NOT NULL,
    snapshot       TEXT NOT NULL,
    generation     INTEGER NOT NULL DEFAULT 0,
    memory         TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS side_question_requests (
    id           TEXT PRIMARY KEY,
    ordinal      INTEGER UNIQUE,
    session_key  TEXT NOT NULL REFERENCES side_question_sessions(session_key) ON DELETE CASCADE,
    generation   INTEGER NOT NULL,
    client_id    TEXT NOT NULL,
    question     TEXT NOT NULL,
    answer       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
    error        TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL,
    snapshot_at  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    sequence     INTEGER NOT NULL DEFAULT 0,
    usage        TEXT NOT NULL DEFAULT '{}',
    context_info TEXT NOT NULL DEFAULT '{}',
    UNIQUE(session_key,generation,client_id)
);

-- =====================================================================
-- 资产拦截规则 / 任务级拦截规则
-- =====================================================================
CREATE TABLE IF NOT EXISTS asset_intercept_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    enabled    INTEGER NOT NULL DEFAULT 1,
    kind       TEXT NOT NULL CHECK (kind IN ('exact_domain','exact_ip','exact_url','fuzzy_domain','fuzzy_ip','fuzzy_url','cidr')),
    pattern    TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    builtin    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_intercept_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    enabled    INTEGER NOT NULL DEFAULT 1,
    action     TEXT NOT NULL DEFAULT 'block' CHECK (action IN ('block','allow')),
    kind       TEXT NOT NULL CHECK (kind IN ('exact_domain','exact_ip','exact_url','fuzzy_domain','fuzzy_ip','fuzzy_url','cidr')),
    pattern    TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- =====================================================================
-- 漏洞 IM 推送
-- =====================================================================
CREATE TABLE IF NOT EXISTS notification_channels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    config      TEXT NOT NULL DEFAULT '{}',
    mode        TEXT NOT NULL DEFAULT 'realtime',
    filter      TEXT NOT NULL DEFAULT '{}',
    rate_per_min INTEGER NOT NULL DEFAULT 20,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    finding_id INTEGER NOT NULL,
    snapshot   TEXT NOT NULL,
    fanned_out INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       INTEGER NOT NULL REFERENCES notification_events(id) ON DELETE CASCADE,
    channel_id     INTEGER NOT NULL REFERENCES notification_channels(id) ON DELETE CASCADE,
    state          TEXT NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error     TEXT NOT NULL DEFAULT '',
    batch_id       INTEGER,
    created_at     TEXT NOT NULL,
    sent_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_due ON notification_deliveries(next_attempt_at) WHERE state='pending';
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_history ON notification_deliveries(id DESC);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_channel ON notification_deliveries(channel_id, id DESC);

-- =====================================================================
-- LLM 调用记录（llm-records 页 + token 汇总）
-- =====================================================================
CREATE TABLE IF NOT EXISTS llm_usage (
    id                    TEXT PRIMARY KEY,
    model                 TEXT NOT NULL DEFAULT '',
    session_id            TEXT NOT NULL DEFAULT '',
    task_id               INTEGER,
    exploration_id        INTEGER,
    prompt                 TEXT NOT NULL DEFAULT '',
    response               TEXT NOT NULL DEFAULT '',
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens    INTEGER NOT NULL DEFAULT 0,
    started_at            TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_task ON llm_usage(task_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(model, created_at DESC);

-- =====================================================================
-- 流量录制
-- =====================================================================
CREATE TABLE IF NOT EXISTS traffic_exchanges (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    host         TEXT NOT NULL,
    method       TEXT NOT NULL,
    path         TEXT NOT NULL,
    scheme       TEXT NOT NULL,
    status       INTEGER,
    resp_len     INTEGER,
    req_headers  TEXT NOT NULL DEFAULT '{}',
    resp_headers TEXT NOT NULL DEFAULT '{}',
    req_body     TEXT NOT NULL DEFAULT '',
    resp_body    TEXT NOT NULL DEFAULT '',
    req_body_b64 INTEGER NOT NULL DEFAULT 0,
    resp_body_b64 INTEGER NOT NULL DEFAULT 0,
    mime         TEXT NOT NULL DEFAULT '',
    task_ids     TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traffic_host   ON traffic_exchanges(host);
CREATE INDEX IF NOT EXISTS idx_traffic_ts     ON traffic_exchanges(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traffic_status ON traffic_exchanges(status);
