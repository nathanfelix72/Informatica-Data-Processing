"""
Streamlit web application for consolidating Informatica usage spreadsheets.

Features:
- Upload multiple Excel files with drag-and-drop support
- Automatically normalize and merge data
- Calculate IPUs and costs
- Map Org IDs to organization names
- Preview and analyze consolidated data
- Export results to Excel, CSV, or summary report
- Dark-mode friendly interface

Run with: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import io
from datetime import datetime, timezone, timedelta
from pathlib import Path

from processing import (
    process_and_merge_files,
    get_duplicate_task_run_ids,
    get_failed_task_counts,
    get_summary_by_group,
)
from calculations import set_ipu_conversion_factor, set_cost_per_ipu_month
from reports import (
    save_run,
    get_tasks_by_date_range,
    get_task_date_range,
    get_daily_stats_by_date_range,
    get_org_stats_by_date_range,
    get_project_stats_by_date_range,
    get_environment_stats_by_date_range,
    get_task_type_stats_by_date_range,
    get_status_stats_by_date_range,
    detect_anomalies_in_date_range,
    get_task_spikes_for_period,
)


# Page configuration
st.set_page_config(
    page_title="Informatica Usage Consolidator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better dark mode support
st.markdown("""
<style>
    .metric-card {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: rgba(255, 255, 255, 0.1);
        margin-bottom: 1rem;
    }
    .error-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: rgba(255, 100, 100, 0.2);
        border-left: 4px solid #ff6464;
    }
    .success-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: rgba(100, 255, 100, 0.2);
        border-left: 4px solid #64ff64;
    }
</style>
""", unsafe_allow_html=True)


def initialize_session_state():
    """Initialize session state variables for the app."""
    if 'merged_df' not in st.session_state:
        st.session_state.merged_df = None
    if 'upload_errors' not in st.session_state:
        st.session_state.upload_errors = []
    if 'processing_complete' not in st.session_state:
        st.session_state.processing_complete = False
    if 'show_global_filters' not in st.session_state:
        st.session_state.show_global_filters = False
    if 'export_name' not in st.session_state:
        st.session_state.export_name = ""
    if 'current_view' not in st.session_state:
        st.session_state.current_view = "analysis"  # analysis, reports, compare, trends


def display_header():
    """Display main header and title."""
    st.title("Informatica Usage Consolidator")
    st.markdown("""
    This tool helps you consolidate multiple Informatica usage spreadsheets 
    into a single dataset with normalized columns, calculated metrics, and a
    deduplicated historical table.
    """)


def display_sidebar():
    """Display sidebar with configuration options."""
    st.sidebar.header("Configuration")
    
    # IPU conversion factor
    ipu_factor = st.sidebar.number_input(
        "IPU Conversion Factor",
        min_value=0.1,
        max_value=10.0,
        value=0.16,
        step=0.01,
        help="Multiplier applied to Metered Value to calculate IPUs"
    )
    st.session_state.ipu_factor = ipu_factor
    set_ipu_conversion_factor(ipu_factor)
    
    # Cost per IPU per month
    cost_per_ipu = st.sidebar.number_input(
        "Cost per IPU/Month ($)",
        min_value=0.01,
        max_value=100.0,
        value=36.04,
        step=0.01,
        help="Cost multiplier for IPU calculations"
    )
    st.session_state.cost_per_ipu = cost_per_ipu
    set_cost_per_ipu_month(cost_per_ipu)
    
    st.sidebar.markdown("---")


def display_file_upload():
    """Display file upload section."""
    st.header("Upload Files")
    
    uploaded_files = st.file_uploader(
        "Select Excel or CSV files to consolidate",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=True,
        help="Drag and drop multiple files or click to select"
    )
    
    org_assignments = {}

    def infer_org_from_filename(filename, org_options, used_orgs):
        """Guess the organization from the uploaded filename."""
        name = ''.join(ch for ch in filename.lower() if ch.isalnum())

        patterns = [
            ("cessb", "CES-Sandbox"),
            ("cessandbox", "CES-Sandbox"),
            ("cesprod", "CES-Prod"),
            ("byucampusprod", "BYU-Campus-Prod"),
            ("campusprod", "BYU-Campus-Prod"),
            ("byucampusint", "BYU-Campus-Int"),
            ("campusint", "BYU-Campus-Int"),
            ("byuprod", "BYU-Prod"),
            ("byuint", "BYU-Int"),
            ("byudev", "BYU-Dev"),
        ]

        for token, org in patterns:
            if token in name and org in org_options and org not in used_orgs:
                return org

        for token, org in patterns:
            if token in name and org in org_options:
                return org

        for org in org_options:
            if org not in used_orgs:
                return org

        return org_options[0] if org_options else None
    
    if uploaded_files:
        st.info(f"{len(uploaded_files)} file(s) selected")
        
        # Show org selection for each file
        st.subheader("Select Organization for Each File")
        
        org_options = ["BYU-Dev", "BYU-Int", "BYU-Prod", "BYU-Campus-Int", "BYU-Campus-Prod", "CES-Prod", "CES-Sandbox"]
        used_orgs = set()
        
        for uploaded_file in uploaded_files:
            col1, col2 = st.columns([3, 1])
            suggested_org = infer_org_from_filename(uploaded_file.name, org_options, used_orgs)
            with col1:
                st.write(f"**{uploaded_file.name}**")
            with col2:
                default_index = org_options.index(suggested_org) if suggested_org in org_options else 0
                org = st.selectbox(
                    "Org",
                    org_options,
                    index=default_index,
                    key=uploaded_file.name,
                    label_visibility="collapsed"
                )
                org_assignments[uploaded_file.name] = org
                used_orgs.add(org)
        
        if st.button("Process Files", width="stretch"):
            with st.spinner("Processing files..."):
                merged_df, errors = process_and_merge_files(uploaded_files, org_assignments)
                
                # Persist merged_df to disk to avoid storing a huge DataFrame in session_state
                import os, time
                cache_dir = Path(__file__).parent / '.cache'
                cache_dir.mkdir(exist_ok=True)
                timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
                cache_path = cache_dir / f'merged_run_{timestamp}.pkl'
                merged_df.to_pickle(cache_path)

                st.session_state.merged_df_path = str(cache_path)
                # Keep a small preview in session state for UI responsiveness
                st.session_state.merged_preview = merged_df.head(2000)
                st.session_state.upload_errors = errors
                st.session_state.processing_complete = True
                st.session_state.uploaded_files = uploaded_files  # Store for later reference
                
                if errors:
                    with st.expander("Processing Errors", expanded=True):
                        for error in errors:
                            st.error(error)
                
                if not merged_df.empty:
                    st.success(f"Successfully processed! {len(merged_df):,} total rows")
    
    return uploaded_files


def display_global_filters(df):
    """Display one global sidebar filter panel and return filtered dataframe."""
    if df is None or df.empty:
        return df

    missing_label = "(Missing)"

    def build_filter_options(series):
        values = sorted([x for x in series.dropna().unique()])
        if series.isna().any():
            values.append(missing_label)
        return values

    filters = {}

    with st.sidebar.expander("Global Filters", expanded=False):
        st.caption("Filters apply to every section below")
        filter_cols = st.columns(1)

        if 'Org' in df.columns:
            with filter_cols[0]:
                all_orgs = build_filter_options(df['Org'])
                selected_orgs = st.multiselect(
                    "Organizations",
                    all_orgs,
                    default=all_orgs,
                    key="global_org_filter"
                )
                filters['Org'] = selected_orgs

        if 'Project Name' in df.columns:
            with filter_cols[0]:
                all_projects = build_filter_options(df['Project Name'])
                selected_projects = st.multiselect(
                    "Projects",
                    all_projects,
                    default=all_projects,
                    key="global_project_filter"
                )
                filters['Project Name'] = selected_projects

        if 'Folder Name' in df.columns:
            with filter_cols[0]:
                all_folders = build_filter_options(df['Folder Name'])
                selected_folders = st.multiselect(
                    "Folders",
                    all_folders,
                    default=all_folders,
                    key="global_folder_filter"
                )
                filters['Folder Name'] = selected_folders

        if 'Task Type' in df.columns:
            with filter_cols[0]:
                all_task_types = build_filter_options(df['Task Type'])
                selected_task_types = st.multiselect(
                    "Task Types",
                    all_task_types,
                    default=all_task_types,
                    key="global_tasktype_filter"
                )
                filters['Task Type'] = selected_task_types

        if 'Task Name' in df.columns:
            with filter_cols[0]:
                all_task_names = sorted([x for x in df['Task Name'].dropna().unique()])
                selected_task_name = st.selectbox(
                    "Task Name",
                    options=["All"] + all_task_names,
                    index=0,
                    key="global_taskname_filter"
                )
                filters['Task Name'] = selected_task_name

        if st.button("Clear Filters", key="clear_global_filters", width="stretch"):
            for key in [
                "global_org_filter",
                "global_project_filter",
                "global_folder_filter",
                "global_tasktype_filter",
                "global_taskname_filter",
            ]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

    filtered_df = df.copy()
    for col, selected_vals in filters.items():
        if col == 'Task Name':
            if selected_vals != "All":
                filtered_df = filtered_df[filtered_df[col] == selected_vals]
        elif selected_vals:
            if missing_label in selected_vals:
                non_missing_vals = [value for value in selected_vals if value != missing_label]
                filtered_df = filtered_df[
                    filtered_df[col].isin(non_missing_vals) | filtered_df[col].isna()
                ]
            else:
                filtered_df = filtered_df[filtered_df[col].isin(selected_vals)]

    st.caption(f"Showing {len(filtered_df):,} of {len(df):,} rows after global filters")
    return filtered_df


def display_data_preview(df):
    """Display preview of merged data."""
    if df is None or df.empty:
        return
    
    st.header("Data Preview")
    
    # Key metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Rows", f"{len(df):,}")
    
    with col2:
        st.metric("Total IPUs", f"{df['IPUs'].sum():,.8f}" if 'IPUs' in df.columns else "N/A")
    
    with col3:
        st.metric("Total Cost", f"${df['Cost/IPU/Month'].sum():,.6f}" if 'Cost/IPU/Month' in df.columns else "N/A")
    
    with col4:
        unique_tasks = df['Task Run ID'].nunique() if 'Task Run ID' in df.columns else 0
        st.metric("Unique Tasks", f"{unique_tasks:,}")
    
    # Data table with pagination
    st.subheader("Data Table")
    
    rows_per_page = st.selectbox(
        "Rows per page",
        [10, 25, 50, 100],
        index=1
    )
    
    page = st.number_input(
        "Page",
        min_value=1,
        max_value=max(1, (len(df) + rows_per_page - 1) // rows_per_page)
    )
    
    start_idx = (page - 1) * rows_per_page
    end_idx = min(start_idx + rows_per_page, len(df))
    
    st.dataframe(
        df.iloc[start_idx:end_idx],
        width="stretch",
        height=500
    )
    
    # Column statistics
    with st.expander("📊 Column Statistics"):
        numeric_cols = df.select_dtypes(include=['number']).columns
        
        if len(numeric_cols) > 0:
            stats_df = df[numeric_cols].describe().round(2)
            st.dataframe(stats_df, width="stretch")


def display_duplicate_analysis(df):
    """Display duplicate Task Run ID analysis."""
    if df is None or df.empty:
        return
    
    st.header("Duplicate Analysis")
    
    duplicates = get_duplicate_task_run_ids(df)
    
    if not duplicates.empty:
        st.warning(f"Found {len(duplicates):,} rows with duplicate Task Run IDs")
        
        with st.expander("View Duplicates"):
            st.dataframe(duplicates, width="stretch")
    else:
        st.success("No duplicate Tasks found!")


def display_status_analysis(df):
    """Display task status analysis with interactive filtering."""
    if df is None or df.empty:
        return
    
    st.header("Task Status Analysis")
    df_filtered = df.copy()
    
    status_counts = get_failed_task_counts(df_filtered)
    
    if not status_counts.empty:
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.dataframe(status_counts, width="stretch")
        
        with col2:
            st.bar_chart(status_counts)
        
        # Interactive status filter
        st.divider()
        st.subheader("Detailed Analysis by Status")
        
        available_statuses = sorted([x for x in df_filtered['Status'].unique() if pd.notna(x)])
        selected_status = st.selectbox("Select a Status to Analyze", available_statuses, key="status_filter")
        
        if selected_status:
            status_detail_df = df_filtered[df_filtered['Status'] == selected_status].copy()
            
            # Summary metrics
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Count", len(status_detail_df))
            with col2:
                st.metric("Total IPUs", f"{status_detail_df['IPUs'].sum():,.8f}")
            with col3:
                st.metric("Total Cost", f"${status_detail_df['Cost/IPU/Month'].sum():,.6f}")
            with col4:
                st.metric("Unique Tasks", status_detail_df['Task ID'].nunique())
            
            # Tabs for different analyses
            tab1, tab2, tab3, tab4, tab5 = st.tabs(["Top Tasks (Detailed)", "Daily and Hourly", "By Project/Folder", "By Organization", "Data Preview"])
            
            with tab1:
                st.subheader(f"Top Tasks by IPU Usage ({selected_status})")
                groupby_cols = ['Task ID', 'Task Name', 'Project Name', 'Folder Name']
                if all(col in status_detail_df.columns for col in groupby_cols):
                    top_n = st.selectbox(
                        "Rows to show",
                        [10, 20, 50],
                        index=0,
                        key="top_tasks_row_count"
                    )
                    task_summary = status_detail_df.groupby(groupby_cols).agg({
                        'IPUs': 'sum',
                        'Cost/IPU/Month': 'sum',
                        'Task Run ID': 'count',
                    }).reset_index()
                    task_summary.columns = ['Task ID', 'Task Name', 'Project Name', 'Folder Name', 'Total IPUs', 'Total Cost', 'Run Count']
                    task_summary = task_summary.sort_values('Total IPUs', ascending=False)
                    st.caption(f"Showing top {min(top_n, len(task_summary))} of {len(task_summary)} grouped tasks")
                    st.table(task_summary.head(top_n))
                else:
                    st.warning("Required columns (Task ID, Task Name, Project Name, Folder Name) not all available")
            
            with tab2:
                st.subheader(f"Daily and Hourly Breakdown ({selected_status})")
                filtered_df_time = status_detail_df.copy()
                
                # Parse Start DateTime if available, otherwise from Start Time
                if 'Start DateTime' in filtered_df_time.columns:
                    filtered_df_time['Start DateTime'] = pd.to_datetime(filtered_df_time['Start DateTime'], errors='coerce', format='mixed')
                elif 'Start Time' in filtered_df_time.columns:
                    filtered_df_time['Start DateTime'] = pd.to_datetime(filtered_df_time['Start Time'], errors='coerce', format='mixed')
                else:
                    st.info("No Start Time data available for hourly breakdown")
                    filtered_df_time = None
                
                if filtered_df_time is not None:
                    filtered_df_time = filtered_df_time.dropna(subset=['Start DateTime'])
                    
                    if not filtered_df_time.empty:
                        filtered_df_time['Date'] = filtered_df_time['Start DateTime'].dt.date
                        filtered_df_time['Hour of Day'] = filtered_df_time['Start DateTime'].dt.hour

                        daily = filtered_df_time.groupby('Date').agg({
                            'Task Run ID': 'count',
                            'IPUs': 'sum',
                            'Cost/IPU/Month': 'sum',
                        }).reset_index()
                        daily.columns = ['Date', 'Count', 'Total IPUs', 'Total Cost']
                        daily = daily.sort_values('Date')

                        hourly = filtered_df_time.groupby('Hour of Day').agg({
                            'Task Run ID': 'count',
                            'IPUs': 'sum',
                            'Cost/IPU/Month': 'sum',
                        }).reset_index()
                        hourly.columns = ['Hour of Day', 'Count', 'Total IPUs', 'Total Cost']
                        hourly = hourly.sort_values('Hour of Day')
                        
                        st.markdown("**Daily Breakdown**")
                        col1, col2 = st.columns(2)
                        with col1:
                            st.line_chart(daily.set_index('Date')[['Count']], width="stretch")
                        with col2:
                            st.line_chart(daily.set_index('Date')[['Total IPUs']], width="stretch")
                        st.table(daily)

                        st.markdown("**Hourly Breakdown (All Days Combined)**")
                        col3, col4 = st.columns(2)
                        with col3:
                            st.line_chart(hourly.set_index('Hour of Day')[['Count']], width="stretch")
                        with col4:
                            st.line_chart(hourly.set_index('Hour of Day')[['Total IPUs']], width="stretch")
                        st.table(hourly)
                    else:
                        st.info("No valid datetime data for hourly breakdown")
            
            with tab3:
                st.subheader(f"Breakdown by Project/Folder ({selected_status})")
                groupby_cols = ['Project Name', 'Folder Name']
                if all(col in status_detail_df.columns for col in groupby_cols):
                    proj_summary = status_detail_df.groupby(groupby_cols).agg({
                        'IPUs': 'sum',
                        'Cost/IPU/Month': 'sum',
                        'Task Run ID': 'count',
                        'Task ID': 'nunique',
                    }).reset_index()
                    proj_summary.columns = ['Project Name', 'Folder Name', 'Total IPUs', 'Total Cost', 'Run Count', 'Unique Tasks']
                    proj_summary = proj_summary.sort_values('Total IPUs', ascending=False)
                    st.table(proj_summary)
                else:
                    st.info("Project Name and/or Folder Name columns not available")
            
            with tab4:
                st.subheader(f"Breakdown by Organization ({selected_status})")
                if 'Org' in status_detail_df.columns:
                    org_summary = status_detail_df.groupby('Org').agg({
                        'IPUs': 'sum',
                        'Cost/IPU/Month': 'sum',
                        'Task Run ID': 'count',
                    }).reset_index()
                    org_summary.columns = ['Org', 'Total IPUs', 'Total Cost', 'Task Count']
                    org_summary = org_summary.sort_values('Total IPUs', ascending=False)
                    st.table(org_summary)
                else:
                    st.info("Org column not available")
            
            with tab5:
                st.subheader(f"Data Preview ({selected_status})")
                st.dataframe(status_detail_df, width="stretch")
    else:
        st.info("No status data available")


def display_time_series_analysis(df):
    """Display usage over time visualizations."""
    if df is None or df.empty:
        return
    
    # Ensure Start Time is datetime
    if 'Start Time' not in df.columns:
        return
    
    st.header("Usage Over Time")
    df_filtered = df.copy()
    
    # Prepare Start DateTime (prefer pre-parsed 'Start DateTime' when available)
    df_time = df_filtered.copy()
    if 'Start DateTime' in df_time.columns:
        df_time['Start DateTime'] = pd.to_datetime(df_time['Start DateTime'], errors='coerce', format='mixed')
        ts_col = 'Start DateTime'
    else:
        df_time['Start DateTime'] = pd.to_datetime(df_time['Start Time'], errors='coerce', format='mixed')
        ts_col = 'Start DateTime'

    df_time = df_time.dropna(subset=[ts_col])
    
    if df_time.empty:
        st.warning("No valid Start Time data available for time-series analysis")
        return
    
    # Time filters (date range and hour-of-day range)
    min_ts = df_time[ts_col].min()
    max_ts = df_time[ts_col].max()

    filter_time_cols = st.columns(2)
    default_start_date = max(min_ts.date(), (max_ts - timedelta(days=30)).date())
    default_end_date = max_ts.date()

    with filter_time_cols[0]:
        selected_date_range = st.date_input(
            "Date Range",
            value=(default_start_date, default_end_date),
            min_value=min_ts.date(),
            max_value=max_ts.date(),
            key="time_date_range_filter"
        )
    with filter_time_cols[1]:
        selected_hour_range = st.slider(
            "Hour Range (0-23)",
            min_value=0,
            max_value=23,
            value=(0, 23),
            key="time_hour_range_filter"
        )

    if isinstance(selected_date_range, tuple) and len(selected_date_range) == 2:
        start_date, end_date = selected_date_range
    else:
        start_date = selected_date_range
        end_date = selected_date_range

    df_time = df_time[
        (df_time[ts_col].dt.date >= start_date)
        & (df_time[ts_col].dt.date <= end_date)
        & (df_time[ts_col].dt.hour >= selected_hour_range[0])
        & (df_time[ts_col].dt.hour <= selected_hour_range[1])
    ]

    if df_time.empty:
        st.warning("No data available after applying date/hour filters")
        return

    # Create time-based dimensions
    df_time['Date'] = df_time[ts_col].dt.date
    df_time['Hour'] = df_time[ts_col].dt.floor('h')
    df_time['Hour of Day'] = df_time[ts_col].dt.hour

    # Tab selection for different time granularities
    time_tab1, time_tab2, time_tab3, time_tab4, time_tab5 = st.tabs([
        "Daily",
        "Daily by Org",
        "Hourly",
        "Hourly by Org",
        "Task Duration",
    ])

    with time_tab1:
        st.subheader("Daily Usage Summary")
        daily_stats = df_time.groupby('Date').agg({
            'Task Run ID': 'count',
            'IPUs': 'sum',
            'Cost/IPU/Month': 'sum',
        }).reset_index()
        daily_stats.columns = ['Date', 'Task Count', 'Total IPUs', 'Total Cost']
        daily_stats = daily_stats.sort_values('Date')
        
        col1, col2 = st.columns(2)
        with col1:
            st.line_chart(daily_stats.set_index('Date')[['Task Count']], width="stretch")
        with col2:
            st.line_chart(daily_stats.set_index('Date')[['Total IPUs']], width="stretch")
        
        st.dataframe(daily_stats, width="stretch", height=400)
    
    with time_tab2:
        st.subheader("Daily Usage by Organization")
        if 'Org' in df_time.columns:
            org_daily = df_time.groupby(['Date', 'Org']).agg({
                'Task Run ID': 'count',
                'IPUs': 'sum',
                'Cost/IPU/Month': 'sum',
            }).reset_index()
            org_daily.columns = ['Date', 'Org', 'Task Count', 'Total IPUs', 'Total Cost']
            
            # Pivot for line chart using Task Count
            org_count_pivot = org_daily.pivot_table(
                index='Date', 
                columns='Org', 
                values='Task Count', 
                aggfunc='sum'
            )
            
            if not org_count_pivot.empty:
                st.line_chart(org_count_pivot, width="stretch")
                st.dataframe(org_daily.sort_values('Date'), width="stretch", height=400)
        else:
            st.info("Org column not available")

    with time_tab3:
        st.subheader("Hourly Usage Summary (All Days Combined)")
        hourly_stats = df_time.groupby('Hour of Day').agg({
            'Task Run ID': 'count',
            'IPUs': 'sum',
            'Cost/IPU/Month': 'sum',
        }).reset_index()
        hourly_stats.columns = ['Hour of Day', 'Task Count', 'Total IPUs', 'Total Cost']
        hourly_stats = hourly_stats.sort_values('Hour of Day')

        col1, col2 = st.columns(2)
        with col1:
            st.line_chart(hourly_stats.set_index('Hour of Day')[['Task Count']], width="stretch")
        with col2:
            st.line_chart(hourly_stats.set_index('Hour of Day')[['Total IPUs']], width="stretch")

        st.dataframe(hourly_stats, width="stretch", height=400)

    with time_tab4:
        st.subheader("Hourly Usage by Organization (All Days Combined)")
        if 'Org' in df_time.columns:
            org_hourly = df_time.groupby(['Hour of Day', 'Org']).agg({
                'Task Run ID': 'count',
                'IPUs': 'sum',
                'Cost/IPU/Month': 'sum',
            }).reset_index()
            org_hourly.columns = ['Hour of Day', 'Org', 'Task Count', 'Total IPUs', 'Total Cost']

            org_count_pivot = org_hourly.pivot_table(
                index='Hour of Day',
                columns='Org',
                values='Task Count',
                aggfunc='sum'
            )

            if not org_count_pivot.empty:
                st.line_chart(org_count_pivot.sort_index(), width="stretch")
                st.dataframe(org_hourly.sort_values(['Hour of Day', 'Org']), width="stretch", height=400)
        else:
            st.info("Org column not available")
    
    with time_tab5:
        st.subheader("Task Duration Analysis")
        if 'End Time' in df_time.columns:
            df_duration = df_time.copy()
            # Ensure both start and end are datetimes
            df_duration['End Time'] = pd.to_datetime(df_duration['End Time'], errors='coerce', format='mixed')
            # Use the parsed Start DateTime column if available
            if 'Start DateTime' in df_duration.columns:
                df_duration['Start DateTime'] = pd.to_datetime(df_duration['Start DateTime'], errors='coerce', format='mixed')
                start_col = 'Start DateTime'
            else:
                df_duration['Start Time'] = pd.to_datetime(df_duration['Start Time'], errors='coerce', format='mixed')
                start_col = 'Start Time'

            # Only compute durations where both datetimes are present
            valid_mask = df_duration[start_col].notna() & df_duration['End Time'].notna()
            df_duration.loc[valid_mask, 'Duration (minutes)'] = (
                (df_duration.loc[valid_mask, 'End Time'] - df_duration.loc[valid_mask, start_col]).dt.total_seconds() / 60
            )
            df_duration['Duration (minutes)'] = df_duration['Duration (minutes)'].fillna(0).abs()
            
            # Duration stats overall
            duration_stats = pd.DataFrame({
                'Metric': ['Mean Duration (min)', 'Max Duration (min)', 'Min Duration (min)', 'Median Duration (min)', 'Total Duration (min)', 'Task Count'],
                'Value': [
                    df_duration['Duration (minutes)'].mean(),
                    df_duration['Duration (minutes)'].max(),
                    df_duration['Duration (minutes)'].min(),
                    df_duration['Duration (minutes)'].median(),
                    df_duration['Duration (minutes)'].sum(),
                    len(df_duration)
                ]
            })
            
            st.dataframe(duration_stats, width="stretch", hide_index=True)


def display_summaries(df):
    """Display summary statistics grouped by various dimensions."""
    if df is None or df.empty:
        return
    
    st.header("Summary Reports")
    df_filtered = df.copy()
    
    # Tabs for different summary views
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["By Org", "By Environment", "By Project", "By Project/Folder", "By Task Type"])
    
    with tab1:
        if 'Org' in df_filtered.columns:
            summary = get_summary_by_group(df_filtered, 'Org')
            st.dataframe(summary, width="stretch")
        else:
            st.info("Org column not available")
    
    with tab2:
        if 'Environment' in df_filtered.columns:
            summary = get_summary_by_group(df_filtered, 'Environment')
            st.dataframe(summary, width="stretch")
        else:
            st.info("Environment column not available")
    
    with tab3:
        if 'Project Name' in df_filtered.columns:
            summary = get_summary_by_group(df_filtered, 'Project Name')
            st.dataframe(summary, width="stretch")
        else:
            st.info("Project Name column not available")
    
    with tab4:
        st.subheader("Summary by Project and Folder")
        groupby_cols = ['Project Name', 'Folder Name']
        if all(col in df_filtered.columns for col in groupby_cols):
            proj_folder_summary = df_filtered.groupby(groupby_cols).agg({
                'Task Run ID': 'count',
                'IPUs': 'sum',
                'Cost/IPU/Month': 'sum',
                'Task ID': 'nunique',
            }).reset_index()
            proj_folder_summary.columns = ['Project Name', 'Folder Name', 'Task Run Count', 'Total IPUs', 'Total Cost', 'Unique Tasks']
            proj_folder_summary = proj_folder_summary.sort_values('Total IPUs', ascending=False)
            st.dataframe(proj_folder_summary, width="stretch")
        else:
            st.info("Project Name and/or Folder Name columns not available")
    
    with tab5:
        if 'Task Type' in df_filtered.columns:
            summary = get_summary_by_group(df_filtered, 'Task Type')
            st.dataframe(summary, width="stretch")
        else:
            st.info("Task Type column not available")


def display_export_options(df):
    """Display export options for processed data."""
    if df is None or df.empty:
        return
    
    st.header("Export Data")

    st.text_input(
        "Download name",
        key="export_name",
        placeholder="Optional name for the exported files",
        help="If provided, this name will be used as the filename prefix for Excel, CSV, and summary downloads."
    )

    def build_export_filename(prefix, suffix):
        safe_prefix = "".join(ch for ch in prefix.strip() if ch.isalnum() or ch in ("-", "_", " "))
        safe_prefix = safe_prefix.strip().replace(" ", "_")
        if safe_prefix:
            return f"{safe_prefix}_{suffix}"
        return suffix

    def sanitize_name(name: str) -> str:
        s = "".join(ch for ch in name.strip() if ch.isalnum() or ch in ("-", "_", " ", "."))
        s = s.strip().replace(" ", "_")
        return s

    raw_export_name = st.session_state.export_name.strip()
    safe_export_name = sanitize_name(raw_export_name)

    # If user provided a name, we'll use it verbatim (with the appropriate extension).
    if safe_export_name:
        st.caption(f"Downloads will use: {safe_export_name}.(xlsx/csv as chosen)")
    else:
        st.caption(f"Downloads will use: informatica_consolidated_<timestamp>... when no name provided")
    
    col1, col2, col3 = st.columns(3)
    
    # Export to Excel
    with col1:
        excel_buffer = io.BytesIO()
        df.to_excel(excel_buffer, index=False, engine='openpyxl')
        excel_buffer.seek(0)
        
        if safe_export_name:
            excel_name = safe_export_name if safe_export_name.lower().endswith('.xlsx') else f"{safe_export_name}.xlsx"
        else:
            excel_name = build_export_filename("informatica", f"consolidated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

        st.download_button(
            label="Download Excel",
            data=excel_buffer,
            file_name=excel_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch"
        )
    
    # Export to CSV
    with col2:
        csv_data = df.to_csv(index=False)
        
        if safe_export_name:
            csv_name = safe_export_name if safe_export_name.lower().endswith('.csv') else f"{safe_export_name}.csv"
        else:
            csv_name = build_export_filename("informatica", f"consolidated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

        st.download_button(
            label="Download CSV",
            data=csv_data,
            file_name=csv_name,
            mime="text/csv",
            width="stretch"
        )
    
    # Export summary report
    with col3:
        summary_buffer = io.BytesIO()
        
        with pd.ExcelWriter(summary_buffer, engine='openpyxl') as writer:
            # Overall summary
            df.describe().to_excel(writer, sheet_name='Overall')
            
            # By Org
            if 'Org' in df.columns:
                get_summary_by_group(df, 'Org').to_excel(writer, sheet_name='By Org')
            
            # By Environment
            if 'Environment' in df.columns:
                get_summary_by_group(df, 'Environment').to_excel(writer, sheet_name='By Env')
        
        summary_buffer.seek(0)
        
        if safe_export_name:
            summary_name = safe_export_name if safe_export_name.lower().endswith('.xlsx') else f"{safe_export_name}.xlsx"
        else:
            summary_name = build_export_filename("informatica", f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

        st.download_button(
            label="Download Summary",
            data=summary_buffer,
            file_name=summary_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch"
        )


def display_save_run_section(df):
    """Display option to append the current data to the historical table."""
    if df is None or df.empty:
        return
    
    st.header("Save to History")

    save_button = st.button("Append to Historical Table", width="stretch", type="primary")

    if save_button:
        try:
            status_message = st.empty()
            progress_bar = st.progress(0)
            log_box = st.empty()
            # Keep a short, in-memory log for UI display
            st.session_state.save_logs = []

            def progress_cb(percent: int, message: str):
                try:
                    progress_bar.progress(min(max(int(percent), 0), 100))
                except Exception:
                    pass
                status_message.info(message)
                # append to session logs and show last 30 lines
                st.session_state.save_logs.append(f"{datetime.now(timezone.utc).isoformat()} - {message}")
                log_box.text('\n'.join(st.session_state.save_logs[-30:]))

            if 'merged_df_path' in st.session_state:
                full_df = pd.read_pickle(st.session_state.merged_df_path)
            else:
                full_df = df

            # Attach callback to DataFrame attrs (backwards-compatible hook)
            try:
                full_df.attrs['progress_cb'] = progress_cb
            except Exception:
                # If attrs not writable for some reason, ignore and call directly
                pass


            rows_added, total_rows = save_run(full_df)

            progress_cb(100, 'Historical save complete.')

            st.success("Data saved to the historical table")
            st.info(f"Rows added: {rows_added:,}")
            st.info(f"Total historical rows: {total_rows:,}")

        except Exception as e:
            st.error("Historical save failed.")
            st.error(f"Error saving history: {str(e)}")


def display_trend_analysis():
    """Display trend analysis based on task start dates (not run dates)."""
    st.header("Time-Series Trend Analysis")
    
    try:
        # Get date range of available data
        min_date_str, max_date_str = get_task_date_range()
        
        if min_date_str is None:
            st.info("No task data available. Upload and save some runs to get started!")
            return
        
        # Parse dates
        min_date = pd.to_datetime(min_date_str).date()
        max_date = pd.to_datetime(max_date_str).date()
        
        st.write(f"Data available from **{min_date}** to **{max_date}**")
        st.write("Analysis is based on task start dates, not run save dates. All data is automatically combined.")
        
        default_start_date = max(min_date, (max_date - timedelta(days=30)))
        default_end_date = max_date

        # Date range selector
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(
                "Start Date",
                value=default_start_date,
                min_value=min_date,
                max_value=max_date,
                key="trend_start_date"
            )
        with col2:
            end_date = st.date_input(
                "End Date",
                value=default_end_date,
                min_value=min_date,
                max_value=max_date,
                key="trend_end_date"
            )
        
        if start_date > end_date:
            st.error("Start date must be before end date")
            return

        def _fmt_date(d):
            return f"{d.strftime('%B')} {d.day}"

        def _fmt_datetime_now():
            now = datetime.now()
            return f"{now.strftime('%b')} {now.day}, {now.year} {now.strftime('%H:%M')}"

        def _month_range(anchor_date):
            start = anchor_date.replace(day=1)
            return start, anchor_date

        def _prev_month_same_span(current_start, current_end):
            prev_month_end = current_start - timedelta(days=1)
            prev_month_start = prev_month_end.replace(day=1)
            span_days = (current_end - current_start).days
            return prev_month_start, min(prev_month_end, prev_month_start + timedelta(days=span_days))

        def _quarter_range(anchor_date):
            q_start_month = ((anchor_date.month - 1) // 3) * 3 + 1
            start = anchor_date.replace(month=q_start_month, day=1)
            return start, anchor_date

        def _prev_quarter_same_span(current_start, current_end):
            if current_start.month <= 3:
                prev_q_start = current_start.replace(year=current_start.year - 1, month=10, day=1)
            else:
                prev_q_start = current_start.replace(month=current_start.month - 3, day=1)
            span_days = (current_end - current_start).days
            return prev_q_start, prev_q_start + timedelta(days=span_days)
        
        def _window_ranges(anchor_date, days):
            current_start = anchor_date - timedelta(days=days - 1)
            current_end = anchor_date
            previous_end = current_start - timedelta(days=1)
            previous_start = previous_end - timedelta(days=days - 1)
            return current_start, current_end, previous_start, previous_end

        def _period_ranges(anchor_date, period_key):
            if period_key == 'weekly':
                return _window_ranges(anchor_date, 7)
            if period_key == 'monthly_30d':
                return _window_ranges(anchor_date, 30)
            if period_key == 'monthly_calendar':
                current_start, current_end = _month_range(anchor_date)
                previous_start, previous_end = _prev_month_same_span(current_start, current_end)
                return current_start, current_end, previous_start, previous_end
            if period_key == 'quarterly':
                current_start, current_end = _quarter_range(anchor_date)
                previous_start, previous_end = _prev_quarter_same_span(current_start, current_end)
                return current_start, current_end, previous_start, previous_end
            return _window_ranges(anchor_date, 7)

        def _safe_pct_change(curr, prev):
            if prev == 0:
                return "n/a" if curr == 0 else "new"
            return f"{((curr - prev) / prev) * 100:+.1f}%"

        def _trend_label(curr, prev):
            if prev == 0:
                return "new activity" if curr > 0 else "flat"
            pct = ((curr - prev) / prev) * 100
            if abs(pct) < 5:
                return "roughly flat"
            if pct > 0:
                return f"up ({pct:+.0f}%)"
            return f"down ({pct:+.0f}%)"

        def _build_dimension_delta_bullets(curr_df, prev_df, key_col, label, top_n=3):
            if curr_df.empty and prev_df.empty:
                return [f"- {label}: no data in either comparison window."]

            curr = curr_df[[key_col, 'task_count', 'total_ipus', 'total_cost']].copy() if not curr_df.empty else pd.DataFrame(columns=[key_col, 'task_count', 'total_ipus', 'total_cost'])
            prev = prev_df[[key_col, 'task_count', 'total_ipus', 'total_cost']].copy() if not prev_df.empty else pd.DataFrame(columns=[key_col, 'task_count', 'total_ipus', 'total_cost'])

            curr = curr.rename(columns={
                'task_count': 'task_count_curr',
                'total_ipus': 'total_ipus_curr',
                'total_cost': 'total_cost_curr',
            })
            prev = prev.rename(columns={
                'task_count': 'task_count_prev',
                'total_ipus': 'total_ipus_prev',
                'total_cost': 'total_cost_prev',
            })

            merged = curr.merge(prev, on=key_col, how='outer').fillna(0)
            merged['delta_ipus'] = merged['total_ipus_curr'] - merged['total_ipus_prev']
            merged['delta_cost'] = merged['total_cost_curr'] - merged['total_cost_prev']
            merged['delta_tasks'] = merged['task_count_curr'] - merged['task_count_prev']

            if merged.empty:
                return [f"- {label}: no rows found."]

            up = merged[merged['delta_ipus'] > 0].sort_values('delta_ipus', ascending=False).head(top_n)
            down = merged[merged['delta_ipus'] < 0].sort_values('delta_ipus', ascending=True).head(top_n)

            up_names = [str(x) for x in up[key_col].tolist() if pd.notna(x)]
            down_names = [str(x) for x in down[key_col].tolist() if pd.notna(x)]

            bullets = []
            bullets.append(f"- {label} trending up: {', '.join(up_names) if up_names else 'none material'}.")
            bullets.append(f"- {label} trending down: {', '.join(down_names) if down_names else 'none material'}.")

            return bullets

        def _effective_metrics(df):
            if df is None or df.empty:
                return pd.DataFrame(columns=['org', 'project_name', 'task_name', 'effective_ipus', 'effective_cost'])

            out = df.copy()

            if 'ipus' in out.columns:
                ipus = pd.to_numeric(out['ipus'], errors='coerce')
            else:
                ipus = pd.Series([pd.NA] * len(out), index=out.index)

            if 'metered_value' in out.columns:
                metered = pd.to_numeric(out['metered_value'], errors='coerce').fillna(0)
            else:
                metered = pd.Series([0.0] * len(out), index=out.index)

            if 'cost' in out.columns:
                cost = pd.to_numeric(out['cost'], errors='coerce')
            else:
                cost = pd.Series([pd.NA] * len(out), index=out.index)

            ipu_factor = float(st.session_state.get('ipu_factor', 0.16))
            cost_per_ipu = float(st.session_state.get('cost_per_ipu', 36.04))

            out['effective_ipus'] = ipus.fillna(metered * ipu_factor).fillna(0)
            out['effective_cost'] = cost.fillna(out['effective_ipus'] * cost_per_ipu).fillna(0)

            for col in ['org', 'project_name', 'task_name']:
                if col not in out.columns:
                    out[col] = '(Unknown)'
                out[col] = out[col].fillna('(Unknown)').astype(str)

            return out

        def _build_org_split_lines(cur_start, cur_end, prev_start, prev_end):
            def _fmt_ipu_per_run(value):
                val = float(value)
                abs_val = abs(val)
                if abs_val == 0:
                    return "0"
                if abs_val >= 0.01:
                    return f"{val:.3f}"
                if abs_val >= 0.0001:
                    return f"{val:.6f}"
                return f"{val:.2e}"

            org_curr = get_org_stats_by_date_range(cur_start.isoformat(), cur_end.isoformat())
            org_prev = get_org_stats_by_date_range(prev_start.isoformat(), prev_end.isoformat())

            org_curr = org_curr.rename(columns={'task_count': 'task_count_curr', 'total_ipus': 'total_ipus_curr'}) if not org_curr.empty else pd.DataFrame(columns=['org', 'task_count_curr', 'total_ipus_curr'])
            org_prev = org_prev.rename(columns={'task_count': 'task_count_prev', 'total_ipus': 'total_ipus_prev'}) if not org_prev.empty else pd.DataFrame(columns=['org', 'task_count_prev', 'total_ipus_prev'])

            org_change = org_curr.merge(org_prev, on='org', how='outer').fillna(0)
            org_change['delta_ipus'] = org_change['total_ipus_curr'] - org_change['total_ipus_prev']

            cur_tasks_raw = get_tasks_by_date_range(cur_start.isoformat(), cur_end.isoformat())
            prev_tasks_raw = get_tasks_by_date_range(prev_start.isoformat(), prev_end.isoformat())
            cur_tasks = _effective_metrics(cur_tasks_raw)
            prev_tasks = _effective_metrics(prev_tasks_raw)

            lines = []
            if org_change.empty:
                lines.append("- No organization-level changes found for this period.")
                return lines

            org_change = org_change.sort_values('delta_ipus', ascending=False)

            for _, org_row in org_change.iterrows():
                org_name = org_row['org'] if pd.notna(org_row['org']) and str(org_row['org']).strip() else '(Unknown)'
                delta = float(org_row['delta_ipus'])
                if abs(delta) < 0.01 and float(org_row['total_ipus_curr']) == 0 and float(org_row['total_ipus_prev']) == 0:
                    continue

                direction = 'Increased' if delta >= 0 else 'Decreased'
                lines.append(f"- {org_name}: {direction} {abs(delta):,.2f} IPUs")

                org_cur_tasks = cur_tasks[cur_tasks['org'] == org_name]
                org_prev_tasks = prev_tasks[prev_tasks['org'] == org_name]

                # Project highlight in this org
                proj_cur = org_cur_tasks.groupby('project_name', dropna=False).agg(ipus_curr=('effective_ipus', 'sum')).reset_index() if not org_cur_tasks.empty else pd.DataFrame(columns=['project_name', 'ipus_curr'])
                proj_prev = org_prev_tasks.groupby('project_name', dropna=False).agg(ipus_prev=('effective_ipus', 'sum')).reset_index() if not org_prev_tasks.empty else pd.DataFrame(columns=['project_name', 'ipus_prev'])
                proj = proj_cur.merge(proj_prev, on='project_name', how='outer').fillna(0)
                if not proj.empty:
                    proj['delta'] = proj['ipus_curr'] - proj['ipus_prev']
                    top_proj = proj.iloc[proj['delta'].abs().idxmax()]
                    proj_dir = 'increased' if top_proj['delta'] >= 0 else 'decreased'
                    lines.append(f"  - {top_proj['project_name']}: {proj_dir} {abs(float(top_proj['delta'])):,.2f} IPUs")

                # Task run-count and cost highlight in this org
                task_cur = org_cur_tasks.groupby('task_name', dropna=False).agg(
                    runs_curr=('task_name', 'count'),
                    ipus_curr=('effective_ipus', 'sum'),
                ).reset_index() if not org_cur_tasks.empty else pd.DataFrame(columns=['task_name', 'runs_curr', 'ipus_curr'])
                task_prev = org_prev_tasks.groupby('task_name', dropna=False).agg(
                    runs_prev=('task_name', 'count'),
                    ipus_prev=('effective_ipus', 'sum'),
                ).reset_index() if not org_prev_tasks.empty else pd.DataFrame(columns=['task_name', 'runs_prev', 'ipus_prev'])
                task = task_cur.merge(task_prev, on='task_name', how='outer').fillna(0)
                if not task.empty:
                    task['run_delta'] = task['runs_curr'] - task['runs_prev']
                    task['run_delta_abs'] = task['run_delta'].abs()
                    top_task = task.sort_values('run_delta_abs', ascending=False).head(1)
                    if not top_task.empty:
                        row = top_task.iloc[0]
                        task_name = row['task_name'] if str(row['task_name']).strip() else '(Unknown task)'
                        lines.append(
                            f"    - {task_name} run count: {int(row['runs_prev'])} → {int(row['runs_curr'])}"
                        )

                        prev_runs = max(int(row['runs_prev']), 1)
                        curr_runs = max(int(row['runs_curr']), 1)
                        prev_ipu_per_run = float(row['ipus_prev']) / prev_runs
                        curr_ipu_per_run = float(row['ipus_curr']) / curr_runs
                        prev_rate = _fmt_ipu_per_run(prev_ipu_per_run)
                        curr_rate = _fmt_ipu_per_run(curr_ipu_per_run)
                        tiny_note = " (tiny change)" if prev_rate == curr_rate and prev_ipu_per_run != curr_ipu_per_run else ""
                        lines.append(
                            f"    - {task_name} IPU/run: {prev_rate} → {curr_rate}{tiny_note}"
                        )

                lines.append("")

            return lines

        def _build_period_section(anchor_date, period_key, label):
            cur_start, cur_end, prev_start, prev_end = _period_ranges(anchor_date, period_key)

            period_title = label
            if period_key == 'monthly_calendar':
                period_title = f"{label} ({cur_start.strftime('%B')} {cur_start.year})"
            elif period_key == 'quarterly':
                quarter = ((cur_start.month - 1) // 3) + 1
                period_title = f"{label} (Q{quarter} {cur_start.year})"

            org_curr = get_org_stats_by_date_range(cur_start.isoformat(), cur_end.isoformat())
            org_prev = get_org_stats_by_date_range(prev_start.isoformat(), prev_end.isoformat())
            proj_curr = get_project_stats_by_date_range(cur_start.isoformat(), cur_end.isoformat())
            proj_prev = get_project_stats_by_date_range(prev_start.isoformat(), prev_end.isoformat())

            daily_curr = get_daily_stats_by_date_range(cur_start.isoformat(), cur_end.isoformat())
            daily_prev = get_daily_stats_by_date_range(prev_start.isoformat(), prev_end.isoformat())

            curr_tasks = int(daily_curr['task_count'].sum()) if not daily_curr.empty else 0
            prev_tasks = int(daily_prev['task_count'].sum()) if not daily_prev.empty else 0
            curr_ipus = float(daily_curr['total_ipus'].sum()) if not daily_curr.empty else 0.0
            prev_ipus = float(daily_prev['total_ipus'].sum()) if not daily_prev.empty else 0.0

            lines = []
            if period_key == 'monthly_calendar':
                prev_label = prev_start.strftime('%b')
                curr_label = cur_start.strftime('%b')
                delta_ipus = curr_ipus - prev_ipus
                dir_word = 'increase' if delta_ipus >= 0 else 'decrease'
                lines.append(f"{cur_start.strftime('%B')} IDMC Change Report")
                lines.append("")
                lines.append(f"{abs(delta_ipus):,.2f} IPU {dir_word} from {prev_label} to {curr_label}")
                lines.append("")

            lines.append(
                f"- {period_title}: {_fmt_date(cur_start)} to {_fmt_date(cur_end)} "
                f"(vs {_fmt_date(prev_start)} to {_fmt_date(prev_end)})."
            )
            lines.append(
                f"  - Workload: {prev_tasks:,} → {curr_tasks:,} tasks ({curr_tasks - prev_tasks:+,})."
            )
            lines.append(
                f"  - IPU usage: {prev_ipus:,.2f} → {curr_ipus:,.2f} ({curr_ipus - prev_ipus:+,.2f})."
            )
            lines.append(
                f"  - Totals: {curr_tasks:,} tasks, {curr_ipus:,.2f} total IPUs."
            )

            lines.extend(_build_dimension_delta_bullets(org_curr, org_prev, 'org', 'Organizations'))
            lines.extend(_build_dimension_delta_bullets(proj_curr, proj_prev, 'project_name', 'Projects'))
            lines.append("")
            lines.extend(_build_org_split_lines(cur_start, cur_end, prev_start, prev_end))
            lines.append("")
            return lines

        def _build_executive_report_text(anchor_date, selected_periods):
            report_lines = []
            report_lines.append("Informatica Usage Executive Trend Report")
            report_lines.append(f"Prepared on {_fmt_datetime_now()} using data through {_fmt_date(anchor_date)}.")
            report_lines.append("")
            report_lines.append("Summary")
            report_lines.append(
                "- Plain-language summary of week, month, and quarter trends across organizations and projects."
            )
            report_lines.append("- Focused on high-impact shifts and notable spikes.")
            report_lines.append("")

            if 'weekly' in selected_periods:
                report_lines.append("Weekly Change")
                report_lines.extend(_build_period_section(anchor_date, 'weekly', "Past Week"))

            if 'monthly_30d' in selected_periods:
                report_lines.append("Monthly Change (Rolling 30 Days)")
                report_lines.extend(_build_period_section(anchor_date, 'monthly_30d', "Last 30 Days"))

            if 'monthly_calendar' in selected_periods:
                report_lines.append("Monthly Change (Calendar Month)")
                report_lines.extend(_build_period_section(anchor_date, 'monthly_calendar', "Month to Date"))

            if 'quarterly' in selected_periods:
                report_lines.append("Quarterly Change")
                report_lines.extend(_build_period_section(anchor_date, 'quarterly', "Quarter to Date"))

            q_start = (anchor_date - timedelta(days=89)).isoformat()
            q_end = anchor_date.isoformat()
            report_lines.append("Potential Anomalies")
            anomalies = detect_anomalies_in_date_range(q_start, q_end, metric='total_ipus', threshold_std=2.0)

            def _ipu_anomaly_driver_text(anomaly_day):
                day_start = datetime.combine(anomaly_day, datetime.min.time())
                day_end = datetime.combine(anomaly_day, datetime.strptime("23:59:59", "%H:%M:%S").time())
                day_tasks = _effective_metrics(get_tasks_by_date_range(day_start.isoformat(sep=' '), day_end.isoformat(sep=' ')))
                if day_tasks.empty:
                    return ""

                total_day_ipus = float(day_tasks['effective_ipus'].sum())
                if total_day_ipus <= 0:
                    return ""

                proj = day_tasks.groupby('project_name', dropna=False).agg(ipus=('effective_ipus', 'sum')).reset_index()
                task = day_tasks.groupby('task_name', dropna=False).agg(ipus=('effective_ipus', 'sum')).reset_index()
                top_proj = proj.sort_values('ipus', ascending=False).head(1)
                top_task = task.sort_values('ipus', ascending=False).head(1)

                top_proj_ipus = float(top_proj.iloc[0]['ipus']) if not top_proj.empty else 0.0
                top_task_ipus = float(top_task.iloc[0]['ipus']) if not top_task.empty else 0.0

                if top_proj_ipus >= top_task_ipus and top_proj_ipus > 0:
                    name = str(top_proj.iloc[0]['project_name']).strip() if not top_proj.empty else "(Unknown project)"
                    share = top_proj_ipus / total_day_ipus
                    if share >= 0.35:
                        return f" Primary driver: project {name} ({top_proj_ipus:,.2f} IPUs)."
                    return ""

                if top_task_ipus > 0:
                    name = str(top_task.iloc[0]['task_name']).strip() if not top_task.empty else "(Unknown task)"
                    share = top_task_ipus / total_day_ipus
                    if share >= 0.35:
                        return f" Primary driver: task {name} ({top_task_ipus:,.2f} IPUs)."
                return ""

            if anomalies.empty:
                report_lines.append("- total_ipus: no unusual pattern detected.")
            else:
                top = anomalies.sort_values('z_score', ascending=False).head(3)
                report_lines.append(f"- total_ipus: {len(anomalies)} unusual day(s) detected. Dates to review:")
                for _, row in top.iterrows():
                    anomaly_day = pd.to_datetime(row['date']).date()
                    observed_ipus = float(row['total_ipus']) if 'total_ipus' in row else 0.0
                    driver_text = _ipu_anomaly_driver_text(anomaly_day)
                    report_lines.append(
                        f"  - {_fmt_date(anomaly_day)} ({row['anomaly_type']}): {observed_ipus:,.2f} IPUs.{driver_text}"
                    )

            report_lines.append("")
            report_lines.append("Task Spike Watch")
            spikes = get_task_spikes_for_period(
                end_date=anchor_date.isoformat(),
                lookback_days=90,
                baseline_days=90,
                threshold_std=3.0,
                min_baseline_days=5,
                top_n=10,
            )
            if spikes.empty:
                report_lines.append("- No task showed a major spike versus prior baseline.")
            else:
                report_lines.append(f"- {len(spikes)} task spike(s) flagged. Top items:")
                for _, row in spikes.head(5).iterrows():
                    report_lines.append(
                        f"  - {_fmt_date(pd.to_datetime(row['task_date']).date())}: {row['task_name']} in {row['org']} / {row['project_name']} "
                        f"ran materially above baseline (about {row['multiplier_vs_baseline']:.1f}x)."
                    )

            report_lines.append("")
            report_lines.append("Recommended Actions")
            report_lines.append("- Validate top increasing orgs/projects for planned growth versus unexpected activity.")
            report_lines.append("- Review anomaly days against release schedules, incidents, and backfills.")
            report_lines.append("- Investigate top task spikes for retry loops, schedule drift, or configuration changes.")

            return "\n".join(report_lines)

        # Tabs for different analyses
        tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
            "Daily Trends", "By Organization", "By Project", "By Environment", "By Task Type", "Anomaly Detection", "Narrative Summary"
        ])
        
        with tab1:
            st.subheader("Daily Usage Trends")
            st.write("Shows how your task usage varies day by day")
            
            daily_stats = get_daily_stats_by_date_range(
                start_date.isoformat(), end_date.isoformat()
            )
            
            if daily_stats.empty:
                st.info("No task data for this date range")
            else:
                daily_stats['date'] = pd.to_datetime(daily_stats['date'], errors='coerce')
                daily_stats = daily_stats.dropna(subset=['date']).sort_values('date')

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.line_chart(daily_stats.set_index('date')[['task_count']], width='stretch')
                with col2:
                    st.line_chart(daily_stats.set_index('date')[['total_ipus']], width='stretch')
                with col3:
                    st.line_chart(daily_stats.set_index('date')[['total_cost']], width='stretch')

                if (daily_stats['total_ipus'].sum() == 0) and (daily_stats['total_cost'].sum() == 0):
                    st.info(
                        "Task counts are present, but historical IPU/Cost values are all zero for this date range. "
                        "This usually means earlier saved rows did not include IPU/Cost fields."
                    )

                st.caption("Charts shown: Task Count, Total IPUs, Total Cost")
                
                st.dataframe(daily_stats, width='stretch', hide_index=True)
                
                # Summary stats
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Days", len(daily_stats))
                with col2:
                    st.metric("Total Tasks", daily_stats['task_count'].sum())
                with col3:
                    st.metric("Total IPUs", f"{daily_stats['total_ipus'].sum():,.2f}")
                with col4:
                    st.metric("Total Cost", f"${daily_stats['total_cost'].sum():,.2f}")

            st.divider()
            st.subheader("Export Logs by Timestamp")
            st.caption("Choose an exact start/end timestamp and export matching log rows.")

            export_col1, export_col2 = st.columns(2)
            with export_col1:
                export_start_date = st.date_input(
                    "Export Start Date",
                    value=start_date,
                    min_value=min_date,
                    max_value=max_date,
                    key="trend_export_start_date",
                )
                export_start_time = st.time_input(
                    "Export Start Time",
                    value=datetime.min.time(),
                    key="trend_export_start_time",
                )

            with export_col2:
                export_end_date = st.date_input(
                    "Export End Date",
                    value=end_date,
                    min_value=min_date,
                    max_value=max_date,
                    key="trend_export_end_date",
                )
                export_end_time = st.time_input(
                    "Export End Time",
                    value=datetime.strptime("23:59", "%H:%M").time(),
                    key="trend_export_end_time",
                )

            export_start_dt = datetime.combine(export_start_date, export_start_time)
            export_end_dt = datetime.combine(export_end_date, export_end_time)

            if export_start_dt > export_end_dt:
                st.error("Export start timestamp must be before export end timestamp")
            else:
                export_tasks = get_tasks_by_date_range(
                    export_start_dt.isoformat(sep=' '),
                    export_end_dt.isoformat(sep=' '),
                )

                if export_tasks.empty:
                    st.info("No log rows found for the selected export timestamp range.")
                else:
                    st.caption(
                        f"{len(export_tasks):,} log rows match {export_start_dt} to {export_end_dt}."
                    )
                    download_col1, download_col2 = st.columns(2)

                    with download_col1:
                        csv_data = export_tasks.to_csv(index=False)
                        st.download_button(
                            label="Download Trend Logs CSV",
                            data=csv_data,
                            file_name=f"trend_logs_{export_start_dt.strftime('%Y%m%d_%H%M%S')}_to_{export_end_dt.strftime('%Y%m%d_%H%M%S')}.csv",
                            mime="text/csv",
                            width="stretch",
                        )

                    with download_col2:
                        excel_buffer = io.BytesIO()
                        export_tasks.to_excel(excel_buffer, index=False, engine='openpyxl')
                        excel_buffer.seek(0)
                        st.download_button(
                            label="Download Trend Logs Excel",
                            data=excel_buffer,
                            file_name=f"trend_logs_{export_start_dt.strftime('%Y%m%d_%H%M%S')}_to_{export_end_dt.strftime('%Y%m%d_%H%M%S')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            width="stretch",
                        )
        
        with tab2:
            st.subheader("🏢 Breakdown by Organization")
            st.write("See which organizations are using the most resources")
            
            org_stats = get_org_stats_by_date_range(
                start_date.isoformat(), end_date.isoformat()
            )
            
            if org_stats.empty:
                st.info("No organization data for this date range")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    st.bar_chart(org_stats.set_index('org')[['total_ipus']], width='stretch')
                with col2:
                    st.bar_chart(org_stats.set_index('org')[['total_cost']], width='stretch')
                
                st.dataframe(org_stats, width='stretch', hide_index=True)
        
        with tab3:
            st.subheader("📁 Breakdown by Project")
            st.write("See which projects are using the most resources")
            
            project_stats = get_project_stats_by_date_range(
                start_date.isoformat(), end_date.isoformat()
            )
            
            if project_stats.empty:
                st.info("No project data for this date range")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    st.bar_chart(project_stats.set_index('project_name')[['total_ipus']], width='stretch')
                with col2:
                    st.bar_chart(project_stats.set_index('project_name')[['total_cost']], width='stretch')
                
                st.dataframe(project_stats, width='stretch', hide_index=True)
        
        with tab4:
            st.subheader("🌐 Breakdown by Environment")
            st.write("See which environments are using the most resources")
            
            env_stats = get_environment_stats_by_date_range(
                start_date.isoformat(), end_date.isoformat()
            )
            
            if env_stats.empty:
                st.info("No environment data for this date range")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    st.bar_chart(env_stats.set_index('environment')[['total_ipus']], width='stretch')
                with col2:
                    st.bar_chart(env_stats.set_index('environment')[['total_cost']], width='stretch')
                
                st.dataframe(env_stats, width='stretch', hide_index=True)
        
        with tab5:
            st.subheader("⚙️ Breakdown by Task Type")
            st.write("See which task types are using the most resources")
            
            tasktype_stats = get_task_type_stats_by_date_range(
                start_date.isoformat(), end_date.isoformat()
            )
            
            if tasktype_stats.empty:
                st.info("No task type data for this date range")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    st.bar_chart(tasktype_stats.set_index('task_type')[['total_ipus']], width='stretch')
                with col2:
                    st.bar_chart(tasktype_stats.set_index('task_type')[['total_cost']], width='stretch')
                
                st.dataframe(tasktype_stats, width='stretch', hide_index=True)
        
        with tab6:
            st.subheader("🔍 Anomaly Detection")
            st.write("Identify unusual days that deviate from normal patterns")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                metric = st.selectbox(
                    "Metric to check:",
                    ["task_count", "total_ipus", "total_cost"],
                    format_func=lambda x: {"total_ipus": "Total IPUs", "total_cost": "Total Cost", "task_count": "Task Count"}[x]
                )
            with col2:
                threshold = st.slider("Sensitivity (std devs):", 1.0, 3.0, 2.0, 0.1)
            with col3:
                org_filter = st.selectbox(
                    "Filter by org (optional):",
                    ["All"] + sorted(get_org_stats_by_date_range(
                        start_date.isoformat(), end_date.isoformat()
                    )['org'].tolist() if not get_org_stats_by_date_range(
                        start_date.isoformat(), end_date.isoformat()
                    ).empty else [])
                )
            
            org_param = None if org_filter == "All" else org_filter
            
            anomalies = detect_anomalies_in_date_range(
                start_date.isoformat(), end_date.isoformat(),
                metric=metric, threshold_std=threshold, org=org_param
            )
            
            if anomalies.empty:
                st.success("No anomalies detected in this date range!")
            else:
                st.warning(f"Found {len(anomalies)} anomalous days")
                
                # Display anomalies
                display_cols = ['date', metric, 'anomaly_type', 'z_score']
                st.dataframe(anomalies[display_cols], width='stretch', hide_index=True)
                
                # Visualization
                daily_stats = get_daily_stats_by_date_range(
                    start_date.isoformat(), end_date.isoformat(), org=org_param
                )
                if not daily_stats.empty:
                    daily_stats['date'] = pd.to_datetime(daily_stats['date'], errors='coerce')
                    daily_stats = daily_stats.dropna(subset=['date']).sort_values('date')
                    st.line_chart(daily_stats.set_index('date')[[metric]], width='stretch')

        with tab7:
            st.subheader("Report")
            st.write("Narrative summary.")

            selected_periods = st.multiselect(
                "Include sections",
                options=["weekly", "monthly_30d", "monthly_calendar", "quarterly"],
                default=["weekly", "monthly_calendar"],
                format_func=lambda x: {
                    "weekly": "Weekly",
                    "monthly_30d": "Monthly (Rolling Last 30 Days)",
                    "monthly_calendar": "Monthly (Calendar Month)",
                    "quarterly": "Quarterly (Calendar Quarter)",
                }[x],
                key="executive_report_period_filter_v2",
            )

            if not selected_periods:
                st.info("Select at least one section (Weekly, Monthly Rolling 30 Days, Monthly Calendar, or Quarterly).")
                return

            anchor_date = end_date
            report_text = _build_executive_report_text(anchor_date, selected_periods)

            st.text_area(
                "Copy/Paste Report",
                value=report_text,
                height=520,
            )
    
    except Exception as e:
        st.error(f"Error in trend analysis: {str(e)}")
        import traceback
        st.write(traceback.format_exc())


def main():
    """Main app entry point."""
    initialize_session_state()
    
    display_header()
    display_sidebar()
    
    # Navigation menu
    st.sidebar.markdown("---")
    st.sidebar.header("📚 Navigation")
    view = st.sidebar.radio(
        "Select view:",
        ["Current Analysis", "Trend Analysis"],
        key="nav_view"
    )
    
    if view == "Current Analysis":
        display_file_upload()
        
        if st.session_state.processing_complete:
            if 'merged_df_path' in st.session_state:
                full_df = pd.read_pickle(st.session_state.merged_df_path)
            elif 'merged_preview' in st.session_state:
                full_df = st.session_state.merged_preview
            else:
                full_df = st.session_state.merged_df

            st.session_state.source_files = st.session_state.get('uploaded_files', [])

            filtered_df = display_global_filters(full_df)

            display_save_run_section(full_df)

            st.divider()
            
            display_data_preview(filtered_df)
            
            st.divider()
            
            display_time_series_analysis(filtered_df)
            
            st.divider()
            
            display_duplicate_analysis(filtered_df)
            
            st.divider()
            
            display_status_analysis(filtered_df)
            
            st.divider()
            
            display_summaries(filtered_df)
            
            st.divider()

            display_export_options(filtered_df)

    
    elif view == "Trend Analysis":
        display_trend_analysis()
    
    # Footer
    st.divider()
    st.markdown("""
    ---
    **Version 1.0** | Built with Streamlit for Informatica usage consolidation | Historical-table storage and trend analysis
    """)


if __name__ == "__main__":
    main()
