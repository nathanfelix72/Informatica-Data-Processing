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

# Canonical base orgs shown in the upload UI (Task Usage).
BASE_ORGS = [
    "BYU-Dev",
    "BYU-Int",
    "BYU-Prod",
    "BYU-Campus-Int",
    "BYU-Campus-Prod",
    "CES-Prod",
    "CES-Sandbox",
]

# Mass Ingestion is tracked as its own org (same base name, distinct series).
MASS_INGESTION_ORG_SUFFIX = " Mass Ingestion"

# Billing / governance parents and their Informatica child orgs.
PARENT_ORG_CHILDREN = {
    "CES-Prod": ["BYU-Prod", "BYU-Campus-Prod"],
    "CES-Sandbox": ["BYU-Dev", "BYU-Int", "BYU-Campus-Int"],
}

CHILD_TO_PARENT = {
    child: parent
    for parent, children in PARENT_ORG_CHILDREN.items()
    for child in children
}


def base_org_name(org_name):
    """Strip Mass Ingestion suffix so TU and MI map to the same base org."""
    text = str(org_name).strip() if org_name is not None else ""
    if not text:
        return "Unknown"
    if text.endswith(MASS_INGESTION_ORG_SUFFIX):
        return text[: -len(MASS_INGESTION_ORG_SUFFIX)].strip() or "Unknown"
    return text


def parent_org_name(org_name):
    """Map a child/base org (or its MI twin) to CES-Prod / CES-Sandbox."""
    base = base_org_name(org_name)
    if base in PARENT_ORG_CHILDREN:
        return base
    return CHILD_TO_PARENT.get(base, "Other")


def expand_org_focus(selection):
    """Resolve a Quick Answers focus into concrete org labels to include.

    - All orgs / None → None (no filter)
    - CES-Prod / CES-Sandbox → all children + Mass Ingestion twins
    - A specific label → only that label
    """
    if selection is None or selection in ("All orgs", "", "All"):
        return None
    text = str(selection).strip()
    if text in PARENT_ORG_CHILDREN:
        children = PARENT_ORG_CHILDREN[text]
        return children + [mass_ingestion_org_name(child) for child in children]
    return [text]


def get_focus_options(available_orgs=None):
    """Focus dropdown: All, parents, then concrete org series present in data."""
    options = ["All orgs", "CES-Prod", "CES-Sandbox"]
    if available_orgs:
        for org in sorted({str(o) for o in available_orgs if o is not None and str(o).strip()}):
            if org not in options:
                options.append(org)
    return options


def mass_ingestion_org_name(org_name):
    """Return the Mass Ingestion org label for a base org name."""
    base = str(org_name).strip() if org_name is not None else ""
    if not base:
        base = "Unknown"
    if base.endswith(MASS_INGESTION_ORG_SUFFIX):
        return base
    return f"{base}{MASS_INGESTION_ORG_SUFFIX}"


def is_mass_ingestion_org(org_name):
    """True when the org label is already a Mass Ingestion org."""
    text = str(org_name).strip() if org_name is not None else ""
    return text.endswith(MASS_INGESTION_ORG_SUFFIX)


def get_all_org_options():
    """Base orgs plus Mass Ingestion counterparts for upload / filters."""
    options = []
    for org in BASE_ORGS:
        options.append(org)
        options.append(mass_ingestion_org_name(org))
    return options


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
