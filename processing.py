"""
Data processing and normalization functions for Informatica usage files.

Handles reading Excel files, normalizing column names, merging dataframes,
and creating derived columns. All operations optimized for performance
with large datasets (500k+ rows).
"""

import pandas as pd
import numpy as np
from datetime import datetime
from calculations import calculate_ipus, calculate_cost_per_ipu_month
from mappings import get_org_name


def _to_datetime_mixed(values):
    return pd.to_datetime(values, errors='coerce', format='mixed')


# Standard column names expected in output
STANDARD_COLUMNS = [
    'Task ID',
    'Task Name',
    'Task Object Name',
    'Task Type',
    'Task Run ID',
    'Project Name',
    'Folder Name',
    'Org ID',
    'Environment ID',
    'Environment',
    'Cores Used',
    'Start Time',
    'End Time',
    'Status',
    'Metered Value',
    'Audit Time',
    'OBM Task Time(s)',
    'Org',
    'Run Date',
    'IPUs',
    'Cost/IPU/Month'
]


def normalize_column_names(df):
    """
    Normalize column names to standard format.
    
    Removes extra whitespace, handles common variations,
    and ensures consistency across different file sources.
    
    Args:
        df: pandas DataFrame with potentially inconsistent column names
        
    Returns:
        DataFrame with normalized column names
    """
    # Strip whitespace from column names
    df.columns = df.columns.str.strip()
    
    # Create a mapping of common variations to standard names
    column_mapping = {
        'taskid': 'Task ID',
        'task id': 'Task ID',
        'taskname': 'Task Name',
        'task name': 'Task Name',
        'taskobjectname': 'Task Object Name',
        'task object name': 'Task Object Name',
        'tasktype': 'Task Type',
        'task type': 'Task Type',
        'taskrunid': 'Task Run ID',
        'task run id': 'Task Run ID',
        'projectname': 'Project Name',
        'project name': 'Project Name',
        'foldername': 'Folder Name',
        'folder name': 'Folder Name',
        'orgid': 'Org ID',
        'org id': 'Org ID',
        'environmentid': 'Environment ID',
        'environment id': 'Environment ID',
        'coresused': 'Cores Used',
        'cores used': 'Cores Used',
        'starttime': 'Start Time',
        'start time': 'Start Time',
        'endtime': 'End Time',
        'end time': 'End Time',
        'meteredvalue': 'Metered Value',
        'metered value': 'Metered Value',
        'audittime': 'Audit Time',
        'audit time': 'Audit Time',
        'obmtasktime(s)': 'OBM Task Time(s)',
        'obm task time(s)': 'OBM Task Time(s)',
    }
    
    # Create lowercase version of column names for mapping
    lowercase_cols = {col.lower(): col for col in df.columns}
    
    # Apply mappings
    rename_dict = {}
    for lower_name, standard_name in column_mapping.items():
        if lower_name in lowercase_cols:
            rename_dict[lowercase_cols[lower_name]] = standard_name
    
    df = df.rename(columns=rename_dict)
    
    return df


def read_excel_file(file_path):
    """
    Read an Excel file and return a pandas DataFrame.
    
    Handles multiple sheets (reads first sheet with data),
    skips empty rows, and manages common Excel issues.
    
    Args:
        file_path: Path to Excel file or file-like object
        
    Returns:
        DataFrame with data from Excel file, or None if error
    """
    try:
        # Read the first sheet
        df = pd.read_excel(file_path, engine='openpyxl')
        
        # Remove completely empty rows
        df = df.dropna(how='all')
        
        return df
    except Exception as e:
        raise ValueError(f"Error reading file: {str(e)}")


def read_csv_file(file_path):
    """
    Read a CSV file and return a pandas DataFrame.
    
    Args:
        file_path: Path to CSV file or file-like object
        
    Returns:
        DataFrame with data from CSV file
    """
    try:
        df = pd.read_csv(file_path)
        
        # Remove completely empty rows
        df = df.dropna(how='all')
        
        return df
    except Exception as e:
        raise ValueError(f"Error reading file: {str(e)}")


def merge_dataframes(dataframes_list):
    """
    Merge multiple DataFrames into a single DataFrame.
    
    Uses efficient concatenation and preserves data types
    when possible. Uses inplace operations to minimize memory.
    
    Args:
        dataframes_list: List of pandas DataFrames to merge
        
    Returns:
        Merged DataFrame with all rows from input dataframes
    """
    if not dataframes_list:
        return pd.DataFrame()
    
    if len(dataframes_list) == 1:
        return dataframes_list[0].copy()
    
    # Use efficient concatenation
    # ignore_index=False to preserve original row indices (they will be reset)
    merged_df = pd.concat(dataframes_list, ignore_index=True)
    
    return merged_df


def add_run_date_column(df):
    """
    Add Run Date column and a parsed `Start DateTime` when possible.

    Attempts to parse `Start Time` into a full datetime. If `Start Time`
    contains only a time (no date), the function will try to use the date
    part from `Audit Time` or `End Time` to build a complete datetime.

    `Run Date` intentionally represents the date this consolidation is run
    (import + calculations), not the task execution date from source logs.
    
    Args:
        df: DataFrame to add Run Date to
        
    Returns:
        DataFrame with new 'Run Date' column added
    """
    import_run_date = datetime.now().date()

    # Work with string representations
    if 'Start Time' in df.columns:
        start_raw = df['Start Time'].astype(str)

        # Initial parse (may result in today's date if input was time-only)
        start_dt = _to_datetime_mixed(start_raw)

        # Detect strings that look like time-only (e.g. "45:10.0", "12:34:56")
        time_only_mask = start_raw.str.match(r"^\s*\d{1,2}(?::\d{2}){1,2}(?:\.\d+)?\s*$")

        # Also ensure there is no obvious date segment (no slash, dash, or 4-digit year)
        no_date_mask = ~start_raw.str.contains(r"[/\-]|\d{4}")

        mask_time_only = time_only_mask & no_date_mask

        # Try to find a date source (Audit Time, End Time)
        date_source_cols = [col for col in ['Audit Time', 'End Time'] if col in df.columns]

        for col in date_source_cols:
            if mask_time_only.any():
                src_dt = _to_datetime_mixed(df.loc[mask_time_only, col])
                have_date = src_dt.notna()
                if have_date.any():
                    # Combine date from src_dt with time from start_raw
                    time_vals = start_raw[mask_time_only][have_date]
                    combined = _to_datetime_mixed(src_dt.dt.date.astype(str) + ' ' + time_vals)
                    start_dt.loc[mask_time_only[mask_time_only].index[have_date.index]] = combined

        # Ensure we add a Start DateTime column
        df['Start DateTime'] = start_dt

        # Run Date is always the date the import/consolidation is executed.
        df['Run Date'] = import_run_date
        
        return df

    # If no Start Time is present, still stamp with import/consolidation date.
    df['Run Date'] = import_run_date

    return df


def add_org_column(df):
    """
    Add Org column by mapping Org ID.
    
    Uses the org ID mapping from mappings module.
    Handles missing values gracefully.
    
    Args:
        df: DataFrame that should have 'Org ID' column
        
    Returns:
        DataFrame with new 'Org' column added
    """
    if 'Org ID' not in df.columns:
        df['Org'] = 'Unknown'
        return df
    
    # Use vectorized apply for performance with large datasets
    df['Org'] = df['Org ID'].astype(str).apply(get_org_name)
    
    return df


def add_ipu_column(df):
    """
    Add IPUs column calculated from Metered Value.
    
    Uses vectorized calculation for performance.
    
    Args:
        df: DataFrame that should have 'Metered Value' column
        
    Returns:
        DataFrame with new 'IPUs' column added
    """
    if 'Metered Value' not in df.columns:
        df['IPUs'] = 0.0
        return df
    
    df['IPUs'] = calculate_ipus(df['Metered Value'])
    
    return df


def add_cost_column(df):
    """
    Add Cost/IPU/Month column calculated from IPUs.
    
    Uses vectorized calculation for performance.
    
    Args:
        df: DataFrame that should have 'IPUs' column
        
    Returns:
        DataFrame with new 'Cost/IPU/Month' column added
    """
    if 'IPUs' not in df.columns:
        df['Cost/IPU/Month'] = 0.0
        return df
    
    df['Cost/IPU/Month'] = calculate_cost_per_ipu_month(df['IPUs'])
    
    return df


def process_and_merge_files(uploaded_files, org_assignments=None):
    """
    Master function to process and merge multiple uploaded files.
    
    Orchestrates the entire pipeline:
    1. Read all Excel/CSV files
    2. Normalize column names
    3. Assign org per file
    4. Merge into single DataFrame
    5. Add derived columns
    6. Handle errors gracefully
    
    Args:
        uploaded_files: List of uploaded file objects from Streamlit
        org_assignments: Dict mapping file names to org values {filename: org}
        
    Returns:
        Tuple of (merged_df, error_messages)
        merged_df: Combined DataFrame or empty if all failed
        error_messages: List of error strings from processing
    """
    if org_assignments is None:
        org_assignments = {}
    
    dataframes = []
    errors = []
    
    for uploaded_file in uploaded_files:
        try:
            # Determine file type and read accordingly
            if uploaded_file.name.endswith('.csv'):
                df = read_csv_file(uploaded_file)
            else:
                df = read_excel_file(uploaded_file)
            
            # Normalize column names
            df = normalize_column_names(df)
            
            # Assign org for this file
            org = org_assignments.get(uploaded_file.name, 'Unknown')
            df['Org'] = org
            
            # Keep only standard columns that exist in this file
            existing_cols = [col for col in df.columns if col in STANDARD_COLUMNS]
            df = df[existing_cols]
            
            dataframes.append(df)
            
        except Exception as e:
            errors.append(f"Error processing {uploaded_file.name}: {str(e)}")
    
    # Merge all successfully read files
    if dataframes:
        merged_df = merge_dataframes(dataframes)
        
        # Add derived columns
        merged_df = add_run_date_column(merged_df)
        merged_df = add_ipu_column(merged_df)
        merged_df = add_cost_column(merged_df)
        
        # Reorder columns with calculated ones at the end
        cols_order = [col for col in STANDARD_COLUMNS if col in merged_df.columns]
        merged_df = merged_df[cols_order]
        
        return merged_df, errors
    else:
        return pd.DataFrame(), errors


def get_duplicate_task_run_ids(df):
    """
    Find and return all duplicate Task Run IDs.
    
    Args:
        df: DataFrame with 'Task Run ID' column
        
    Returns:
        DataFrame containing only rows with duplicate Task Run IDs
    """
    # Consider a duplicate only if the entire row is identical.
    if df.empty:
        return pd.DataFrame()

    # Replace NaNs with a sentinel so identical NaNs count as equal
    df_cmp = df.fillna('__NaN__')

    # Convert all columns to string to ensure consistent comparison
    df_str = df_cmp.astype(str)

    # Find duplicated rows (keep=False to mark all copies)
    dup_mask = df_str.duplicated(keep=False)

    duplicates = df[dup_mask]

    if duplicates.empty:
        return pd.DataFrame(columns=df.columns)

    # Return duplicates in the original column order
    return duplicates.sort_index()


def get_failed_task_counts(df):
    """
    Get count of failed tasks grouped by Status.
    
    Args:
        df: DataFrame with 'Status' column
        
    Returns:
        pandas Series with status counts
    """
    if 'Status' not in df.columns or df.empty:
        return pd.Series()
    
    # Count by status
    status_counts = df['Status'].value_counts()
    
    return status_counts


def get_summary_by_group(df, group_by_column):
    """
    Create summary statistics grouped by specified column.
    
    Includes row count, total IPUs, total cost, and basic stats.
    
    Args:
        df: DataFrame to summarize
        group_by_column: Column name to group by
        
    Returns:
        DataFrame with summary statistics
    """
    if group_by_column not in df.columns or df.empty:
        return pd.DataFrame()

    # Ensure numeric columns are numeric to avoid aggregation issues
    for col in ['IPUs', 'Cost/IPU/Month', 'Cores Used']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Build aggregation dict dynamically so missing columns don't break the result
    agg_dict = {
        'Task Run ID': 'count',
        'IPUs': ['sum', 'mean'],
        'Cost/IPU/Month': ['sum', 'mean'],
    }

    if 'Cores Used' in df.columns:
        agg_dict['Cores Used'] = ['sum', 'mean']

    summary = df.groupby(group_by_column).agg(agg_dict).round(6)

    # Flatten multiindex columns and name them according to what's present
    new_cols = []
    for col in summary.columns:
        if isinstance(col, tuple):
            new_cols.append(f"{col[0]}_{col[1]}")
        else:
            new_cols.append(col)

    summary.columns = new_cols

    # Standardize column names for compatibility with the UI expectations
    rename_map = {
        'Task Run ID_count': 'Task Count',
        'IPUs_sum': 'Total IPUs',
        'IPUs_mean': 'Avg IPUs',
        'Cost/IPU/Month_sum': 'Total Cost',
        'Cost/IPU/Month_mean': 'Avg Cost',
        'Cores Used_sum': 'Total Cores',
        'Cores Used_mean': 'Avg Cores',
    }

    summary = summary.rename(columns={k: v for k, v in rename_map.items() if k in summary.columns})

    return summary.reset_index()


def get_pivot_summary(df, group_cols=None):
    """
    Produce a simple pivot-style summary matching Excel: sums of IPUs and Cost per grouping.

    Args:
        df: DataFrame
        group_cols: list of columns to group by (defaults to ['Org'])

    Returns:
        DataFrame with summed IPUs and Cost columns
    """
    if group_cols is None:
        group_cols = ['Org']

    for col in ['IPUs', 'Cost/IPU/Month']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    pivot = df.groupby(group_cols).agg({'IPUs': 'sum', 'Cost/IPU/Month': 'sum'}).round(6).reset_index()
    return pivot


def export_to_excel(df, file_path):
    """
    Export DataFrame to Excel file.
    
    Args:
        df: DataFrame to export
        file_path: Output file path
        
    Returns:
        True if successful, False otherwise
    """
    try:
        df.to_excel(file_path, index=False, engine='openpyxl')
        return True
    except Exception as e:
        raise ValueError(f"Error exporting to Excel: {str(e)}")


def export_to_csv(df, file_path):
    """
    Export DataFrame to CSV file.
    
    Args:
        df: DataFrame to export
        file_path: Output file path
        
    Returns:
        True if successful, False otherwise
    """
    try:
        df.to_csv(file_path, index=False)
        return True
    except Exception as e:
        raise ValueError(f"Error exporting to CSV: {str(e)}")
