# Configuration Reference

This file documents all configurable parameters in the application.

## Calculations Configuration

Located in: `calculations.py`

### IPU Conversion Factor
```python
IPU_CONVERSION_FACTOR = 1.0  # Default: 1.0
```
- **Purpose**: Multiplier applied to Metered Value to calculate IPUs
- **Example**: If your Metered Value is in thousands, set to 0.001
- **Runtime Change**: Use Streamlit sidebar slider (0.1 to 10.0)

### Cost Per IPU/Month
```python
COST_PER_IPU_MONTH = 1.0  # Default: 1.0
```
- **Purpose**: Cost multiplier for each IPU monthly cost calculation
- **Example**: If each IPU costs $2.50, set to 2.50
- **Runtime Change**: Use Streamlit sidebar input (0.01 to 100.0)

### Core Cost Multiplier
Edit the `calculate_cores_cost()` function:
```python
cost_per_core_minute = 0.001  # Cost per core per minute
```
- **Default**: $0.001 per core per minute
- **Adjust**: Change this value to match your cost model

## Mappings Configuration

Located in: `mappings.py`

### Organization ID Mappings
```python
ORG_MAPPING = {
    "1001": "Production",
    "1002": "Staging",
    "1003": "Development",
    "1004": "QA",
    "1005": "Test",
}
```

#### Pre-configured IDs
- 1001 → Production
- 1002 → Staging  
- 1003 → Development
- 1004 → QA
- 1005 → Test

#### Add Custom Mappings

**Option A: Edit Code**
```python
ORG_MAPPING = {
    "1001": "Production",
    "1002": "Staging",
    "1003": "Development",
    "1004": "QA",
    "1005": "Test",
    "1006": "DataLake",      # Add new ones
    "1007": "DataMart",
    "2001": "Client_A_Prod",
}
```

**Option B: Runtime (via Streamlit UI)**
- Start the app: `streamlit run app.py`
- Sidebar → View/Edit Mappings
- Add New Mapping section
- Enter Org ID and Organization Name
- Click "Add Mapping"

## Data Processing Configuration

Located in: `processing.py`

### Standard Columns
The app expects these input columns (normalized):
```python
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
```

**Note**: The app auto-normalizes variations like:
- "taskid" → "Task ID"
- "task id" → "Task ID"
- "Task_ID" → "Task ID"
- etc.

### Column Name Variations Handled

The normalization process handles these variations:
```
taskid, task id, TaskID, TASK_ID → Task ID
coresused, cores used, Cores_Used → Cores Used
meteredvalue, metered value → Metered Value
orgid, org id, Org_ID → Org ID
environmentid, environment id → Environment ID
obmtasktime(s), obm task time(s) → OBM Task Time(s)
```

To add more variations, edit the `column_mapping` dict in:
```python
def normalize_column_names(df):
    column_mapping = {
        'yourvariation': 'Standard Name',
        # Add more as needed
    }
```

## Streamlit App Configuration

Located in: `app.py`

### Page Configuration
```python
st.set_page_config(
    page_title="Informatica Usage Consolidator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)
```

**Customizable Options**:
- `page_title`: Title shown in browser tab
- `page_icon`: Emoji or icon shown in tab
- `layout`: "centered" or "wide" (affects sidebar behavior)
- `initial_sidebar_state`: "auto", "expanded", or "collapsed"

### File Upload Configuration
Currently accepts:
- Excel files (.xlsx, .xls)

To add CSV support, modify:
```python
uploaded_files = st.file_uploader(
    "Select Excel files to consolidate",
    type=["xlsx", "xls", "csv"],  # Add "csv" here
    accept_multiple_files=True,
)
```

### Export Format Configuration
Currently supports:
- Excel (.xlsx)
- CSV (.csv)
- Summary Report (multi-sheet Excel)

Add JSON export by extending `display_export_options()`:
```python
json_data = df.to_json(orient='records')
st.download_button(
    label="📥 Download JSON",
    data=json_data,
    file_name=f"informatica_consolidated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    mime="application/json",
    use_container_width=True
)
```

## Environment Variables (Optional)

You can set these environment variables to override defaults:

```bash
export IPU_CONVERSION_FACTOR=2.0
export COST_PER_IPU_MONTH=5.0
streamlit run app.py
```

To use in code, update `app.py`:
```python
import os
ipu_factor = float(os.getenv('IPU_CONVERSION_FACTOR', 1.0))
cost_per_ipu = float(os.getenv('COST_PER_IPU_MONTH', 1.0))
```

## Performance Tuning

### Large File Handling
For files with 500,000+ rows:
1. **Process in Batches**: Upload 10-20 files at a time
2. **Monitor Memory**: Watch system memory usage
3. **Close Other Apps**: Free up RAM for processing
4. **Increase Timeout** (if needed):
   ```bash
   streamlit run app.py --client.toolbarMode=minimal --logger.level=error
   ```

### DataFrame Memory Optimization
Edit `processing.py` to reduce columns:
```python
# Only keep necessary columns
keep_cols = ['Task ID', 'Task Run ID', 'Status', 'IPUs', 'Cost/IPU/Month']
df = df[keep_cols]
```

## Testing Configuration

To test with sample data, create `test_data.py`:
```python
import pandas as pd
from io import BytesIO

def create_sample_excel():
    """Create sample Informatica data for testing"""
    data = {
        'Task ID': [1, 2, 3],
        'Task Name': ['ETL_1', 'ETL_2', 'ETL_3'],
        'Task Type': ['SQL', 'SQL', 'Python'],
        'Org ID': ['1001', '1001', '1002'],
        'Start Time': pd.date_range('2024-01-01', periods=3),
        'End Time': pd.date_range('2024-01-01', periods=3) + pd.Timedelta(hours=1),
        'Metered Value': [100, 200, 150],
        'Status': ['Success', 'Success', 'Failed'],
    }
    return pd.DataFrame(data)
```

## Advanced Customization

### Add Custom Calculated Columns
Edit `calculations.py` and `processing.py`:
```python
def calculate_custom_metric(df):
    """Custom metric calculation"""
    df['My_Metric'] = df['IPUs'] * df['Cores Used']
    return df
```

### Extend Summary Reports
Edit `display_summaries()` in `app.py`:
```python
with tab5:  # New tab
    if 'Status' in df.columns:
        summary = get_summary_by_group(df, 'Status')
        st.dataframe(summary, use_container_width=True)
```

### Add Database Export
Add to `processing.py`:
```python
def export_to_database(df, connection_string):
    """Export to SQL database"""
    # Add sqlalchemy code here
    pass
```

---

**Pro Tip**: Make a backup of your modified files before updating the app!
