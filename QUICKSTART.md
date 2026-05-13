# Quick Start Guide

## Installation & Setup (2 minutes)

### Step 1: Install Dependencies
```bash
cd /Users/nathanfelix/InformaticaLogging
python3 -m pip install -r requirements.txt
```

### Step 2: Run the App
```bash
streamlit run app.py
```

The app will automatically open in your browser at `http://localhost:8501`

## First Time Usage

1. **Upload Excel Files**: Click "Select Excel files" or drag & drop multiple files
2. **Configure Settings** (optional):
   - Adjust IPU Conversion Factor in sidebar
   - Set Cost per IPU/Month in sidebar
   - Add Org ID mappings if needed
3. **Process Files**: Click "Process Files" button
4. **Review Results**:
   - View data preview with pagination
   - Check duplicate analysis
   - Review status summaries
5. **Export**: Download as Excel, CSV, or Summary Report

## What Each Module Does

### `app.py` - User Interface
- Web interface built with Streamlit
- File upload handling
- Data visualization
- Export functionality

### `processing.py` - Data Pipeline
- Reads Excel files
- Normalizes column names across different formats
- Merges multiple DataFrames efficiently
- Adds calculated columns
- Detects duplicates
- Creates summary reports

### `calculations.py` - Business Logic
- Converts Metered Value → IPUs (configurable factor)
- Calculates Cost/IPU/Month (configurable pricing)
- Handles task duration calculations
- Core cost calculations

### `mappings.py` - Configuration
- Maps Org IDs to organization names
- Add/edit mappings at runtime
- Lookup functions

## Key Features

✅ **Supports 500,000+ rows** - Optimized for large datasets
✅ **Drag & drop upload** - Upload multiple files at once
✅ **Auto column normalization** - Handles different naming conventions
✅ **Duplicate detection** - Find duplicate Task Run IDs
✅ **Status analysis** - Count tasks by status
✅ **Summary reports** - Grouped by Org, Environment, Project, Type
✅ **Multiple exports** - Excel, CSV, and summary reports
✅ **Configurable** - IPU factors, pricing, Org mappings
✅ **Dark mode friendly** - Works well in light/dark themes

## Example Workflow

```
1. Have Excel files ready with Informatica usage data
2. pip install -r requirements.txt
3. streamlit run app.py
4. Upload 3-5 Excel files
5. Click "Process Files"
6. Review statistics (duplicates, status counts)
7. Export consolidated data as Excel
8. Use exported file for reporting/analysis
```

## Troubleshooting

**"ModuleNotFoundError: No module named 'pandas'"**
→ Run: `python3 -m pip install -r requirements.txt`

**"File is not a valid Excel file"**
→ Ensure files are .xlsx or .xls format (not CSV)

**App loads slowly with large files**
→ This is normal. The app processes row-by-row aggregations which may take time on 500k+ row datasets. Be patient.

**Org ID not mapping to organization name**
→ Add the mapping in sidebar: "View/Edit Mappings" → "Add New Mapping"

## Contact & Support

For issues or feature requests, check:
- Error messages in the app (Error logging panel)
- README.md for detailed documentation
- Each module has inline comments explaining functions

---
**Ready to use!** Just run `streamlit run app.py` and start uploading your Informatica data.
