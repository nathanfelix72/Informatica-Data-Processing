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
from datetime import datetime

from processing import (
    process_and_merge_files,
    get_duplicate_task_run_ids,
    get_failed_task_counts,
    get_summary_by_group,
)
from calculations import set_ipu_conversion_factor, set_cost_per_ipu_month


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


def display_header():
    """Display main header and title."""
    st.title("Informatica Usage Consolidator")
    st.markdown("""
    This tool helps you consolidate multiple Informatica usage spreadsheets 
    into a single dataset with normalized columns, calculated metrics, and 
    summary statistics.
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
                
                st.session_state.merged_df = merged_df
                st.session_state.upload_errors = errors
                st.session_state.processing_complete = True
                
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
    with filter_time_cols[0]:
        selected_date_range = st.date_input(
            "Date Range",
            value=(min_ts.date(), max_ts.date()),
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


def main():
    """Main app entry point."""
    initialize_session_state()
    
    display_header()
    display_sidebar()
    
    display_file_upload()
    
    if st.session_state.processing_complete:
        filtered_df = display_global_filters(st.session_state.merged_df)

        if filtered_df is None or filtered_df.empty:
            st.warning("No rows match the selected global filters")
            return

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
    
    # Footer
    st.divider()
    st.markdown("""
    ---
    **Version 1.0** | Built with Streamlit for Informatica usage consolidation
    """)


if __name__ == "__main__":
    main()
