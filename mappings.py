"""
Org ID to Organization name mappings and configuration.

Customize the ORG_MAPPING dictionary to match your Informatica environments.
Add or modify mappings as needed for your organization.
"""

# Configurable Org ID to Organization name mapping
# Update this dictionary with your actual org IDs and names
ORG_MAPPING = {
    "1001": "BYU-Dev",
    "1002": "BYU-Int",
    "1003": "BYU-Prod",
    "1004": "BYU-Campus-Int",
    "1005": "BYU-Campus-Prod",
    "1006": "CES-Prod",
    "1007": "CES-Sandbox",
    # Add more mappings as needed
}


def get_org_name(org_id):
    """
    Map an Org ID to its organization name.
    
    Args:
        org_id: The Org ID to look up (can be int or str)
        
    Returns:
        Organization name if found, otherwise returns the org_id as string
    """
    org_id_str = str(org_id).strip() if org_id is not None else ""
    return ORG_MAPPING.get(org_id_str, org_id_str if org_id_str else "Unknown")


def add_custom_mapping(org_id, org_name):
    """
    Add or update an Org ID mapping at runtime.
    
    Args:
        org_id: The Org ID (will be converted to string)
        org_name: The organization name
    """
    ORG_MAPPING[str(org_id)] = org_name


def get_all_mappings():
    """Return a copy of all current Org ID mappings."""
    return ORG_MAPPING.copy()
