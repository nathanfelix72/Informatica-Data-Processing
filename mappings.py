"""
Organization name helpers and default seed data.

The live organization catalog lives in the SQLite `organizations` table
(see reports.py). Constants below are used only to seed new databases and
as fallbacks when the DB is unavailable.
"""

# Default Informatica Org ID → name (seeded into organizations.informatica_id)
DEFAULT_ORG_ID_MAPPING = {
    "1001": "BYU-Dev",
    "1002": "BYU-Int",
    "1003": "BYU-Prod",
    "1004": "BYU-Campus-Int",
    "1005": "BYU-Campus-Prod",
    "1006": "CES-Prod",
    "1007": "CES-Sandbox",
}

# Canonical base orgs seeded into the catalog for new installs.
DEFAULT_BASE_ORGS = [
    "BYU-Dev",
    "BYU-Int",
    "BYU-Prod",
    "BYU-Campus-Int",
    "BYU-Campus-Prod",
    "CES-Prod",
    "CES-Sandbox",
]

# Filename tokens (lowercase, alphanumeric match) → base org for upload inference.
DEFAULT_FILENAME_TOKENS = {
    "CES-Sandbox": ["cessb", "cessandbox"],
    "CES-Prod": ["cesprod"],
    "BYU-Campus-Prod": ["byucampusprod", "campusprod"],
    "BYU-Campus-Int": ["byucampusint", "campusint"],
    "BYU-Prod": ["byuprod"],
    "BYU-Int": ["byuint"],
    "BYU-Dev": ["byudev"],
}

# Mass Ingestion is tracked as its own org (same base name, distinct series).
MASS_INGESTION_ORG_SUFFIX = " Mass Ingestion"

# Billing / governance parents and their Informatica child orgs (seed defaults).
DEFAULT_PARENT_ORG_CHILDREN = {
    "CES-Prod": ["BYU-Prod", "BYU-Campus-Prod"],
    "CES-Sandbox": ["BYU-Dev", "BYU-Int", "BYU-Campus-Int"],
}

# Backwards-compatible aliases (prefer DEFAULT_* / DB-backed helpers).
ORG_MAPPING = DEFAULT_ORG_ID_MAPPING
BASE_ORGS = DEFAULT_BASE_ORGS
PARENT_ORG_CHILDREN = DEFAULT_PARENT_ORG_CHILDREN

CHILD_TO_PARENT = {
    child: parent
    for parent, children in DEFAULT_PARENT_ORG_CHILDREN.items()
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


def _parent_lookup():
    """Return (child_to_parent dict, set of parent names) from DB with seed fallback."""
    try:
        from reports import get_org_parent_map
        return get_org_parent_map()
    except Exception:
        parents = set(DEFAULT_PARENT_ORG_CHILDREN)
        return dict(CHILD_TO_PARENT), parents


def parent_org_name(org_name):
    """Map a child/base org (or its MI twin) to its billing parent."""
    base = base_org_name(org_name)
    child_to_parent, parents = _parent_lookup()
    if base in parents:
        return base
    return child_to_parent.get(base, "Other")


def is_parent_org(org_name):
    """True when the name is a billing parent (e.g. CES-Prod)."""
    if org_name is None:
        return False
    _, parents = _parent_lookup()
    return str(org_name).strip() in parents


def expand_org_focus(selection):
    """Resolve a Quick Answers focus into concrete org labels to include.

    - All orgs / None → None (no filter)
    - A parent (CES-Prod / CES-Sandbox) → all children + Mass Ingestion twins
    - A specific label → only that label
    """
    if selection is None or selection in ("All orgs", "", "All"):
        return None
    text = str(selection).strip()
    child_to_parent, parents = _parent_lookup()
    if text in parents:
        children = [
            name
            for name, parent in child_to_parent.items()
            if parent == text and name != text
        ]
        if not children:
            # Seed fallback shape when DB has parent rows but no children yet.
            children = list(DEFAULT_PARENT_ORG_CHILDREN.get(text, []))
        return children + [mass_ingestion_org_name(child) for child in children]
    return [text]


def get_focus_options(available_orgs=None):
    """Focus dropdown: All, parents, then concrete org series present in data."""
    _, parents = _parent_lookup()
    options = ["All orgs"] + sorted(parents)
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
    try:
        from reports import get_all_org_options as db_options
        options = db_options()
        if options:
            return options
    except Exception:
        pass
    options = []
    for org in DEFAULT_BASE_ORGS:
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
    try:
        from reports import get_org_name as db_get_org_name
        return db_get_org_name(org_id)
    except Exception:
        org_id_str = str(org_id).strip() if org_id is not None else ""
        return DEFAULT_ORG_ID_MAPPING.get(
            org_id_str, org_id_str if org_id_str else "Unknown"
        )


def add_custom_mapping(org_id, org_name):
    """
    Add or update an Org ID mapping in the database catalog.

    Args:
        org_id: The Org ID (will be converted to string)
        org_name: The organization name
    """
    from reports import ensure_organization

    ensure_organization(org_name, informatica_id=str(org_id))
    # Keep in-memory seed dict in sync for this process.
    DEFAULT_ORG_ID_MAPPING[str(org_id)] = org_name


def get_all_mappings():
    """Return Org ID → name mappings from the catalog (seed fallback)."""
    try:
        from reports import init_database, DB_PATH
        import sqlite3

        init_database()
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute(
                """
                SELECT informatica_id, name FROM organizations
                WHERE informatica_id IS NOT NULL AND TRIM(informatica_id) != ''
                """
            ).fetchall()
            if rows:
                return {str(oid): name for oid, name in rows}
        finally:
            conn.close()
    except Exception:
        pass
    return DEFAULT_ORG_ID_MAPPING.copy()
