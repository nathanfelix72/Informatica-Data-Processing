"""
Report storage and retrieval functions for Informatica usage data.

Manages SQLite database for storing historical runs, summaries, and enabling
trend analysis and anomaly detection across time.
"""

import sqlite3
import pandas as pd
import json
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import logging
import calculations
from processing import _to_datetime_mixed

# Prefer pytz if available, otherwise fall back to zoneinfo (Python 3.9+)
try:
    import pytz
    _HAS_PYTZ = True
except Exception:
    _HAS_PYTZ = False
    try:
        from zoneinfo import ZoneInfo
    except Exception:
        ZoneInfo = None


DB_PATH = Path(__file__).parent / "informatica_reports.db"


def init_database():
    """Initialize the SQLite database with required tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Table: Run metadata
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_name TEXT NOT NULL,
            run_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            source_files TEXT NOT NULL,
            ipu_conversion_factor REAL,
            cost_per_ipu_month REAL,
            total_rows INTEGER,
            total_ipus REAL,
            total_cost REAL,
            unique_task_runs INTEGER,
            row_count INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Table: Organization summaries per run
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS org_summaries (
            summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            org_name TEXT NOT NULL,
            task_count INTEGER,
            total_ipus REAL,
            total_cost REAL,
            unique_tasks INTEGER,
            avg_ipus_per_task REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
    ''')
    
    # Table: Environment summaries per run
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS env_summaries (
            summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            environment TEXT NOT NULL,
            task_count INTEGER,
            total_ipus REAL,
            total_cost REAL,
            unique_tasks INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
    ''')
    
    # Table: Project summaries per run
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS project_summaries (
            summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            project_name TEXT NOT NULL,
            task_count INTEGER,
            total_ipus REAL,
            total_cost REAL,
            unique_tasks INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
    ''')
    
    # Table: Daily statistics per run
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_stats (
            stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            stat_date DATE NOT NULL,
            task_count INTEGER,
            total_ipus REAL,
            total_cost REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
    ''')
    
    # Table: Task Type summaries per run
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_type_summaries (
            summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            task_type TEXT NOT NULL,
            task_count INTEGER,
            total_ipus REAL,
            total_cost REAL,
            unique_tasks INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
    ''')
    
    # Table: Status summaries per run
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS status_summaries (
            summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            status TEXT,
            task_count INTEGER,
            total_ipus REAL,
            total_cost REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
    ''')
    
    # Table: Actual task records for time-series analysis
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            task_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            task_name TEXT,
            task_object_name TEXT,
            task_type TEXT,
            task_run_id TEXT,
            row_hash TEXT,
            agent_name TEXT,
            project_name TEXT,
            folder_name TEXT,
            org TEXT,
            environment TEXT,
            status TEXT,
            log_type TEXT,
            start_time DATETIME,
            end_time DATETIME,
            ipus REAL,
            cost REAL,
            metered_value REAL,
            cores_used REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Audit trail for history edits and imports
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            start_date DATE,
            end_date DATE,
            affected_rows INTEGER,
            remaining_rows INTEGER,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Organization catalog (upload picklist). task.org stays denormalized text.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            informatica_id TEXT,
            parent_name TEXT,
            filename_tokens TEXT,
            sort_order INTEGER DEFAULT 100,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_organizations_name ON organizations(name)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_organizations_sort ON organizations(sort_order)'
    )

    # Migrate older DBs before creating indexes that depend on newer columns.
    # CREATE TABLE IF NOT EXISTS does not add columns to an existing table.
    cursor.execute("PRAGMA table_info(tasks)")
    cols = [r[1] for r in cursor.fetchall()]
    for column, ddl in [
        ('row_hash', 'ALTER TABLE tasks ADD COLUMN row_hash TEXT'),
        ('agent_name', 'ALTER TABLE tasks ADD COLUMN agent_name TEXT'),
        ('log_type', 'ALTER TABLE tasks ADD COLUMN log_type TEXT'),
        ('task_object_name', 'ALTER TABLE tasks ADD COLUMN task_object_name TEXT'),
    ]:
        if column not in cols:
            try:
                cursor.execute(ddl)
            except Exception:
                pass

    # Create indices for efficient queries (after migrations so columns exist)
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_start_time ON tasks(start_time)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_end_time ON tasks(end_time)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_org ON tasks(org)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_name ON tasks(agent_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_project ON tasks(project_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_environment ON tasks(environment)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_task_type ON tasks(task_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_task_object_name ON tasks(task_object_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_log_type ON tasks(log_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_history_events_created_at ON history_events(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_history_events_action ON history_events(action)')

    # Ensure unique index on row_hash to avoid inserting identical rows
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_row_hash ON tasks(row_hash)')

    # Mass Ingestion rows are tracked as distinct orgs (e.g. "BYU-Dev Mass Ingestion")
    # so they never combine with Task Usage for the same base name.
    _migrate_mass_ingestion_orgs(cursor, conn)
    _migrate_mass_ingestion_ipu_factor(cursor)
    _seed_organizations(cursor)

    conn.commit()
    conn.close()


def _seed_organizations(cursor):
    """Insert default orgs and any base names already present in tasks.

    Never renames or updates existing tasks.org values — catalog only.
    """
    from mappings import (
        DEFAULT_BASE_ORGS,
        DEFAULT_ORG_ID_MAPPING,
        DEFAULT_PARENT_ORG_CHILDREN,
        DEFAULT_FILENAME_TOKENS,
        base_org_name,
    )

    child_to_parent = {
        child: parent
        for parent, children in DEFAULT_PARENT_ORG_CHILDREN.items()
        for child in children
    }
    # Parents are also selectable orgs.
    for parent in DEFAULT_PARENT_ORG_CHILDREN:
        child_to_parent.setdefault(parent, parent)

    id_by_name = {name: oid for oid, name in DEFAULT_ORG_ID_MAPPING.items()}
    tokens_by_name = {
        name: ",".join(tokens) for name, tokens in DEFAULT_FILENAME_TOKENS.items()
    }

    cursor.execute("SELECT name FROM organizations")
    existing = {row[0] for row in cursor.fetchall()}

    # Discover base org names already used in task history (preserve spelling).
    discovered = []
    try:
        cursor.execute(
            """
            SELECT DISTINCT org FROM tasks
            WHERE org IS NOT NULL AND TRIM(org) != ''
            """
        )
        for (org,) in cursor.fetchall():
            base = base_org_name(org)
            if base and base != "Unknown":
                discovered.append(base)
    except Exception:
        pass

    to_seed = []
    seen = set(existing)
    for idx, name in enumerate(DEFAULT_BASE_ORGS):
        if name not in seen:
            to_seed.append((name, idx * 10, True))
            seen.add(name)
    for name in discovered:
        if name not in seen:
            to_seed.append((name, 1000 + len(to_seed), False))
            seen.add(name)

    for name, sort_order, is_default in to_seed:
        parent = child_to_parent.get(name)
        # Self-parent only for known billing parents; leave others NULL.
        if parent == name and name not in DEFAULT_PARENT_ORG_CHILDREN:
            parent = None
        informatica_id = id_by_name.get(name)
        tokens = tokens_by_name.get(name)
        cursor.execute(
            """
            INSERT OR IGNORE INTO organizations
                (name, informatica_id, parent_name, filename_tokens, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, informatica_id, parent, tokens, sort_order),
        )


def list_base_organizations():
    """Return base organization names from the catalog (sorted)."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT name FROM organizations
            ORDER BY sort_order ASC, name ASC
            """
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_all_org_options():
    """Base orgs plus Mass Ingestion counterparts for upload / filters."""
    from mappings import mass_ingestion_org_name

    options = []
    for org in list_base_organizations():
        options.append(org)
        options.append(mass_ingestion_org_name(org))
    return options


def get_org_parent_map():
    """Map base org name → billing parent (CES-Prod / CES-Sandbox / self / Other).

    Built from organizations.parent_name; falls back to seed defaults when empty.
    """
    from mappings import DEFAULT_PARENT_ORG_CHILDREN

    init_database()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT name, parent_name FROM organizations"
        ).fetchall()
    finally:
        conn.close()

    child_to_parent = {}
    parents = set()
    for name, parent_name in rows:
        if parent_name:
            child_to_parent[name] = parent_name
            if parent_name == name:
                parents.add(name)
            else:
                parents.add(parent_name)

    if not child_to_parent:
        for parent, children in DEFAULT_PARENT_ORG_CHILDREN.items():
            parents.add(parent)
            child_to_parent[parent] = parent
            for child in children:
                child_to_parent[child] = parent

    return child_to_parent, parents


def get_filename_org_patterns():
    """Return (token, base_org) pairs for filename inference, longest tokens first."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT name, filename_tokens FROM organizations
            WHERE filename_tokens IS NOT NULL AND TRIM(filename_tokens) != ''
            """
        ).fetchall()
    finally:
        conn.close()

    patterns = []
    for name, tokens in rows:
        for token in str(tokens).split(","):
            token = token.strip().lower()
            if token:
                patterns.append((token, name))

    if not patterns:
        from mappings import DEFAULT_FILENAME_TOKENS
        for name, tokens in DEFAULT_FILENAME_TOKENS.items():
            for token in tokens:
                patterns.append((token, name))

    patterns.sort(key=lambda item: len(item[0]), reverse=True)
    return patterns


def ensure_organization(name, parent_name=None, informatica_id=None, filename_tokens=None):
    """Insert a base org into the catalog if missing. Returns the stored base name.

    Does not modify tasks rows. Mass Ingestion suffixes are stripped for catalog storage.
    """
    from mappings import base_org_name

    init_database()
    base = base_org_name(name)
    if not base or base == "Unknown":
        return base

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = conn.execute(
            "SELECT name FROM organizations WHERE name = ?", (base,)
        ).fetchone()
        if existing:
            return base

        max_sort = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM organizations"
        ).fetchone()[0]
        tokens = None
        if filename_tokens:
            if isinstance(filename_tokens, (list, tuple)):
                tokens = ",".join(str(t).strip().lower() for t in filename_tokens if str(t).strip())
            else:
                tokens = str(filename_tokens).strip().lower() or None

        conn.execute(
            """
            INSERT INTO organizations
                (name, informatica_id, parent_name, filename_tokens, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (base, informatica_id, parent_name, tokens, int(max_sort) + 10),
        )
        conn.commit()
        return base
    finally:
        conn.close()


def get_org_name(org_id):
    """Map an Informatica Org ID to its catalog name (fallback: id as string)."""
    org_id_str = str(org_id).strip() if org_id is not None else ""
    if not org_id_str:
        return "Unknown"

    init_database()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT name FROM organizations
            WHERE informatica_id = ?
            LIMIT 1
            """,
            (org_id_str,),
        ).fetchone()
        if row:
            return row[0]
    finally:
        conn.close()

    from mappings import DEFAULT_ORG_ID_MAPPING
    return DEFAULT_ORG_ID_MAPPING.get(org_id_str, org_id_str)


def _migrate_mass_ingestion_orgs(cursor, conn):
    """Rename Mass Ingestion orgs and recompute row hashes for dedupe consistency."""
    from mappings import mass_ingestion_org_name, MASS_INGESTION_ORG_SUFFIX

    cursor.execute(
        """
        SELECT COUNT(*) FROM tasks
        WHERE log_type = 'Mass Ingestion'
          AND org IS NOT NULL
          AND TRIM(org) != ''
          AND org NOT LIKE ?
        """,
        (f'%{MASS_INGESTION_ORG_SUFFIX}',),
    )
    pending = cursor.fetchone()[0]
    if not pending:
        return

    rows = pd.read_sql_query(
        """
        SELECT task_record_id, task_id, task_run_id, task_name, task_type,
               project_name, folder_name, org, environment, agent_name,
               log_type, start_time, end_time, metered_value
        FROM tasks
        WHERE log_type = 'Mass Ingestion'
          AND org IS NOT NULL
          AND TRIM(org) != ''
          AND org NOT LIKE ?
        """,
        conn,
        params=(f'%{MASS_INGESTION_ORG_SUFFIX}',),
    )
    if rows.empty:
        return

    rows['org'] = rows['org'].map(mass_ingestion_org_name)
    # Match save_run hashing: numeric metered values rounded, nulls -> 0
    rows['metered_value'] = pd.to_numeric(rows['metered_value'], errors='coerce').round(6).fillna(0)
    for dt_col in ['start_time', 'end_time']:
        rows[dt_col] = rows[dt_col].fillna('').astype(str)

    # Mirror the display-column order used by save_run
    display_order = [
        'Task ID', 'Task Run ID', 'Task Name', 'Task Type', 'Project Name',
        'Folder Name', 'Org', 'Environment', 'Agent Name', 'Log Type',
        'Start Time', 'End Time', 'Metered Value',
    ]
    rename_map = {
        'task_id': 'Task ID',
        'task_run_id': 'Task Run ID',
        'task_name': 'Task Name',
        'task_type': 'Task Type',
        'project_name': 'Project Name',
        'folder_name': 'Folder Name',
        'org': 'Org',
        'environment': 'Environment',
        'agent_name': 'Agent Name',
        'log_type': 'Log Type',
        'start_time': 'Start Time',
        'end_time': 'End Time',
        'metered_value': 'Metered Value',
    }
    hash_frame = rows.rename(columns=rename_map)[display_order]
    new_hashes = _compute_row_hashes(hash_frame, display_order)

    updates = list(zip(
        rows['org'].tolist(),
        new_hashes,
        rows['task_record_id'].tolist(),
    ))
    cursor.executemany(
        'UPDATE tasks SET org = ?, row_hash = ? WHERE task_record_id = ?',
        updates,
    )


def _migrate_mass_ingestion_ipu_factor(cursor):
    """Recalculate Mass Ingestion IPUs/costs with the 0.1 conversion factor."""
    mi_factor = float(calculations.MASS_INGESTION_IPU_CONVERSION_FACTOR)
    cost_per_ipu = float(calculations.COST_PER_IPU_MONTH)

    # Skip when already on the MI factor (within rounding tolerance).
    cursor.execute(
        """
        SELECT COUNT(*) FROM tasks
        WHERE log_type = 'Mass Ingestion'
          AND metered_value IS NOT NULL
          AND ABS(
                COALESCE(ipus, 0)
                - ROUND(COALESCE(metered_value, 0) * ?, 8)
              ) > 0.0000001
        """,
        (mi_factor,),
    )
    pending = cursor.fetchone()[0]
    if not pending:
        return

    cursor.execute(
        """
        UPDATE tasks
        SET ipus = ROUND(COALESCE(metered_value, 0) * ?, 8),
            cost = ROUND(COALESCE(metered_value, 0) * ? * ?, 6)
        WHERE log_type = 'Mass Ingestion'
        """,
        (mi_factor, mi_factor, cost_per_ipu),
    )


def _ipu_fallback_sql() -> tuple[str, list]:
    """SQL expression + params for metered→IPU using log-type-aware factors."""
    mi_factor = float(calculations.MASS_INGESTION_IPU_CONVERSION_FACTOR)
    tu_factor = float(calculations.IPU_CONVERSION_FACTOR)
    expr = (
        "COALESCE(metered_value, 0) * "
        "CASE WHEN log_type = 'Mass Ingestion' THEN ? ELSE ? END"
    )
    return expr, [mi_factor, tu_factor]


def _effective_ipu_sql() -> tuple[str, list]:
    """COALESCE(stored ipus, metered * log-type factor)."""
    fallback_expr, fallback_params = _ipu_fallback_sql()
    return f"COALESCE(ipus, {fallback_expr})", fallback_params


def _effective_cost_sql() -> tuple[str, list]:
    """COALESCE(stored cost, effective_ipus * cost_per_ipu)."""
    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_per_ipu = float(calculations.COST_PER_IPU_MONTH)
    return f"COALESCE(cost, ({ipu_expr}) * ?)", ipu_params + [cost_per_ipu]


def _append_log_type_filter(query: str, params: list, log_type: str | None) -> tuple[str, list]:
    """Append SQL for log_type. Legacy NULL/blank rows count as Task Usage."""
    if not log_type:
        return query, params

    if log_type == 'Task Usage':
        query += " AND (log_type = ? OR log_type IS NULL OR TRIM(COALESCE(log_type, '')) = '')"
        params.append(log_type)
    else:
        query += ' AND log_type = ?'
        params.append(log_type)
    return query, params


def record_history_event(action: str, start_date=None, end_date=None, affected_rows: int | None = None,
                         remaining_rows: int | None = None, note: str | None = None) -> None:
    """Persist an audit event for an import or deletion."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO history_events (action, start_date, end_date, affected_rows, remaining_rows, note)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            action,
            None if start_date is None else str(start_date),
            None if end_date is None else str(end_date),
            affected_rows,
            remaining_rows,
            note,
        )
    )
    conn.commit()
    conn.close()


def get_history_events(action: str = None, limit: int | None = None) -> pd.DataFrame:
    """Return the audit trail for historical imports and deletes."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    query = 'SELECT event_id, action, start_date, end_date, affected_rows, remaining_rows, note, created_at FROM history_events'
    params = []
    if action:
        query += ' WHERE action = ?'
        params.append(action)
    query += ' ORDER BY created_at DESC, event_id DESC'
    if limit is not None:
        query += ' LIMIT ?'
        params.append(int(limit))

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def count_tasks_by_date_range(start_date: str, end_date: str,
                              org: str = None, project: str = None,
                              environment: str = None, task_type: str = None,
                              status: str = None, log_type: str = None) -> int:
    """Count task records filtered by date range and optional dimensions."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    query = 'SELECT COUNT(*) FROM tasks WHERE end_time >= ? AND end_time <= ?'
    params = [f'{start_date} 00:00:00', f'{end_date} 23:59:59']

    if org:
        query += ' AND org = ?'
        params.append(org)
    if project:
        query += ' AND project_name = ?'
        params.append(project)
    if environment:
        query += ' AND environment = ?'
        params.append(environment)
    if task_type:
        query += ' AND task_type = ?'
        params.append(task_type)
    if status:
        query += ' AND status = ?'
        params.append(status)
    query, params = _append_log_type_filter(query, params, log_type)

    cursor = conn.cursor()
    cursor.execute(query, params)
    count = int(cursor.fetchone()[0])
    conn.close()
    return count


def get_mst_timestamp():
    """Get current timestamp in Mountain Standard Time."""
    if _HAS_PYTZ:
        mst = pytz.timezone('America/Denver')
        return datetime.now(mst).strftime('%Y-%m-%d %H:%M:%S')
    else:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo('America/Denver')).strftime('%Y-%m-%d %H:%M:%S')
        # Fallback to UTC timestamp if timezone support missing
        return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')


def _summarize_date_ranges(frame: pd.DataFrame) -> tuple[str | None, str | None, str | None]:
    """Return overall start/end dates and a grouped date-range label for a frame."""
    if frame is None or frame.empty:
        return None, None, None

    date_series = None
    for candidate in ['End Time', 'Start Time']:
        if candidate in frame.columns:
            # format='mixed' keeps ISO YYYY-MM-DD intact; dayfirst still handles DD/MM slash dates
            parsed = _to_datetime_mixed(frame[candidate]).dropna()
            if not parsed.empty:
                date_series = parsed.dt.date
                break

    if date_series is None or date_series.empty:
        return None, None, None

    unique_dates = sorted(set(date_series.tolist()))
    start_date = unique_dates[0].isoformat()
    end_date = unique_dates[-1].isoformat()

    ranges = []
    range_start = unique_dates[0]
    range_end = unique_dates[0]
    for current_date in unique_dates[1:]:
        if current_date == range_end + timedelta(days=1):
            range_end = current_date
        else:
            ranges.append((range_start, range_end))
            range_start = current_date
            range_end = current_date
    ranges.append((range_start, range_end))

    def _format_range(start, end):
        if start == end:
            return start.isoformat()
        return f'{start.isoformat()} to {end.isoformat()}'

    label = '; '.join(_format_range(start, end) for start, end in ranges)
    return start_date, end_date, label


def _hash_column_values(series: pd.Series) -> list[str]:
    """Normalize a column to hash-stable strings (matching prior row-hash rules)."""
    if pd.api.types.is_numeric_dtype(series):
        values = series.to_numpy()
        out = []
        for v in values:
            if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
                out.append('')
            else:
                out.append(format(v, '.12g'))
        return out

    out = []
    for v in series.to_numpy():
        if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
            out.append('')
        else:
            out.append(str(v))
    return out


def _compute_row_hashes(frame: pd.DataFrame, columns: list[str], progress_callback=None) -> list[str]:
    """SHA256 row hashes without DataFrame.apply (too slow for ~1M rows)."""
    if frame.empty:
        return []

    joined = None
    for col in columns:
        as_text = pd.Series(_hash_column_values(frame[col]), index=frame.index, dtype=object)
        joined = as_text if joined is None else (joined + '|' + as_text)

    values = joined.to_numpy()
    total = len(values)
    hashes = [''] * total
    report_every = max(50_000, total // 20) if total else 1

    for i, text in enumerate(values):
        hashes[i] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if progress_callback and (i + 1 == total or (i + 1) % report_every == 0):
            pct = int(10 + ((i + 1) / total) * 25)
            progress_callback(pct, f'Computed hashes for rows 0:{i + 1}')

    return hashes


def save_run(merged_df: pd.DataFrame) -> tuple[int, int, str | None, str | None, str | None]:
    """Append merged rows to the historical task table, deduplicated by row hash.

    This implementation performs chunked batched inserts using `INSERT OR IGNORE`
    against a unique `row_hash` index. If `progress_callback` is provided it will
    be called with `(percent:int, message:str)` periodically so the UI can update.
    A lightweight file logger is also written next to the DB so you can inspect
    what happened after the run.
    """
    # Allow optional progress callback: progress_callback(percent:int, message:str)
    def _noop_progress(percent, message):
        return

    progress_callback = None
    # Backwards compatible: caller may pass a callable via merged_df.attrs['progress_cb']
    if hasattr(merged_df, 'attrs') and isinstance(merged_df.attrs, dict) and merged_df.attrs.get('progress_cb'):
        progress_callback = merged_df.attrs.get('progress_cb')

    # If the caller passed a direct argument (newer signature), support that too
    # Note: we keep the original signature for backward compatibility, so
    # callers that want progress should set `merged_df.attrs['progress_cb'] = cb`.
    if progress_callback is None:
        progress_callback = _noop_progress

    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Setup lightweight logger
    logger = logging.getLogger('informatica.save')
    if not logger.handlers:
        fh = logging.FileHandler(Path(DB_PATH).with_name('informatica_save.log'))
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(fh)
        logger.setLevel(logging.INFO)

    if merged_df is None or merged_df.empty:
        cursor.execute('SELECT COUNT(*) FROM tasks')
        total_rows = cursor.fetchone()[0]
        conn.close()
        return 0, total_rows, None, None, None

    progress_callback(2, f'Preparing {len(merged_df):,} rows for historical save...')
    processed_start_date, processed_end_date, processed_label = _summarize_date_ranges(merged_df)

    # Only stable task identity belongs in the dedupe hash.
    # Derived/calculated fields (Run Date, IPUs, Cost/IPU/Month, etc.) and
    # mutable snapshot fields (like Status) are intentionally excluded so
    # re-uploading the same task data does not create a new hash.
    hash_columns = [
        'Task ID', 'Task Run ID', 'Task Name', 'Task Type', 'Project Name',
        'Folder Name', 'Org', 'Environment', 'Agent Name', 'Log Type',
        'Start Time', 'End Time', 'Metered Value',
    ]
    available_columns = [col for col in hash_columns if col in merged_df.columns]

    if not available_columns:
        cursor.execute('SELECT COUNT(*) FROM tasks')
        total_rows = cursor.fetchone()[0]
        conn.close()
        return 0, total_rows, None, None, None

    # Use a wider set of source columns for storage than we use for dedupe hashing.
    insert_source_columns = [
        'Task ID', 'Task Name', 'Task Object Name', 'Task Type', 'Task Run ID',
        'Agent Name', 'Project Name', 'Folder Name', 'Org', 'Environment', 'Status',
        'Log Type', 'Start Time', 'End Time', 'IPUs', 'Cost/IPU/Month', 'Metered Value', 'Cores Used'
    ]
    present_source_columns = [col for col in insert_source_columns if col in merged_df.columns]
    staging_df = merged_df[present_source_columns].copy()

    # Normalize datetimes once (format='mixed' keeps ISO dates intact; dayfirst handles DD/MM).
    progress_callback(5, 'Normalizing timestamps...')
    for dt_col in ['Start Time', 'End Time']:
        if dt_col in staging_df.columns:
            staging_df[dt_col] = _to_datetime_mixed(staging_df[dt_col])
            staging_df[dt_col] = staging_df[dt_col].dt.strftime('%Y-%m-%d %H:%M:%S')
            staging_df[dt_col] = staging_df[dt_col].fillna('')

    for num_col in ['IPUs', 'Cost/IPU/Month', 'Metered Value', 'Cores Used']:
        if num_col in staging_df.columns:
            staging_df[num_col] = pd.to_numeric(staging_df[num_col], errors='coerce')
            staging_df[num_col] = staging_df[num_col].round(6)

    # Hash from the same normalized staging frame used for insert.
    progress_callback(8, 'Computing deterministic row hashes...')
    hash_frame = staging_df[available_columns].copy()
    for num_col in ['Metered Value']:
        if num_col in hash_frame.columns:
            hash_frame[num_col] = hash_frame[num_col].fillna(0)
    staging_df['row_hash'] = _compute_row_hashes(hash_frame, available_columns, progress_callback)

    progress_callback(40, 'Checking existing historical rows...')
    existing_hashes = {
        row[0] for row in cursor.execute('SELECT row_hash FROM tasks WHERE row_hash IS NOT NULL') if row[0]
    }
    new_rows_df = staging_df[~staging_df['row_hash'].isin(existing_hashes)].drop_duplicates(subset=['row_hash'])
    _added_start_date, _added_end_date, added_label = _summarize_date_ranges(new_rows_df)

    col_map = {
        'Task ID': 'task_id',
        'Task Name': 'task_name',
        'Task Object Name': 'task_object_name',
        'Task Type': 'task_type',
        'Task Run ID': 'task_run_id',
        'Agent Name': 'agent_name',
        'Project Name': 'project_name',
        'Folder Name': 'folder_name',
        'Org': 'org',
        'Environment': 'environment',
        'Status': 'status',
        'Log Type': 'log_type',
        'Start Time': 'start_time',
        'End Time': 'end_time',
        'IPUs': 'ipus',
        'Cost/IPU/Month': 'cost',
        'Metered Value': 'metered_value',
        'Cores Used': 'cores_used',
        'row_hash': 'row_hash',
    }
    new_rows_df = new_rows_df.rename(columns=col_map)

    insert_cols = [
        col for col in [
            'task_id', 'task_name', 'task_object_name', 'task_type', 'task_run_id', 'row_hash',
            'agent_name', 'project_name', 'folder_name', 'org', 'environment', 'status',
            'log_type', 'start_time', 'end_time', 'ipus', 'cost', 'metered_value', 'cores_used'
        ] if col in new_rows_df.columns
    ]

    cursor.execute('SELECT COUNT(*) FROM tasks')
    before_count = cursor.fetchone()[0]

    total_to_insert = len(new_rows_df)
    if total_to_insert == 0:
        progress_callback(100, 'No new rows to insert (all duplicates)')
        conn.close()
        return 0, before_count, None, None, None

    progress_callback(45, f'Preparing {total_to_insert:,} new rows for insert...')
    insert_df = new_rows_df[insert_cols].copy()
    for col in insert_df.columns:
        if insert_df[col].dtype == object:
            insert_df[col] = insert_df[col].replace('', None)
    insert_df = insert_df.where(insert_df.notna(), None)
    tuples = list(insert_df.itertuples(index=False, name=None))

    placeholders = ','.join(['?'] * len(insert_cols))
    insert_sql = f"INSERT OR IGNORE INTO tasks ({', '.join(insert_cols)}) VALUES ({placeholders})"

    chunk_size = 10_000
    processed = 0
    report_every = max(chunk_size, total_to_insert // 20)
    conn.execute('BEGIN')
    try:
        for start in range(0, total_to_insert, chunk_size):
            end = min(start + chunk_size, total_to_insert)
            batch = tuples[start:end]
            cursor.executemany(insert_sql, batch)
            processed += len(batch)
            if processed == total_to_insert or processed % report_every < chunk_size:
                pct = int(45 + (processed / total_to_insert) * 50)
                progress_callback(pct, f'Inserted {processed:,}/{total_to_insert:,} new rows')
            logger.info(f'Inserted batch rows {start}:{end} ({len(batch)} rows)')
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception('Error during chunked insert')
        raise

    cursor.execute('SELECT COUNT(*) FROM tasks')
    after_count = cursor.fetchone()[0]
    rows_added = after_count - before_count

    if rows_added > 0:
        if processed_label:
            progress_callback(100, f'Finished. {rows_added} new rows added from processed {processed_label}, {after_count} total rows')
        else:
            progress_callback(100, f'Finished. {rows_added} new rows added, {after_count} total rows')
        logger.info(f'Finished save_run: {rows_added} new rows, {after_count} total')

        note = f'Added date range(s): {added_label}' if added_label else None
        record_history_event(
            'ADD',
            start_date=processed_start_date,
            end_date=processed_end_date,
            affected_rows=rows_added,
            remaining_rows=after_count,
            note=note,
        )
    else:
        processed_start_date = None
        processed_end_date = None
        progress_callback(100, f'Finished. {rows_added} new rows added, {after_count} total rows')
        logger.info(f'Finished save_run: {rows_added} new rows, {after_count} total')

    conn.close()

    return rows_added, after_count, processed_start_date, processed_end_date, added_label


def delete_tasks_by_date_range(start_date: str, end_date: str, org: str = None,
                               project: str = None, environment: str = None,
                               task_type: str = None, agent_name: str = None,
                               status: str = None, log_type: str = None) -> tuple[int, int]:
    """Delete historical task rows in a date range and return deleted and remaining counts."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    query = 'DELETE FROM tasks WHERE end_time >= ? AND end_time <= ?'
    params = [f'{start_date} 00:00:00', f'{end_date} 23:59:59']

    if org:
        query += ' AND org = ?'
        params.append(org)
    if project:
        query += ' AND project_name = ?'
        params.append(project)
    if environment:
        query += ' AND environment = ?'
        params.append(environment)
    if task_type:
        query += ' AND task_type = ?'
        params.append(task_type)
    if agent_name:
        query += ' AND agent_name = ?'
        params.append(agent_name)
    if status:
        query += ' AND status = ?'
        params.append(status)
    query, params = _append_log_type_filter(query, params, log_type)

    cursor.execute('SELECT COUNT(*) FROM tasks')
    before_count = cursor.fetchone()[0]

    cursor.execute(query, params)
    deleted_rows = cursor.rowcount if cursor.rowcount != -1 else None

    cursor.execute('SELECT COUNT(*) FROM tasks')
    remaining_rows = cursor.fetchone()[0]
    if deleted_rows is None:
        deleted_rows = before_count - remaining_rows
    conn.commit()
    conn.close()

    record_history_event(
        'DELETE',
        start_date=start_date,
        end_date=end_date,
        affected_rows=deleted_rows,
        remaining_rows=remaining_rows,
        note='Historical rows deleted by date range',
    )

    return deleted_rows, remaining_rows



def get_all_runs() -> pd.DataFrame:
    """Get list of all saved runs."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    query = '''
        SELECT run_id, run_name, run_timestamp, total_rows, total_ipus, 
               total_cost, unique_task_runs, created_at
        FROM runs
        ORDER BY run_timestamp DESC
    '''
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def get_run_details(run_id: int) -> dict:
    """Get detailed information about a specific run."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Convert numpy types to Python native types
    try:
        run_id = int(run_id)
    except (TypeError, ValueError):
        pass
    
    # Get run metadata
    cursor.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,))
    columns = [description[0] for description in cursor.description]
    row = cursor.fetchone()
    
    if row is None:
        conn.close()
        # Return empty structure for missing run
        return {
            'run': {},
            'org_summary': pd.DataFrame(),
            'env_summary': pd.DataFrame(),
            'project_summary': pd.DataFrame(),
            'task_type_summary': pd.DataFrame(),
            'status_summary': pd.DataFrame(),
            'daily_stats': pd.DataFrame(),
        }
    
    run = dict(zip(columns, row))
    
    # Get summaries
    org_summary = pd.read_sql_query(
        'SELECT * FROM org_summaries WHERE run_id = ? ORDER BY total_ipus DESC',
        conn, params=(run_id,)
    )
    
    env_summary = pd.read_sql_query(
        'SELECT * FROM env_summaries WHERE run_id = ? ORDER BY total_ipus DESC',
        conn, params=(run_id,)
    )
    
    project_summary = pd.read_sql_query(
        'SELECT * FROM project_summaries WHERE run_id = ? ORDER BY total_ipus DESC',
        conn, params=(run_id,)
    )
    
    task_type_summary = pd.read_sql_query(
        'SELECT * FROM task_type_summaries WHERE run_id = ? ORDER BY total_ipus DESC',
        conn, params=(run_id,)
    )
    
    status_summary = pd.read_sql_query(
        'SELECT * FROM status_summaries WHERE run_id = ? ORDER BY total_ipus DESC',
        conn, params=(run_id,)
    )
    
    daily_stats = pd.read_sql_query(
        'SELECT * FROM daily_stats WHERE run_id = ? ORDER BY stat_date',
        conn, params=(run_id,)
    )
    
    conn.close()
    
    return {
        'run': run,
        'org_summary': org_summary,
        'env_summary': env_summary,
        'project_summary': project_summary,
        'task_type_summary': task_type_summary,
        'status_summary': status_summary,
        'daily_stats': daily_stats,
    }


def compare_runs(run_ids: list) -> dict:
    """Compare multiple runs and calculate differences."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    
    # Convert numpy types to Python native types
    run_ids = [int(rid) for rid in run_ids]
    
    # Get run metadata for comparison
    placeholders = ','.join('?' * len(run_ids))
    runs = pd.read_sql_query(
        f'''SELECT run_id, run_name, run_timestamp, total_ipus, 
               total_cost, total_rows, unique_task_runs
            FROM runs WHERE run_id IN ({placeholders})
            ORDER BY run_timestamp''',
        conn, params=tuple(run_ids)
    )
    
    # Get org summaries for comparison
    org_comparison = pd.read_sql_query(
        f'''SELECT run_id, org_name, total_ipus, total_cost, task_count
            FROM org_summaries WHERE run_id IN ({placeholders})''',
        conn, params=tuple(run_ids)
    )
    
    # Get environment summaries for comparison
    env_comparison = pd.read_sql_query(
        f'''SELECT run_id, environment, total_ipus, total_cost, task_count
            FROM env_summaries WHERE run_id IN ({placeholders})''',
        conn, params=tuple(run_ids)
    )
    
    # Get project summaries for comparison
    project_comparison = pd.read_sql_query(
        f'''SELECT run_id, project_name, total_ipus, total_cost, task_count
            FROM project_summaries WHERE run_id IN ({placeholders})''',
        conn, params=tuple(run_ids)
    )
    
    conn.close()
    
    return {
        'runs': runs,
        'org_comparison': org_comparison,
        'env_comparison': env_comparison,
        'project_comparison': project_comparison,
    }


def get_trend_data(org_name: str = None, project_name: str = None, 
                   environment: str = None, limit_runs: int = 10) -> dict:
    """
    Get trend data across multiple runs for a specific dimension.
    
    Args:
        org_name: Filter by organization (optional)
        project_name: Filter by project (optional)
        environment: Filter by environment (optional)
        limit_runs: Number of recent runs to include
        
    Returns:
        Dictionary with trend data for visualization
    """
    init_database()
    conn = sqlite3.connect(DB_PATH)
    
    # Get recent runs
    recent_runs = pd.read_sql_query(
        f'''SELECT run_id, run_name, run_timestamp FROM runs 
            ORDER BY run_timestamp DESC LIMIT {limit_runs}''',
        conn
    )
    run_ids = recent_runs['run_id'].tolist()
    
    if not run_ids:
        conn.close()
        return {}
    
    placeholders = ','.join('?' * len(run_ids))
    
    # Build queries based on filters
    trends = {}
    
    if org_name:
        org_trend = pd.read_sql_query(
            f'''SELECT r.run_id, r.run_name, r.run_timestamp, os.total_ipus, 
                   os.total_cost, os.task_count
                FROM org_summaries os
                JOIN runs r ON os.run_id = r.run_id
                WHERE os.run_id IN ({placeholders}) AND os.org_name = ?
                ORDER BY r.run_timestamp''',
            conn, params=tuple(run_ids) + (org_name,)
        )
        trends['org_trend'] = org_trend
    
    if project_name:
        project_trend = pd.read_sql_query(
            f'''SELECT r.run_id, r.run_name, r.run_timestamp, ps.total_ipus,
                   ps.total_cost, ps.task_count
                FROM project_summaries ps
                JOIN runs r ON ps.run_id = r.run_id
                WHERE ps.run_id IN ({placeholders}) AND ps.project_name = ?
                ORDER BY r.run_timestamp''',
            conn, params=tuple(run_ids) + (project_name,)
        )
        trends['project_trend'] = project_trend
    
    if environment:
        env_trend = pd.read_sql_query(
            f'''SELECT r.run_id, r.run_name, r.run_timestamp, es.total_ipus,
                   es.total_cost, es.task_count
                FROM env_summaries es
                JOIN runs r ON es.run_id = r.run_id
                WHERE es.run_id IN ({placeholders}) AND es.environment = ?
                ORDER BY r.run_timestamp''',
            conn, params=tuple(run_ids) + (environment,)
        )
        trends['env_trend'] = env_trend
    
    # Overall trends across all runs
    overall_trend = pd.read_sql_query(
        f'''SELECT run_id, run_name, run_timestamp, total_ipus, total_cost, total_rows
            FROM runs WHERE run_id IN ({placeholders})
            ORDER BY run_timestamp''',
        conn, params=tuple(run_ids)
    )
    trends['overall_trend'] = overall_trend
    
    conn.close()
    return trends


def detect_anomalies(metric: str = 'total_ipus', threshold_std: float = 2.0) -> pd.DataFrame:
    """
    Detect anomalies in metric values across runs.
    
    Args:
        metric: 'total_ipus', 'total_cost', or 'total_rows'
        threshold_std: Number of standard deviations for anomaly detection
        
    Returns:
        DataFrame with flagged anomalies
    """
    init_database()
    conn = sqlite3.connect(DB_PATH)
    
    # Get all runs for the metric
    metric_col = metric
    runs = pd.read_sql_query(
        f'SELECT run_id, run_name, run_timestamp, {metric_col} FROM runs ORDER BY run_timestamp',
        conn
    )
    conn.close()
    
    if runs.empty or len(runs) < 3:
        return pd.DataFrame()
    
    # Calculate mean and std
    mean = runs[metric_col].mean()
    std = runs[metric_col].std()
    
    # Identify anomalies
    runs['z_score'] = (runs[metric_col] - mean) / std
    runs['is_anomaly'] = runs['z_score'].abs() > threshold_std
    runs['anomaly_type'] = runs.apply(
        lambda row: 'High' if row['z_score'] > threshold_std else 'Low' if row['z_score'] < -threshold_std else 'Normal',
        axis=1
    )
    
    return runs[runs['is_anomaly']]


# ============================================================================
# NEW HISTORICAL ANALYSIS FUNCTIONS (by task end date, not run date)
# ============================================================================

def get_task_date_range() -> tuple:
    """Get the min and max end dates from all tasks in the database."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Ignore future-dated rows when reporting available history.
    # The analysis views already stop at yesterday to avoid partial-day counts,
    # so future timestamps would incorrectly extend the date picker window.
    analysis_cutoff = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        'SELECT MIN(end_time), MAX(end_time) FROM tasks WHERE end_time IS NOT NULL AND end_time <= ?',
        (analysis_cutoff,),
    )
    result = cursor.fetchone()
    conn.close()
    
    if result[0] is None:
        return (None, None)
    
    return (result[0], result[1])


def _collapse_dates_to_ranges(missing_dates: list) -> list[tuple]:
    """Collapse a sorted list of dates into inclusive (start, end) ranges."""
    if not missing_dates:
        return []

    missing_ranges = []
    range_start = missing_dates[0]
    previous_date = missing_dates[0]

    for current_date in missing_dates[1:]:
        if current_date == previous_date + timedelta(days=1):
            previous_date = current_date
            continue

        missing_ranges.append((range_start, previous_date))
        range_start = current_date
        previous_date = current_date

    missing_ranges.append((range_start, previous_date))
    return missing_ranges


def format_display_date(value) -> str:
    """Format a date as 'June 5, 2026'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    parsed = pd.to_datetime(value, errors='coerce')
    if pd.isna(parsed):
        return str(value)
    day = parsed.day
    return f"{parsed.strftime('%B')} {day}, {parsed.year}"


def format_display_date_range(start, end) -> str:
    """Format a single day or inclusive range with month names."""
    start_label = format_display_date(start)
    if start == end or end is None:
        return start_label
    return f"{start_label} to {format_display_date(end)}"


def get_missing_task_date_ranges(start_date, end_date, org: str = None, project: str = None,
                                 environment: str = None, task_type: str = None,
                                 status: str = None, log_type: str = None) -> list[tuple]:
    """Return consecutive date ranges with no task data inside the requested span."""
    start_dt = pd.to_datetime(start_date).date()
    end_dt = pd.to_datetime(end_date).date()

    if start_dt > end_dt:
        return []

    daily_stats = get_daily_stats_by_date_range(
        start_dt.isoformat(),
        end_dt.isoformat(),
        org=org,
        project=project,
        environment=environment,
        log_type=log_type,
    )

    expected_dates = pd.date_range(start=start_dt, end=end_dt, freq='D').date

    if daily_stats.empty:
        return [(start_dt, end_dt)]

    observed_dates = {pd.to_datetime(value).date() for value in daily_stats['date'].dropna()}
    missing_dates = sorted(set(expected_dates) - observed_dates)
    return _collapse_dates_to_ranges(missing_dates)


def get_org_coverage_gaps(start_date, end_date, log_type: str = None) -> pd.DataFrame:
    """Return per-org (and log type) coverage plus missing date ranges.

    Gaps are holes *inside* each org/log-type's own first→last observed span
    (intersected with the requested window). Days before an org's first upload
    or after its last upload are not treated as missing — different exports
    simply cover different periods (e.g. Campus-Prod may only have one month
    while Prod Task Usage covers a full year).

    Source filenames are not stored on historical rows; organization is the
    label assigned at upload and is the best proxy for which spreadsheet
    coverage is missing.
    """
    start_dt = pd.to_datetime(start_date).date()
    end_dt = pd.to_datetime(end_date).date()
    if start_dt > end_dt:
        return pd.DataFrame()

    init_database()
    conn = sqlite3.connect(DB_PATH)

    query = (
        "SELECT COALESCE(NULLIF(TRIM(org), ''), 'Unknown') AS org, "
        "COALESCE(NULLIF(TRIM(log_type), ''), 'Task Usage') AS log_type, "
        "DATE(end_time) AS date, "
        "COUNT(*) AS task_count "
        "FROM tasks "
        "WHERE end_time >= ? AND end_time <= ?"
    )
    params = [f'{start_dt.isoformat()} 00:00:00', f'{end_dt.isoformat()} 23:59:59']
    query, params = _append_log_type_filter(query, params, log_type)
    query += " GROUP BY org, log_type, DATE(end_time) ORDER BY org, log_type, DATE(end_time)"

    daily = pd.read_sql_query(query, conn, params=params)
    conn.close()

    if daily.empty:
        return pd.DataFrame(columns=[
            'org', 'log_type', 'first_date', 'last_date', 'days_present',
            'days_missing', 'gap_count', 'missing_ranges',
        ])

    daily['date'] = pd.to_datetime(daily['date'], errors='coerce').dt.date
    daily = daily.dropna(subset=['date'])

    rows = []
    for (org, row_log_type), group in daily.groupby(['org', 'log_type'], dropna=False):
        present = {d for d in group['date'].tolist() if d is not None}
        if not present:
            continue

        first_present = min(present)
        last_present = max(present)
        # Only look for holes inside this org's own coverage window.
        span_start = max(start_dt, first_present)
        span_end = min(end_dt, last_present)
        expected_dates = set(pd.date_range(start=span_start, end=span_end, freq='D').date)
        missing = sorted(expected_dates - present)
        ranges = _collapse_dates_to_ranges(missing)
        range_labels = [format_display_date_range(start, end) for start, end in ranges]
        rows.append({
            'org': org,
            'log_type': row_log_type,
            'first_date': format_display_date(first_present),
            'last_date': format_display_date(last_present),
            'days_present': len(present),
            'days_missing': len(missing),
            'gap_count': len(ranges),
            'missing_ranges': '; '.join(range_labels) if range_labels else '',
        })

    coverage = pd.DataFrame(rows)
    if coverage.empty:
        return coverage
    return coverage.sort_values(['days_missing', 'org', 'log_type'], ascending=[False, True, True]).reset_index(drop=True)


def get_tasks_by_date_range(start_date: str, end_date: str, 
                            org: str = None, project: str = None,
                            environment: str = None, task_type: str = None,
                            agent_name: str = None,
                            status: str = None,
                            log_type: str = None) -> pd.DataFrame:
    """
    Get task records filtered by date range and optional dimensions.
    
    Args:
        start_date: ISO format date string (YYYY-MM-DD)
        end_date: ISO format date string (YYYY-MM-DD)
        org: Filter by organization (optional)
        project: Filter by project name (optional)
        environment: Filter by environment (optional)
        task_type: Filter by task type (optional)
        agent_name: Filter by agent name (optional)
        status: Filter by status (optional)
        log_type: Filter by log type (optional)
    
    Returns:
        DataFrame of task records
    """
    init_database()
    conn = sqlite3.connect(DB_PATH)
    
    query = 'SELECT * FROM tasks WHERE end_time >= ? AND end_time <= ?'
    params = [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    
    if org:
        query += ' AND org = ?'
        params.append(org)
    if project:
        query += ' AND project_name = ?'
        params.append(project)
    if environment:
        query += ' AND environment = ?'
        params.append(environment)
    if task_type:
        query += ' AND task_type = ?'
        params.append(task_type)
    if agent_name:
        query += ' AND agent_name = ?'
        params.append(agent_name)
    if status:
        query += ' AND status = ?'
        params.append(status)
    query, params = _append_log_type_filter(query, params, log_type)
    
    query += ' ORDER BY end_time'
    
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    
    return df


def get_daily_stats_by_date_range(start_date: str, end_date: str,
                                   org: str = None, project: str = None,
                                   environment: str = None,
                                   agent_name: str = None,
                                   log_type: str = None) -> pd.DataFrame:
    """
    Get daily aggregated statistics for a date range.
    
    Returns DataFrame with columns: date, task_count, total_ipus, total_cost
    """
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT DATE(end_time) AS date, "
        "COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost "
        "FROM tasks "
        "WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']

    if org:
        query += ' AND org = ?'
        params.append(org)
    if project:
        query += ' AND project_name = ?'
        params.append(project)
    if environment:
        query += ' AND environment = ?'
        params.append(environment)
    if agent_name:
        query += ' AND agent_name = ?'
        params.append(agent_name)
    query, params = _append_log_type_filter(query, params, log_type)

    query += ' GROUP BY DATE(end_time) ORDER BY DATE(end_time)'

    daily = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return daily


def get_org_stats_by_date_range(start_date: str, end_date: str,
                                log_type: str = None) -> pd.DataFrame:
    """Get statistics by organization for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT COALESCE(NULLIF(TRIM(org), ''), 'Unknown') AS org, COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    query, params = _append_log_type_filter(query, params, log_type)
    query += " GROUP BY COALESCE(NULLIF(TRIM(org), ''), 'Unknown') ORDER BY total_ipus DESC"
    org_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return org_stats


def get_org_daily_stats_by_date_range(start_date: str, end_date: str,
                                      log_type: str = None) -> pd.DataFrame:
    """Daily IPU/task totals per organization without loading every task row."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT DATE(end_time) AS date, "
        "COALESCE(NULLIF(TRIM(org), ''), 'Unknown') AS org, "
        "COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    query, params = _append_log_type_filter(query, params, log_type)
    query += (
        " GROUP BY DATE(end_time), COALESCE(NULLIF(TRIM(org), ''), 'Unknown') "
        "ORDER BY DATE(end_time), org"
    )
    daily = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return daily


def get_project_stats_by_date_range(start_date: str, end_date: str,
                                     org: str = None,
                                     log_type: str = None) -> pd.DataFrame:
    """Get statistics by project for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT project_name, COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    if org:
        query += ' AND org = ?'
        params.append(org)
    query, params = _append_log_type_filter(query, params, log_type)
    query += " GROUP BY project_name ORDER BY total_ipus DESC"
    project_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return project_stats


def get_task_name_stats_by_date_range(start_date: str, end_date: str,
                                      org: str = None, log_type: str = None,
                                      limit: int = 25) -> pd.DataFrame:
    """Top tasks by IPU for a date range (org + project + task name)."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT "
        "COALESCE(NULLIF(TRIM(org), ''), 'Unknown') AS org, "
        "COALESCE(NULLIF(TRIM(project_name), ''), '-') AS project_name, "
        "COALESCE(NULLIF(TRIM(task_name), ''), '(Unnamed)') AS task_name, "
        "COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    if org:
        query += ' AND org = ?'
        params.append(org)
    query, params = _append_log_type_filter(query, params, log_type)
    query += " GROUP BY org, project_name, task_name ORDER BY total_ipus DESC"
    if limit:
        query += " LIMIT ?"
        params.append(int(limit))
    task_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return task_stats


def get_filtered_daily_stats_by_date_range(
    start_date: str,
    end_date: str,
    org: str = None,
    orgs: list | None = None,
    project_name: str = None,
    task_name: str = None,
    log_type: str = None,
) -> pd.DataFrame:
    """Daily IPUs for an optional org/project/task slice (for what-if cut estimates)."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT DATE(end_time) AS date, "
        "COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']

    if org:
        query += ' AND org = ?'
        params.append(org)
    elif orgs:
        placeholders = ','.join('?' * len(orgs))
        query += f' AND org IN ({placeholders})'
        params.extend(list(orgs))

    if project_name is not None:
        if str(project_name).strip() in ('-', '(No project)', ''):
            query += (
                " AND ("
                "project_name IS NULL OR TRIM(COALESCE(project_name, '')) = '' "
                "OR TRIM(COALESCE(project_name, '')) = '-' "
                "OR TRIM(COALESCE(project_name, '')) = '(No project)'"
                ")"
            )
        else:
            query += ' AND project_name = ?'
            params.append(project_name)

    if task_name is not None:
        query += ' AND task_name = ?'
        params.append(task_name)

    query, params = _append_log_type_filter(query, params, log_type)
    query += ' GROUP BY DATE(end_time) ORDER BY DATE(end_time)'

    daily = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return daily


def get_task_usage_lookup_by_date_range(
    start_date: str,
    end_date: str,
    task_query: str,
    folder_query: str = None,
    org: str = None,
    log_type: str = None,
    match_mode: str = 'contains',
) -> pd.DataFrame:
    """Return task rows matching a task object/name query in a date window.

    Matching is performed against both `task_object_name` (preferred) and
    `task_name` (fallback for older rows that do not have object names).
    """
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT "
        "task_run_id, "
        "COALESCE(NULLIF(TRIM(task_object_name), ''), NULLIF(TRIM(task_name), ''), '(Unnamed)') AS matched_task, "
        "COALESCE(NULLIF(TRIM(task_object_name), ''), '') AS task_object_name, "
        "COALESCE(NULLIF(TRIM(task_name), ''), '') AS task_name, "
        "COALESCE(NULLIF(TRIM(folder_name), ''), '(No folder)') AS folder_name, "
        "COALESCE(NULLIF(TRIM(project_name), ''), '(No project)') AS project_name, "
        "COALESCE(NULLIF(TRIM(org), ''), 'Unknown') AS org, "
        "COALESCE(NULLIF(TRIM(log_type), ''), 'Task Usage') AS log_type, "
        "end_time, "
        "COALESCE(" + ipu_expr + ", 0) AS effective_ipus, "
        "COALESCE(" + cost_expr + ", 0) AS effective_cost, "
        "COALESCE(metered_value, 0) AS metered_value "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']

    cleaned_task_query = (task_query or '').strip().lower()
    if cleaned_task_query:
        if match_mode == 'exact':
            query += (
                " AND ("
                "LOWER(TRIM(COALESCE(task_object_name, ''))) = ? "
                "OR LOWER(TRIM(COALESCE(task_name, ''))) = ?"
                ")"
            )
            params.extend([cleaned_task_query, cleaned_task_query])
        else:
            like_query = f"%{cleaned_task_query}%"
            query += (
                " AND ("
                "LOWER(TRIM(COALESCE(task_object_name, ''))) LIKE ? "
                "OR LOWER(TRIM(COALESCE(task_name, ''))) LIKE ?"
                ")"
            )
            params.extend([like_query, like_query])

    cleaned_folder_query = (folder_query or '').strip().lower()
    if cleaned_folder_query:
        if match_mode == 'exact':
            query += " AND LOWER(TRIM(COALESCE(folder_name, ''))) = ?"
            params.append(cleaned_folder_query)
        else:
            query += " AND LOWER(TRIM(COALESCE(folder_name, ''))) LIKE ?"
            params.append(f"%{cleaned_folder_query}%")

    if org:
        query += ' AND org = ?'
        params.append(org)

    query, params = _append_log_type_filter(query, params, log_type)
    query += ' ORDER BY end_time DESC, effective_ipus DESC'

    matches = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return matches


def _normalize_environment_base(name) -> str:
    """Collapse infra variants into the shared environment label.

    Examples:
      'CES-Sandbox - AWS EC2' -> 'CES-Sandbox'
      'CES-Sandbox - On-premise Linux agents' -> 'CES-Sandbox'
      'BYU-Prod - AWS EC2 - DO NOT USE' -> 'BYU-Prod'
      'Student Life' -> 'Student Life'
    """
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return 'Unknown'
    text = str(name).strip()
    if not text:
        return 'Unknown'
    if ' - ' in text:
        return text.split(' - ', 1)[0].strip() or 'Unknown'
    return text


def get_environment_stats_by_date_range(start_date: str, end_date: str,
                                        log_type: str = None) -> pd.DataFrame:
    """Get statistics by environment for a date range.

    Environment names are consolidated by base label so infra suffixes
    (AWS EC2, On-premise Linux agents, etc.) do not create separate rows.
    """
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT environment, COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    query, params = _append_log_type_filter(query, params, log_type)
    query += " GROUP BY environment ORDER BY total_ipus DESC"
    env_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()

    if env_stats.empty:
        return env_stats

    env_stats['environment'] = env_stats['environment'].map(_normalize_environment_base)
    env_stats = (
        env_stats.groupby('environment', as_index=False)
        .agg(
            task_count=('task_count', 'sum'),
            total_ipus=('total_ipus', 'sum'),
            total_cost=('total_cost', 'sum'),
            unique_tasks=('unique_tasks', 'sum'),
        )
        .sort_values('total_ipus', ascending=False)
        .reset_index(drop=True)
    )
    return env_stats


def get_agent_stats_by_date_range(start_date: str, end_date: str,
                                  log_type: str = None) -> pd.DataFrame:
    """Get statistics by agent for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT agent_name, COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    query, params = _append_log_type_filter(query, params, log_type)
    query += " GROUP BY agent_name ORDER BY total_ipus DESC"
    agent_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return agent_stats


def get_log_type_stats_by_date_range(start_date: str, end_date: str) -> pd.DataFrame:
    """Get statistics by log type for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT COALESCE(NULLIF(TRIM(log_type), ''), 'Task Usage') AS log_type, "
        "COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE end_time >= ? AND end_time <= ? "
        "GROUP BY COALESCE(NULLIF(TRIM(log_type), ''), 'Task Usage') "
        "ORDER BY total_ipus DESC"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    log_type_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return log_type_stats


def get_log_type_daily_stats_by_date_range(start_date: str, end_date: str,
                                           log_type: str = None) -> pd.DataFrame:
    """Daily IPU/task totals per log type without loading every task row."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT DATE(end_time) AS date, "
        "COALESCE(NULLIF(TRIM(log_type), ''), 'Task Usage') AS log_type, "
        "COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    query, params = _append_log_type_filter(query, params, log_type)
    query += (
        " GROUP BY DATE(end_time), COALESCE(NULLIF(TRIM(log_type), ''), 'Task Usage') "
        "ORDER BY DATE(end_time), log_type"
    )
    daily = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return daily


def get_task_type_stats_by_date_range(start_date: str, end_date: str,
                                      log_type: str = None) -> pd.DataFrame:
    """Get statistics by task type for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT task_type, COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    query, params = _append_log_type_filter(query, params, log_type)
    query += " GROUP BY task_type ORDER BY total_ipus DESC"
    tasktype_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return tasktype_stats


def get_status_stats_by_date_range(start_date: str, end_date: str,
                                   log_type: str = None) -> pd.DataFrame:
    """Get statistics by status for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT status, COUNT(*) AS task_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS total_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS total_cost "
        "FROM tasks WHERE end_time >= ? AND end_time <= ?"
    )
    params = ipu_params + cost_params + [f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    query, params = _append_log_type_filter(query, params, log_type)
    query += " GROUP BY status ORDER BY total_ipus DESC"
    status_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return status_stats


def detect_anomalies_in_date_range(start_date: str, end_date: str,
                                    metric: str = 'total_ipus',
                                    threshold_std: float = 2.0,
                                    org: str = None,
                                    log_type: str = None) -> pd.DataFrame:
    """
    Detect anomalies in daily metrics across a date range.
    
    Args:
        start_date: ISO format date string
        end_date: ISO format date string
        metric: 'total_ipus', 'total_cost', or 'task_count'
        threshold_std: Standard deviations for anomaly threshold
        org: Optional org to filter by
        log_type: Optional log type to filter by
    
    Returns:
        DataFrame with anomalous days
    """
    daily_stats = get_daily_stats_by_date_range(start_date, end_date, org=org, log_type=log_type)
    
    if daily_stats.empty or len(daily_stats) < 3:
        return pd.DataFrame()
    
    # Calculate z-scores
    mean = daily_stats[metric].mean()
    std = daily_stats[metric].std()
    
    if std == 0:
        return pd.DataFrame()
    
    daily_stats['z_score'] = (daily_stats[metric] - mean) / std
    daily_stats['is_anomaly'] = daily_stats['z_score'].abs() > threshold_std
    daily_stats['anomaly_type'] = daily_stats.apply(
        lambda row: 'High' if row['z_score'] > threshold_std else 'Low' if row['z_score'] < -threshold_std else 'Normal',
        axis=1
    )
    
    return daily_stats[daily_stats['is_anomaly']]


def get_task_spikes_for_period(
    end_date: str,
    lookback_days: int = 90,
    baseline_days: int = 90,
    threshold_std: float = 3.0,
    min_baseline_days: int = 5,
    top_n: int = 10,
    org: str = None,
    log_type: str = None,
) -> pd.DataFrame:
    """Find task-level daily IPU spikes in the current window vs prior baseline.

    The function compares per-task daily IPU totals in the current window
    (`lookback_days`) against each task's baseline behavior in the immediately
    preceding `baseline_days`.
    """
    init_database()
    conn = sqlite3.connect(DB_PATH)

    end_dt = pd.to_datetime(end_date).date()
    current_start = end_dt - timedelta(days=lookback_days - 1)
    baseline_end = current_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=baseline_days - 1)

    ipu_expr, ipu_params = _effective_ipu_sql()
    cost_expr, cost_params = _effective_cost_sql()

    query = (
        "SELECT DATE(end_time) AS task_date, "
        "task_name, task_id, org, project_name, "
        "COUNT(*) AS run_count, "
        "COALESCE(SUM(" + ipu_expr + "), 0) AS daily_ipus, "
        "COALESCE(SUM(" + cost_expr + "), 0) AS daily_cost "
        "FROM tasks "
        "WHERE end_time >= ? AND end_time <= ? "
        "AND task_name IS NOT NULL AND TRIM(task_name) <> '' "
    )
    params = ipu_params + cost_params + [
        f'{baseline_start.isoformat()} 00:00:00',
        f'{end_dt.isoformat()} 23:59:59',
    ]
    if org:
        query += " AND org = ?"
        params.append(org)
    query, params = _append_log_type_filter(query, params, log_type)

    query += " GROUP BY DATE(end_time), task_name, task_id, org, project_name"

    all_daily = pd.read_sql_query(query, conn, params=params)
    conn.close()

    if all_daily.empty:
        return pd.DataFrame()

    all_daily['task_date'] = pd.to_datetime(all_daily['task_date'], errors='coerce')
    all_daily = all_daily.dropna(subset=['task_date'])

    baseline_mask = (
        (all_daily['task_date'].dt.date >= baseline_start)
        & (all_daily['task_date'].dt.date <= baseline_end)
    )
    current_mask = (
        (all_daily['task_date'].dt.date >= current_start)
        & (all_daily['task_date'].dt.date <= end_dt)
    )

    baseline = all_daily[baseline_mask].copy()
    current = all_daily[current_mask].copy()

    if baseline.empty or current.empty:
        return pd.DataFrame()

    baseline_stats = baseline.groupby('task_name', dropna=False).agg(
        baseline_days=('daily_ipus', 'count'),
        baseline_mean_ipus=('daily_ipus', 'mean'),
        baseline_std_ipus=('daily_ipus', 'std'),
    ).reset_index()

    baseline_stats['baseline_std_ipus'] = baseline_stats['baseline_std_ipus'].fillna(0.0)

    merged = current.merge(baseline_stats, on='task_name', how='left')
    merged = merged[merged['baseline_days'] >= int(min_baseline_days)].copy()

    if merged.empty:
        return pd.DataFrame()

    merged['baseline_threshold'] = (
        merged['baseline_mean_ipus'] + threshold_std * merged['baseline_std_ipus']
    )

    merged['z_score'] = 0.0
    nonzero_std = merged['baseline_std_ipus'] > 0
    merged.loc[nonzero_std, 'z_score'] = (
        (merged.loc[nonzero_std, 'daily_ipus'] - merged.loc[nonzero_std, 'baseline_mean_ipus'])
        / merged.loc[nonzero_std, 'baseline_std_ipus']
    )
    merged.loc[~nonzero_std, 'z_score'] = (
        (merged.loc[~nonzero_std, 'daily_ipus'] > merged.loc[~nonzero_std, 'baseline_mean_ipus'])
    ).astype(float) * 99.0

    merged['multiplier_vs_baseline'] = merged['daily_ipus'] / merged['baseline_mean_ipus'].replace(0, pd.NA)
    merged['multiplier_vs_baseline'] = merged['multiplier_vs_baseline'].fillna(0.0)

    spikes = merged[
        (merged['daily_ipus'] > merged['baseline_threshold'])
        & (merged['daily_ipus'] > merged['baseline_mean_ipus'] * 1.5)
    ].copy()

    if spikes.empty:
        return pd.DataFrame()

    spikes = spikes.sort_values(['z_score', 'daily_ipus'], ascending=[False, False])
    keep_cols = [
        'task_date', 'task_name', 'task_id', 'org', 'project_name', 'run_count',
        'daily_ipus', 'daily_cost', 'baseline_days', 'baseline_mean_ipus',
        'baseline_std_ipus', 'baseline_threshold', 'z_score', 'multiplier_vs_baseline'
    ]

    return spikes[keep_cols].head(int(top_n)).reset_index(drop=True)



def delete_run(run_id: int) -> bool:
    """Delete a run and all its associated data."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Delete from all related tables
        cursor.execute('DELETE FROM daily_stats WHERE run_id = ?', (run_id,))
        cursor.execute('DELETE FROM org_summaries WHERE run_id = ?', (run_id,))
        cursor.execute('DELETE FROM env_summaries WHERE run_id = ?', (run_id,))
        cursor.execute('DELETE FROM project_summaries WHERE run_id = ?', (run_id,))
        cursor.execute('DELETE FROM task_type_summaries WHERE run_id = ?', (run_id,))
        cursor.execute('DELETE FROM status_summaries WHERE run_id = ?', (run_id,))
        cursor.execute('DELETE FROM runs WHERE run_id = ?', (run_id,))
        
        conn.commit()
        rows_affected = cursor.rowcount
        conn.close()
        
        return rows_affected > 0
    except Exception as e:
        print(f"Error deleting run: {e}")
        conn.close()
        return False


def debug_check_runs() -> pd.DataFrame:
    """Debug function to check what's in the runs table."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM runs')
    columns = [description[0] for description in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    
    if rows:
        return pd.DataFrame(rows, columns=columns)
    return pd.DataFrame()
