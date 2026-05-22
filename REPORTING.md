# Historical Reporting & Trend Analysis Guide

## Overview

Your Informatica Logging app now keeps one deduplicated historical table of task rows. That table powers trend analysis and anomaly detection across all uploads.

## New Features

### 1. Save to History
After processing your data:
- Click "Append to Historical Table"
- The app adds new task rows to the historical table
- Duplicate rows are skipped automatically using a row hash

**What gets saved:**
- Task-level rows from the merged spreadsheet output
- Normalized task metadata such as org, project, environment, status, time, IPUs, and cost

### 2. Trend Analysis & Anomaly Detection
Access **"Trend Analysis"** to:

#### Anomaly Detection
- Monitor a metric (Total IPUs, Total Cost, or Task Count)
- Automatically flag days that are statistical outliers
- Adjust sensitivity with the standard deviation threshold (1.0-3.0)
- See which runs deviate from normal patterns

#### Trend Visualization
- View overall usage trends across all runs
- Drill into specific organizations, projects, or environments
- Watch how metrics change over time
- Spot patterns and seasonality

## How to Use

### Workflow Example

1. **Week 1**: Process Informatica logs
   - Upload Excel files
   - Process and review
   - Click "Append to Historical Table"

2. **Week 2**: Repeat the process
   - Upload new data
   - Process and review
   - Click "Append to Historical Table" again

3. **Analysis Time**: Review trends
   - Go to "Trend Analysis" → check for anomalies
   - Drill into specific orgs, projects, environments, or task types to see detailed trends

### Key Questions You Can Now Answer

- **"Is our IPU usage trending up or down?"** → Trend Analysis
- **"Did something unusual happen this week?"** → Anomaly Detection
- **"How did cost/usage change over time?"** → Trend Analysis
- **"Which organization's usage is most volatile?"** → Trend by Organization
- **"Are there any projects with unusual spikes?"** → Trend by Project

## Database Storage

All history is stored in `informatica_reports.db` (SQLite database) in your project folder. This allows:
- Historical tracking with no file management needed
- Fast queries for trends and anomalies
- Reliable data persistence

## Tips

1. **Save regularly** - The more history you accumulate, the better your trend analysis becomes
2. **Check anomalies weekly** - Set aside time to review anomaly detection results
3. **Drill down into outliers** - When you find an anomaly, use the detail views to understand why

## Database Maintenance

- **Append new history**: Re-save the same source data after a rerun; duplicates are skipped automatically
- **Export historical data**: Use the current analysis and trend views to review the accumulated history

## What Data is Stored

### Historical Task Rows
- Task ID, task name, task type, task run ID
- Project, folder, org, environment, and status
- Start and end time
- IPUs, cost, metered value, and cores used

## Future Enhancements

The system is designed to grow. You can add:
- Alerts for anomalies exceeding thresholds
- Export trend reports to PDF
- Custom date ranges for comparisons
- Weekly/monthly rollup reports
- Budget tracking and forecasting
