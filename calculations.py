"""
Calculations for IPU values and cost metrics.

Handles IPU calculation from Metered Value and cost per IPU/month calculations.
All operations are vectorized for performance with large datasets.
"""

import pandas as pd
import numpy as np


# Configuration constants
IPU_CONVERSION_FACTOR = 0.16  # Metered Value * 0.16 = IPUs
COST_PER_IPU_MONTH = 36.04    # IPUs * 36.04 = Cost per IPU per month


def calculate_ipus(metered_values):
    """
    Calculate IPUs from Metered Values using vectorized operation.
    
    Args:
        metered_values: pandas Series, list, or array of Metered Value numbers
        
    Returns:
        pandas Series with calculated IPU values
    """
    # Ensure we're working with pandas Series for consistent handling
    if not isinstance(metered_values, pd.Series):
        metered_values = pd.Series(metered_values)

    # Ensure we're working with numeric data
    metered_numeric = pd.to_numeric(metered_values, errors='coerce').fillna(0)

    # Apply conversion factor (vectorized)
    ipus = metered_numeric * IPU_CONVERSION_FACTOR

    # Preserve high precision for small IPU values
    return ipus.round(8)


def calculate_cost_per_ipu_month(ipus):
    """
    Calculate cost per IPU per month using vectorized operation.
    
    Args:
        ipus: pandas Series, list, or array of IPU values
        
    Returns:
        pandas Series with calculated cost values
    """
    # Ensure we're working with pandas Series for consistent handling
    if not isinstance(ipus, pd.Series):
        ipus = pd.Series(ipus)

    # Ensure we're working with numeric data
    ipus_numeric = pd.to_numeric(ipus, errors='coerce').fillna(0)

    # Apply cost calculation (vectorized)
    costs = ipus_numeric * COST_PER_IPU_MONTH

    # Preserve reasonable precision for costs
    return costs.round(6)


def calculate_task_duration_minutes(start_time, end_time):
    """
    Calculate task duration in minutes between start and end times.
    Handles missing values gracefully.
    
    Args:
        start_time: pandas Series of start times (datetime compatible)
        end_time: pandas Series of end times (datetime compatible)
        
    Returns:
        pandas Series with duration in minutes
    """
    # Convert to datetime if not already
    start_dt = pd.to_datetime(start_time, errors='coerce', format='mixed')
    end_dt = pd.to_datetime(end_time, errors='coerce', format='mixed')
    
    # Calculate duration
    duration = (end_dt - start_dt).dt.total_seconds() / 60
    
    return duration.fillna(0).astype(float).round(2)


def calculate_cores_cost(cores_used, duration_minutes):
    """
    Calculate cost based on cores used and task duration.
    Adjust multiplier based on your pricing model.
    
    Args:
        cores_used: pandas Series of cores used
        duration_minutes: pandas Series of duration in minutes
        
    Returns:
        pandas Series with calculated core costs
    """
    cores_numeric = pd.to_numeric(cores_used, errors='coerce').fillna(0)
    duration_numeric = pd.to_numeric(duration_minutes, errors='coerce').fillna(0)
    
    # Cost per core per minute (adjust to your model)
    cost_per_core_minute = 0.001
    costs = cores_numeric * duration_numeric * cost_per_core_minute
    
    return costs.round(4)


def set_ipu_conversion_factor(factor):
    """Update the IPU conversion factor at runtime."""
    global IPU_CONVERSION_FACTOR
    IPU_CONVERSION_FACTOR = float(factor)


def set_cost_per_ipu_month(cost):
    """Update the cost per IPU per month at runtime."""
    global COST_PER_IPU_MONTH
    COST_PER_IPU_MONTH = float(cost)
