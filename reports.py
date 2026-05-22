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
            task_type TEXT,
            task_run_id TEXT,
            row_hash TEXT,
            project_name TEXT,
            folder_name TEXT,
            org TEXT,
            environment TEXT,
            status TEXT,
            start_time DATETIME,
            end_time DATETIME,
            ipus REAL,
            cost REAL,
            metered_value REAL,
            cores_used REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create indices for efficient queries
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_start_time ON tasks(start_time)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_org ON tasks(org)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_project ON tasks(project_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_environment ON tasks(environment)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_task_type ON tasks(task_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)')

    # If an existing DB was created before row_hash existed, try to add the column
    cursor.execute("PRAGMA table_info(tasks)")
    cols = [r[1] for r in cursor.fetchall()]
    if 'row_hash' not in cols:
        try:
            cursor.execute('ALTER TABLE tasks ADD COLUMN row_hash TEXT')
        except Exception:
            # Some SQLite versions or locks may prevent altering; ignore if it fails
            pass

    # Ensure unique index on row_hash to avoid inserting identical rows
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_row_hash ON tasks(row_hash)')
    
    conn.commit()
    conn.close()


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


def save_run(merged_df: pd.DataFrame) -> tuple[int, int]:
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
        return 0, total_rows

    # Only stable task identity belongs in the dedupe hash.
    # Derived/calculated fields (Run Date, IPUs, Cost/IPU/Month, etc.) and
    # mutable snapshot fields (like Status) are intentionally excluded so
    # re-uploading the same task data does not create a new hash.
    hash_columns = [
        'Task ID', 'Task Run ID', 'Task Name', 'Task Type', 'Project Name',
        'Folder Name', 'Org', 'Environment', 'Start Time', 'End Time',
        'Metered Value',
    ]
    available_columns = [col for col in hash_columns if col in merged_df.columns]

    if not available_columns:
        cursor.execute('SELECT COUNT(*) FROM tasks')
        total_rows = cursor.fetchone()[0]
        conn.close()
        return 0, total_rows

    def _compute_row_hash(row, cols):
        parts = []
        for c in cols:
            v = row.get(c) if hasattr(row, 'get') else row[c]
            if pd.isna(v):
                s = ''
            elif isinstance(v, (float, int)):
                s = format(v, '.12g')
            else:
                s = str(v)
            parts.append(s)
        concat = '|'.join(parts)
        return hashlib.sha256(concat.encode('utf-8')).hexdigest()

    norm_df = merged_df[available_columns].copy()

    # Normalize datetimes and numerics (same as before)
    for dt_col in ['Start Time', 'End Time', 'Start DateTime']:
        if dt_col in norm_df.columns:
            norm_df[dt_col] = pd.to_datetime(norm_df[dt_col], errors='coerce')
            norm_df[dt_col] = norm_df[dt_col].dt.strftime('%Y-%m-%d %H:%M:%S')
            norm_df[dt_col] = norm_df[dt_col].fillna('')

    for num_col in ['IPUs', 'Cost/IPU/Month', 'Metered Value', 'Cores Used']:
        if num_col in norm_df.columns:
            norm_df[num_col] = pd.to_numeric(norm_df[num_col], errors='coerce')
            norm_df[num_col] = norm_df[num_col].round(6)
            norm_df[num_col] = norm_df[num_col].fillna(0)

    # Compute row hash (vectorized if possible, otherwise chunked)
    total_rows = len(norm_df)
    try:
        from pandas.util import hash_pandas_object
        progress_callback(5, 'Computing vectorized row hashes...')
        hash_series = hash_pandas_object(norm_df[available_columns], index=False).astype('uint64')
        norm_df['row_hash'] = hash_series.astype(str)
        progress_callback(10, 'Row hashes computed (vectorized)')
    except Exception:
        # Fall back to chunked row-wise hashing with progress updates
        logger.info('Vectorized hashing unavailable, falling back to chunked hashing')
        chunk_hash = 5000
        norm_df['row_hash'] = ''
        for start in range(0, total_rows, chunk_hash):
            end = min(start + chunk_hash, total_rows)
            subset = norm_df.iloc[start:end]
            hashes = subset.apply(lambda r: _compute_row_hash(r, available_columns), axis=1)
            norm_df.loc[subset.index, 'row_hash'] = hashes.values
            pct = int(10 + (end / total_rows) * 20)
            progress_callback(pct, f'Computed hashes for rows {start}:{end}')

    # Prepare DataFrame for DB insert
    col_map = {
        'Task ID': 'task_id',
        'Task Name': 'task_name',
        'Task Type': 'task_type',
        'Task Run ID': 'task_run_id',
        'Project Name': 'project_name',
        'Folder Name': 'folder_name',
        'Org': 'org',
        'Environment': 'environment',
        'Status': 'status',
        'Start Time': 'start_time',
        'End Time': 'end_time',
        'IPUs': 'ipus',
        'Cost/IPU/Month': 'cost',
        'Metered Value': 'metered_value',
        'Cores Used': 'cores_used',
        'row_hash': 'row_hash',
    }

    # Use a wider set of source columns for storage than we use for dedupe hashing.
    # Hash should be stable on task identity; persisted record should retain metrics.
    insert_source_columns = [
        'Task ID', 'Task Name', 'Task Type', 'Task Run ID',
        'Project Name', 'Folder Name', 'Org', 'Environment', 'Status',
        'Start Time', 'End Time', 'IPUs', 'Cost/IPU/Month', 'Metered Value', 'Cores Used'
    ]
    present_source_columns = [col for col in insert_source_columns if col in merged_df.columns]

    staging_df = merged_df[present_source_columns].copy()

    for dt_col in ['Start Time', 'End Time']:
        if dt_col in staging_df.columns:
            staging_df[dt_col] = pd.to_datetime(staging_df[dt_col], errors='coerce')
            staging_df[dt_col] = staging_df[dt_col].dt.strftime('%Y-%m-%d %H:%M:%S')
            staging_df[dt_col] = staging_df[dt_col].fillna('')

    for num_col in ['IPUs', 'Cost/IPU/Month', 'Metered Value', 'Cores Used']:
        if num_col in staging_df.columns:
            staging_df[num_col] = pd.to_numeric(staging_df[num_col], errors='coerce')
            staging_df[num_col] = staging_df[num_col].round(6)

    staging_df['row_hash'] = norm_df['row_hash'].values
    staging_df = staging_df.rename(columns=col_map)

    # Determine insert columns that actually exist
    insert_cols = [
        col for col in [
            'task_id', 'task_name', 'task_type', 'task_run_id', 'row_hash',
            'project_name', 'folder_name', 'org', 'environment', 'status',
            'start_time', 'end_time', 'ipus', 'cost', 'metered_value', 'cores_used'
        ] if col in staging_df.columns
    ]

    # Count before
    cursor.execute('SELECT COUNT(*) FROM tasks')
    before_count = cursor.fetchone()[0]

    # Perform chunked INSERT OR IGNORE
    placeholders = ','.join(['?'] * len(insert_cols))
    insert_sql = f"INSERT OR IGNORE INTO tasks ({', '.join(insert_cols)}) VALUES ({placeholders})"

    # Convert to list of tuples for executemany (fill NaNs with None)
    tuples = []
    for row in staging_df[insert_cols].itertuples(index=False, name=None):
        tuples.append(tuple(None if (pd.isna(x) or (isinstance(x, str) and x == '')) else x for x in row))

    total_to_insert = len(tuples)
    if total_to_insert == 0:
        progress_callback(100, 'No rows to insert')
        cursor.execute('SELECT COUNT(*) FROM tasks')
        after_count = cursor.fetchone()[0]
        conn.close()
        return 0, after_count

    chunk_size = 5000
    processed = 0
    conn.execute('BEGIN')
    try:
        for start in range(0, total_to_insert, chunk_size):
            end = min(start + chunk_size, total_to_insert)
            batch = tuples[start:end]
            cursor.executemany(insert_sql, batch)
            processed += len(batch)
            pct = int(10 + (processed / total_to_insert) * 80)
            progress_callback(pct, f'Inserted {processed:,}/{total_to_insert:,} staging rows')
            logger.info(f'Inserted batch rows {start}:{end} ({len(batch)} rows)')
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.exception('Error during chunked insert')
        raise

    # Final counts
    cursor.execute('SELECT COUNT(*) FROM tasks')
    after_count = cursor.fetchone()[0]
    rows_added = after_count - before_count

    progress_callback(100, f'Finished. {rows_added} new rows added, {after_count} total rows')
    logger.info(f'Finished save_run: {rows_added} new rows, {after_count} total')

    conn.close()

    return rows_added, after_count



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
# NEW TIME-SERIES ANALYSIS FUNCTIONS (by task start date, not run date)
# ============================================================================

def get_task_date_range() -> tuple:
    """Get the min and max start dates from all tasks in the database."""
    init_database()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT MIN(start_time), MAX(start_time) FROM tasks WHERE start_time IS NOT NULL')
    result = cursor.fetchone()
    conn.close()
    
    if result[0] is None:
        return (None, None)
    
    return (result[0], result[1])


def get_tasks_by_date_range(start_date: str, end_date: str, 
                            org: str = None, project: str = None,
                            environment: str = None, task_type: str = None,
                            status: str = None) -> pd.DataFrame:
    """
    Get task records filtered by date range and optional dimensions.
    
    Args:
        start_date: ISO format date string (YYYY-MM-DD)
        end_date: ISO format date string (YYYY-MM-DD)
        org: Filter by organization (optional)
        project: Filter by project name (optional)
        environment: Filter by environment (optional)
        task_type: Filter by task type (optional)
        status: Filter by status (optional)
    
    Returns:
        DataFrame of task records
    """
    init_database()
    conn = sqlite3.connect(DB_PATH)
    
    query = 'SELECT * FROM tasks WHERE start_time >= ? AND start_time <= ?'
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
    
    query += ' ORDER BY start_time'
    
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    
    return df


def get_daily_stats_by_date_range(start_date: str, end_date: str,
                                   org: str = None, project: str = None,
                                   environment: str = None) -> pd.DataFrame:
    """
    Get daily aggregated statistics for a date range.
    
    Returns DataFrame with columns: date, task_count, total_ipus, total_cost
    """
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_factor = float(calculations.IPU_CONVERSION_FACTOR)
    cost_per_ipu = float(calculations.COST_PER_IPU_MONTH)

    query = (
        "SELECT DATE(start_time) AS date, "
        "COUNT(*) AS task_count, "
        "COALESCE(SUM(COALESCE(ipus, COALESCE(metered_value, 0) * ?)), 0) AS total_ipus, "
        "COALESCE(SUM(COALESCE(cost, COALESCE(ipus, COALESCE(metered_value, 0) * ?) * ?)), 0) AS total_cost "
        "FROM tasks "
        "WHERE start_time >= ? AND start_time <= ?"
    )
    params = [ipu_factor, ipu_factor, cost_per_ipu, f'{start_date} 00:00:00', f'{end_date} 23:59:59']

    if org:
        query += ' AND org = ?'
        params.append(org)
    if project:
        query += ' AND project_name = ?'
        params.append(project)
    if environment:
        query += ' AND environment = ?'
        params.append(environment)

    query += ' GROUP BY DATE(start_time) ORDER BY DATE(start_time)'

    daily = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return daily


def get_org_stats_by_date_range(start_date: str, end_date: str) -> pd.DataFrame:
    """Get statistics by organization for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_factor = float(calculations.IPU_CONVERSION_FACTOR)
    cost_per_ipu = float(calculations.COST_PER_IPU_MONTH)

    query = (
        "SELECT org, COUNT(*) AS task_count, "
        "COALESCE(SUM(COALESCE(ipus, COALESCE(metered_value, 0) * ?)), 0) AS total_ipus, "
        "COALESCE(SUM(COALESCE(cost, COALESCE(ipus, COALESCE(metered_value, 0) * ?) * ?)), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE start_time >= ? AND start_time <= ? "
        "GROUP BY org ORDER BY total_ipus DESC"
    )
    params = [ipu_factor, ipu_factor, cost_per_ipu, f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    org_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return org_stats


def get_project_stats_by_date_range(start_date: str, end_date: str) -> pd.DataFrame:
    """Get statistics by project for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_factor = float(calculations.IPU_CONVERSION_FACTOR)
    cost_per_ipu = float(calculations.COST_PER_IPU_MONTH)

    query = (
        "SELECT project_name, COUNT(*) AS task_count, "
        "COALESCE(SUM(COALESCE(ipus, COALESCE(metered_value, 0) * ?)), 0) AS total_ipus, "
        "COALESCE(SUM(COALESCE(cost, COALESCE(ipus, COALESCE(metered_value, 0) * ?) * ?)), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE start_time >= ? AND start_time <= ? "
        "GROUP BY project_name ORDER BY total_ipus DESC"
    )
    params = [ipu_factor, ipu_factor, cost_per_ipu, f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    project_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return project_stats


def get_environment_stats_by_date_range(start_date: str, end_date: str) -> pd.DataFrame:
    """Get statistics by environment for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_factor = float(calculations.IPU_CONVERSION_FACTOR)
    cost_per_ipu = float(calculations.COST_PER_IPU_MONTH)

    query = (
        "SELECT environment, COUNT(*) AS task_count, "
        "COALESCE(SUM(COALESCE(ipus, COALESCE(metered_value, 0) * ?)), 0) AS total_ipus, "
        "COALESCE(SUM(COALESCE(cost, COALESCE(ipus, COALESCE(metered_value, 0) * ?) * ?)), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE start_time >= ? AND start_time <= ? "
        "GROUP BY environment ORDER BY total_ipus DESC"
    )
    params = [ipu_factor, ipu_factor, cost_per_ipu, f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    env_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return env_stats


def get_task_type_stats_by_date_range(start_date: str, end_date: str) -> pd.DataFrame:
    """Get statistics by task type for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_factor = float(calculations.IPU_CONVERSION_FACTOR)
    cost_per_ipu = float(calculations.COST_PER_IPU_MONTH)

    query = (
        "SELECT task_type, COUNT(*) AS task_count, "
        "COALESCE(SUM(COALESCE(ipus, COALESCE(metered_value, 0) * ?)), 0) AS total_ipus, "
        "COALESCE(SUM(COALESCE(cost, COALESCE(ipus, COALESCE(metered_value, 0) * ?) * ?)), 0) AS total_cost, "
        "COUNT(DISTINCT task_id) AS unique_tasks "
        "FROM tasks WHERE start_time >= ? AND start_time <= ? "
        "GROUP BY task_type ORDER BY total_ipus DESC"
    )
    params = [ipu_factor, ipu_factor, cost_per_ipu, f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    tasktype_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return tasktype_stats


def get_status_stats_by_date_range(start_date: str, end_date: str) -> pd.DataFrame:
    """Get statistics by status for a date range."""
    init_database()
    conn = sqlite3.connect(DB_PATH)

    ipu_factor = float(calculations.IPU_CONVERSION_FACTOR)
    cost_per_ipu = float(calculations.COST_PER_IPU_MONTH)

    query = (
        "SELECT status, COUNT(*) AS task_count, "
        "COALESCE(SUM(COALESCE(ipus, COALESCE(metered_value, 0) * ?)), 0) AS total_ipus, "
        "COALESCE(SUM(COALESCE(cost, COALESCE(ipus, COALESCE(metered_value, 0) * ?) * ?)), 0) AS total_cost "
        "FROM tasks WHERE start_time >= ? AND start_time <= ? "
        "GROUP BY status ORDER BY total_ipus DESC"
    )
    params = [ipu_factor, ipu_factor, cost_per_ipu, f'{start_date} 00:00:00', f'{end_date} 23:59:59']
    status_stats = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return status_stats


def detect_anomalies_in_date_range(start_date: str, end_date: str,
                                    metric: str = 'total_ipus',
                                    threshold_std: float = 2.0,
                                    org: str = None) -> pd.DataFrame:
    """
    Detect anomalies in daily metrics across a date range.
    
    Args:
        start_date: ISO format date string
        end_date: ISO format date string
        metric: 'total_ipus', 'total_cost', or 'task_count'
        threshold_std: Standard deviations for anomaly threshold
        org: Optional org to filter by
    
    Returns:
        DataFrame with anomalous days
    """
    daily_stats = get_daily_stats_by_date_range(start_date, end_date, org=org)
    
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

    ipu_factor = float(calculations.IPU_CONVERSION_FACTOR)
    cost_per_ipu = float(calculations.COST_PER_IPU_MONTH)

    query = (
        "SELECT DATE(start_time) AS task_date, "
        "task_name, task_id, org, project_name, "
        "COUNT(*) AS run_count, "
        "COALESCE(SUM(COALESCE(ipus, COALESCE(metered_value, 0) * ?)), 0) AS daily_ipus, "
        "COALESCE(SUM(COALESCE(cost, COALESCE(ipus, COALESCE(metered_value, 0) * ?) * ?)), 0) AS daily_cost "
        "FROM tasks "
        "WHERE start_time >= ? AND start_time <= ? "
        "AND task_name IS NOT NULL AND TRIM(task_name) <> '' "
        "GROUP BY DATE(start_time), task_name, task_id, org, project_name"
    )
    params = [
        ipu_factor,
        ipu_factor,
        cost_per_ipu,
        f'{baseline_start.isoformat()} 00:00:00',
        f'{end_dt.isoformat()} 23:59:59',
    ]

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
