# Informatica Usage Consolidator

A Streamlit web application for consolidating multiple Informatica usage spreadsheets into a single, normalized dataset with calculated metrics and summary statistics.

## Features

### Data Processing
- **Multiple File Upload**: Support for drag-and-drop upload of multiple Excel files
- **Automatic Normalization**: Handles column name variations across files
- **Efficient Merging**: Optimized for datasets with 500,000+ rows
- **Calculated Columns**:
  - Run Date (extracted from Start Time)
  - IPUs (calculated from Metered Value)
  - Cost/IPU/Month (calculated from IPUs)
  - Org (mapped from Org ID)

### Analysis & Reporting
- **Data Preview**: View processed data with pagination
- **Duplicate Detection**: Identify duplicate Task Run IDs
- **Status Analysis**: Count tasks by Status with visualizations
- **Summary Reports**: Grouped statistics by:
  - Organization
  - Environment
  - Project Name
  - Task Type
- **Column Statistics**: Descriptive statistics for all numeric columns

### Export Options
- **Excel Export**: Download consolidated data as .xlsx file
- **CSV Export**: Download data as .csv file
- **Summary Report**: Multi-sheet Excel report with summaries

### Configuration
- **IPU Conversion Factor**: Adjustable multiplier for IPU calculations
- **Cost Per IPU/Month**: Configurable pricing model
- **Org ID Mappings**: Add/edit custom organization name mappings
- **Error Logging**: Detailed error messages for file processing issues

## Installation

### Prerequisites
- Python 3.8 or higher
- pip package manager

### Setup

1. **Clone or extract the project**
   ```bash
   cd InformaticaLogging
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

## Running the Application

```bash
streamlit run app.py
```

The application will open in your default web browser at `http://localhost:8501`

## Project Structure

```
InformaticaLogging/
├── app.py                 # Main Streamlit application UI
├── processing.py          # Data processing and transformation functions
├── calculations.py        # IPU and cost calculation logic
├── mappings.py           # Org ID mapping configuration
├── requirements.txt      # Python dependencies
└── README.md            # This file
```

### Module Descriptions

#### `app.py`
Main Streamlit application with UI components:
- File upload interface
- Data preview and pagination
- Analysis and statistics display
- Export functionality
- Configuration sidebar

#### `processing.py`
Data processing pipeline:
- `read_excel_file()`: Read Excel files with error handling
- `normalize_column_names()`: Handle column name variations
- `merge_dataframes()`: Efficiently merge multiple DataFrames
- `process_and_merge_files()`: Main orchestration function
- `get_duplicate_task_run_ids()`: Find duplicate records
- `get_failed_task_counts()`: Analyze task statuses
- `get_summary_by_group()`: Create grouped summaries
- `export_to_excel()` / `export_to_csv()`: Export functions

#### `calculations.py`
Vectorized calculation functions:
- `calculate_ipus()`: Convert Metered Value to IPUs
- `calculate_cost_per_ipu_month()`: Calculate monthly costs
- `calculate_task_duration_minutes()`: Calculate execution time
- `calculate_cores_cost()`: Calculate cost by cores
- Configurable conversion factors and pricing

#### `mappings.py`
Organization ID mapping configuration:
- `ORG_MAPPING`: Dictionary of Org ID → Organization name
- `get_org_name()`: Look up organization by ID
- `add_custom_mapping()`: Add mappings at runtime
- Pre-configured example mappings

## Input File Format

Excel files should contain columns such as:
- Task ID
- Task Name
- Task Object Name
- Task Type
- Task Run ID
- Project Name
- Folder Name
- Org ID
- Environment ID
- Environment
- Cores Used
- Start Time
- End Time
- Status
- Metered Value
- Audit Time
- OBM Task Time(s)

**Note**: Column names are automatically normalized, so exact spelling doesn't matter.

## Output Columns

After processing, the consolidated data includes:
- All original input columns (normalized)
- **Org**: Organization name (mapped from Org ID)
- **Run Date**: Date extracted from Start Time
- **IPUs**: Calculated from Metered Value
- **Cost/IPU/Month**: Calculated from IPUs

## Performance Characteristics

- **Memory Usage**: Optimized with vectorized pandas operations
- **Row Capacity**: Successfully processes 500,000+ rows
- **No Row-by-Row Loops**: Uses pandas groupby and apply operations
- **Efficient Concatenation**: Uses `pd.concat()` for merging

## Configuration Guide

### Adjusting IPU Calculations

Edit the conversion factor in `calculations.py`:
```python
IPU_CONVERSION_FACTOR = 1.0  # Adjust this value
```

Or change it at runtime using the app sidebar.

### Adding Organization Mappings

#### Option 1: Edit in Code
Edit `mappings.py`:
```python
ORG_MAPPING = {
    "1001": "Production",
    "1002": "Staging",
    # Add your mappings here
}
```

#### Option 2: Use App UI
1. Click "View/Edit Mappings" in the sidebar
2. Scroll to "Add New Mapping"
3. Enter Org ID and Organization Name
4. Click "Add Mapping"

### Adjusting Pricing Model

Change the cost per IPU in the app sidebar or in `calculations.py`:
```python
COST_PER_IPU_MONTH = 1.0  # Adjust this value
```

## Troubleshooting

### "Error reading file: ..."
- Ensure the file is a valid Excel file (.xlsx or .xls)
- Check that the file is not corrupted
- Verify the file is not open in another application

### Columns not appearing in output
- Check that column names exist in input files
- Use the data preview to see available columns
- Column name variations are automatically handled

### Memory issues with large files
- Process files in batches rather than all at once
- Close other applications to free memory
- Consider splitting very large files

### Incorrect IPU values
- Check the IPU Conversion Factor setting in sidebar
- Verify Metered Value column contains numeric data
- Look for empty or non-numeric values in source data

## Tips for Best Results

1. **Data Cleanliness**: Remove blank rows and columns from Excel files before uploading
2. **Column Consistency**: Ensure consistent column names across files (the app normalizes them, but consistency helps)
3. **Date Format**: Use standard datetime formats for Start Time and End Time columns
4. **Organization Mapping**: Set up complete Org ID mappings before processing
5. **Batch Processing**: For very large datasets, process in multiple batches to avoid memory issues

## Requirements

- **streamlit** (1.28.1+): Web application framework
- **pandas** (2.1.1+): Data processing and analysis
- **openpyxl** (3.10.10+): Excel file reading/writing
- **xlrd** (2.0.1+): Legacy Excel format support
- **python-dateutil** (2.8.2+): Date/time utilities

## Performance Notes

The application is optimized for large datasets:
- Uses vectorized pandas operations instead of loops
- Leverages `groupby()` and `apply()` for aggregations
- Minimizes memory copies during merging
- Efficient data type handling

Tested with datasets containing 500,000+ rows with smooth performance.

## Support & Customization

To customize the application:

1. **Add New Calculated Columns**: Add functions to `calculations.py`
2. **Add New Mappings**: Update `ORG_MAPPING` in `mappings.py`
3. **Modify UI Layout**: Edit section display functions in `app.py`
4. **Add Export Formats**: Extend export options in `app.py`
5. **Custom Validation**: Add validation logic to `processing.py`

## License

Created for Informatica usage consolidation and analysis.

## Version History

**v1.0** (Initial Release)
- Core file upload and consolidation
- Column normalization
- IPU and cost calculations
- Organization mapping
- Duplicate detection
- Summary reporting
- Multi-format export
