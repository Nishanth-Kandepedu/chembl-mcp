"""
ChEMBL MCP Server
Exposes 4 tools over ChEMBL's public REST API via streamable HTTP MCP transport.

Tools:
  - search_compounds      : search ChEMBL compounds by name/synonym
  - get_compound_bioactivity : bioactivity records for a ChEMBL compound ID
  - get_target_compounds  : top active compounds for a given target
  - get_admet_properties  : computed physicochemical/ADMET-relevant properties

Run:
  python3 server.py
Server listens on 0.0.0.0:8000 with streamable HTTP at /mcp
"""

import time
import httpx
from mcp.server.fastmcp import FastMCP

import os

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"

# Railway (and most PaaS) inject PORT; default to 8000 for local dev.
mcp = FastMCP("chembl-mcp", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

client = httpx.Client(timeout=15.0, headers={"Accept": "application/json"})

MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.0  # multiplied by attempt number


def _get(path: str, params: dict | None = None) -> dict:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get(f"{CHEMBL_BASE}{path}", params=params or {})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_exc = exc
            if status == 429 or status >= 500:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    continue
            raise
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise
    raise last_exc


@mcp.tool()
def get_database_stats() -> dict:
    """Get the current total compound (molecule) count in ChEMBL.
    Useful for answering 'how many compounds does ChEMBL have' with a
    live, current figure rather than stale knowledge.
    """
    molecules = _get("/molecule.json", {"limit": 1})
    return {
        "total_compounds": molecules.get("page_meta", {}).get("total_count"),
        "source": "ChEMBL REST API (live)",
    }


@mcp.tool()
def search_compounds(query: str, limit: int = 10) -> dict:
    """Search ChEMBL for compounds by name or synonym.

    Args:
        query: Compound name, synonym, or partial name to search for.
        limit: Max number of results to return (default 10, max 25).
    """
    limit = min(max(limit, 1), 25)
    data = _get(
        "/molecule.json",
        {
            "molecule_synonyms__molecule_synonym__icontains": query,
            "limit": limit,
        },
    )
    results = []
    for m in data.get("molecules", []):
        results.append({
            "chembl_id": m.get("molecule_chembl_id"),
            "pref_name": m.get("pref_name"),
            "molecule_type": m.get("molecule_type"),
            "max_phase": m.get("max_phase"),
            "smiles": (m.get("molecule_structures") or {}).get("canonical_smiles"),
        })
    return {"query": query, "count": len(results), "results": results}


@mcp.tool()
def get_compound_bioactivity(chembl_id: str, limit: int = 20) -> dict:
    """Get bioactivity (assay) records for a given ChEMBL compound ID.

    Args:
        chembl_id: ChEMBL molecule ID, e.g. 'CHEMBL25'.
        limit: Max number of activity records to return (default 20, max 50).
    """
    limit = min(max(limit, 1), 50)
    data = _get(
        "/activity.json",
        {
            "molecule_chembl_id": chembl_id,
            "limit": limit,
        },
    )
    results = []
    for a in data.get("activities", []):
        results.append({
            "target_chembl_id": a.get("target_chembl_id"),
            "target_name": a.get("target_pref_name"),
            "assay_type": a.get("assay_type"),
            "standard_type": a.get("standard_type"),
            "standard_value": a.get("standard_value"),
            "standard_units": a.get("standard_units"),
            "standard_relation": a.get("standard_relation"),
            "document_chembl_id": a.get("document_chembl_id"),
        })
    return {"chembl_id": chembl_id, "count": len(results), "activities": results}


@mcp.tool()
def get_target_compounds(target_chembl_id: str, limit: int = 20, max_value_nm: float | None = None) -> dict:
    """Get compounds with bioactivity data against a given ChEMBL target.

    Args:
        target_chembl_id: ChEMBL target ID, e.g. 'CHEMBL279' (VEGFR2).
        limit: Max number of activity records to return (default 20, max 50).
        max_value_nm: Optional filter - only return activities with standard_value
                      (assumed nM, e.g. IC50/Ki) at or below this number.
    """
    limit = min(max(limit, 1), 50)
    params = {
        "target_chembl_id": target_chembl_id,
        "limit": limit,
        "order_by": "standard_value",
    }
    if max_value_nm is not None:
        params["standard_value__lte"] = max_value_nm
        params["standard_units"] = "nM"

    data = _get("/activity.json", params)
    results = []
    for a in data.get("activities", []):
        results.append({
            "molecule_chembl_id": a.get("molecule_chembl_id"),
            "standard_type": a.get("standard_type"),
            "standard_value": a.get("standard_value"),
            "standard_units": a.get("standard_units"),
            "standard_relation": a.get("standard_relation"),
            "assay_description": a.get("assay_description"),
        })
    return {"target_chembl_id": target_chembl_id, "count": len(results), "activities": results}


@mcp.tool()
def get_admet_properties(chembl_id: str) -> dict:
    """Get computed physicochemical / drug-likeness (ADMET-relevant) properties
    for a given ChEMBL compound ID (e.g. molecular weight, LogP, HBD/HBA, PSA,
    Lipinski rule-of-five violations).

    Args:
        chembl_id: ChEMBL molecule ID, e.g. 'CHEMBL25'.
    """
    data = _get(f"/molecule/{chembl_id}.json")
    props = data.get("molecule_properties") or {}
    structures = data.get("molecule_structures") or {}
    return {
        "chembl_id": chembl_id,
        "pref_name": data.get("pref_name"),
        "canonical_smiles": structures.get("canonical_smiles"),
        "molecular_weight": props.get("full_mwt"),
        "alogp": props.get("alogp"),
        "hba": props.get("hba"),
        "hbd": props.get("hbd"),
        "psa": props.get("psa"),
        "rtb": props.get("rtb"),
        "num_ro5_violations": props.get("num_ro5_violations"),
        "qed_weighted": props.get("qed_weighted"),
        "aromatic_rings": props.get("aromatic_rings"),
    }


if __name__ == "__main__":
    # Streamable HTTP transport, mounted at /mcp by default
    mcp.run(transport="streamable-http")
