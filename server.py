"""
ChEMBL + UniProtKB MCP Server
Exposes tools over ChEMBL's and UniProtKB's public REST APIs via
streamable HTTP MCP transport.

Tools:
  - get_database_stats       : live ChEMBL counts (compounds/activities/targets/assays/documents)
  - search_compounds         : search ChEMBL compounds by name/synonym
  - get_compound_bioactivity  : bioactivity records for a ChEMBL compound ID
  - get_compound_mechanism    : mechanism of action + target + action type (e.g. "PI4K inhibitor")
  - get_target_compounds      : top active compounds for a given target
  - get_physchem_properties   : calculated physicochemical/drug-likeness properties (NOT experimental ADMET)
  - get_compound_by_smiles    : exact structure lookup via SMILES
  - get_similar_compounds     : structure similarity search
  - get_drug_indications      : therapeutic indications for a compound
  - get_target_info           : target metadata + UniProt cross-references
  - get_uniprot_entry         : core UniProtKB protein info (name, gene, function)
  - get_uniprot_features      : UniProtKB sequence features (domains, sites, etc.)

Run:
  python3 server.py
Server listens on 0.0.0.0:8000 with streamable HTTP at /mcp
"""

import time
import asyncio
import httpx
from mcp.server.fastmcp import FastMCP

import os

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"
UNIPROT_BASE = "https://rest.uniprot.org/uniprotkb"

# Railway (and most PaaS) inject PORT; default to 8000 for local dev.
mcp = FastMCP("chembl-mcp", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

client = httpx.AsyncClient(timeout=15.0, headers={"Accept": "application/json"})

MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.0  # multiplied by attempt number


async def _get(path: str, params: dict | None = None, base: str = CHEMBL_BASE) -> dict:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(f"{base}{path}", params=params or {})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_exc = exc
            if status == 429 or status >= 500:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    continue
            raise
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise
    raise last_exc


@mcp.tool()
async def get_database_stats(metric: str = "compounds") -> dict:
    """Get a live total count from ChEMBL for a given metric.

    Args:
        metric: Which count to retrieve. One of:
            - "compounds" (total distinct molecules)
            - "activities" (total bioactivity records)
            - "targets" (total drug targets)
            - "assays" (total assays)
            - "documents" (total source documents/papers/patents)
    """
    endpoint_map = {
        "compounds": "/molecule.json",
        "activities": "/activity.json",
        "targets": "/target.json",
        "assays": "/assay.json",
        "documents": "/document.json",
    }
    metric = metric.lower().strip()
    if metric not in endpoint_map:
        return {
            "error": f"Unknown metric '{metric}'. Choose one of: {list(endpoint_map.keys())}"
        }
    data = await _get(endpoint_map[metric], {"limit": 1})
    return {
        "metric": metric,
        "total_count": data.get("page_meta", {}).get("total_count"),
        "source": "ChEMBL REST API (live)",
    }


@mcp.tool()
async def search_compounds(query: str, limit: int = 10) -> dict:
    """Search ChEMBL for compounds by name or synonym.

    Args:
        query: Compound name, synonym, or partial name to search for.
        limit: Max number of results to return (default 10, max 25).
    """
    limit = min(max(limit, 1), 25)
    data = await _get(
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
async def get_compound_bioactivity(chembl_id: str, limit: int = 20) -> dict:
    """Get bioactivity (assay) records for a given ChEMBL compound ID.

    Args:
        chembl_id: ChEMBL molecule ID, e.g. 'CHEMBL25'.
        limit: Max number of activity records to return (default 20, max 50).
    """
    limit = min(max(limit, 1), 50)
    data = await _get(
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
async def get_compound_mechanism(chembl_id: str) -> dict:
    """Get mechanism of action information for a ChEMBL compound (mainly
    populated for approved/clinical drugs): target, action type (e.g.
    INHIBITOR, ANTAGONIST, AGONIST), and mechanism description.

    Args:
        chembl_id: ChEMBL molecule ID, e.g. 'CHEMBL941'.
    """
    data = await _get("/mechanism.json", {"molecule_chembl_id": chembl_id})
    results = []
    for m in data.get("mechanisms", []):
        results.append({
            "target_chembl_id": m.get("target_chembl_id"),
            "mechanism_of_action": m.get("mechanism_of_action"),
            "action_type": m.get("action_type"),
            "mechanism_comment": m.get("mechanism_comment"),
            "direct_interaction": m.get("direct_interaction"),
            "disease_efficacy": m.get("disease_efficacy"),
        })
    return {"chembl_id": chembl_id, "count": len(results), "mechanisms": results}


@mcp.tool()
async def get_target_compounds(target_chembl_id: str, limit: int = 20, max_value_nm: float | None = None) -> dict:
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

    data = await _get("/activity.json", params)
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
async def get_physchem_properties(chembl_id: str) -> dict:
    """Get computed physicochemical / drug-likeness properties for a given
    ChEMBL compound ID (molecular weight, LogP, HBD/HBA, PSA, rotatable
    bonds, Lipinski rule-of-five violations, QED).

    Note: these are calculated physicochemical descriptors, NOT experimental
    ADMET (absorption/distribution/metabolism/excretion/toxicity) data.
    ChEMBL does not provide ADMET predictions via this endpoint.

    Args:
        chembl_id: ChEMBL molecule ID, e.g. 'CHEMBL25'.
    """
    data = await _get(f"/molecule/{chembl_id}.json")
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


@mcp.tool()
async def get_compound_by_smiles(smiles: str, limit: int = 5) -> dict:
    """Look up ChEMBL compounds by exact or similar SMILES structure.
    Uses ChEMBL's similarity search (100% = exact match).

    Args:
        smiles: SMILES string of the query structure.
        limit: Max number of results to return (default 5, max 15).
    """
    limit = min(max(limit, 1), 15)
    # similarity/{smiles}/{threshold}
    data = await _get(f"/similarity/{smiles}/100.json", {"limit": limit})
    results = []
    for m in data.get("molecules", []):
        results.append({
            "chembl_id": m.get("molecule_chembl_id"),
            "pref_name": m.get("pref_name"),
            "similarity": m.get("similarity"),
            "smiles": (m.get("molecule_structures") or {}).get("canonical_smiles"),
            "max_phase": m.get("max_phase"),
        })
    return {"query_smiles": smiles, "count": len(results), "results": results}


@mcp.tool()
async def get_similar_compounds(smiles: str, threshold: int = 80, limit: int = 10) -> dict:
    """Find ChEMBL compounds structurally similar to a given SMILES.

    Args:
        smiles: SMILES string of the query structure.
        threshold: Similarity threshold percentage (default 80, range 40-100).
        limit: Max number of results to return (default 10, max 25).
    """
    threshold = min(max(threshold, 40), 100)
    limit = min(max(limit, 1), 25)
    data = await _get(f"/similarity/{smiles}/{threshold}.json", {"limit": limit})
    results = []
    for m in data.get("molecules", []):
        results.append({
            "chembl_id": m.get("molecule_chembl_id"),
            "pref_name": m.get("pref_name"),
            "similarity": m.get("similarity"),
            "smiles": (m.get("molecule_structures") or {}).get("canonical_smiles"),
            "max_phase": m.get("max_phase"),
        })
    return {"query_smiles": smiles, "threshold": threshold, "count": len(results), "results": results}


@mcp.tool()
async def get_drug_indications(chembl_id: str, limit: int = 20) -> dict:
    """Get approved/investigational therapeutic indications for a ChEMBL compound.

    Args:
        chembl_id: ChEMBL molecule ID, e.g. 'CHEMBL941'.
        limit: Max number of indication records to return (default 20, max 50).
    """
    limit = min(max(limit, 1), 50)
    data = await _get("/drug_indication.json", {"molecule_chembl_id": chembl_id, "limit": limit})
    results = []
    for ind in data.get("drug_indications", []):
        results.append({
            "mesh_heading": ind.get("mesh_heading"),
            "efo_term": ind.get("efo_term"),
            "max_phase_for_ind": ind.get("max_phase_for_ind"),
        })
    return {"chembl_id": chembl_id, "count": len(results), "indications": results}


@mcp.tool()
async def get_target_info(target_chembl_id: str) -> dict:
    """Get metadata for a ChEMBL target: name, organism, target type, and
    cross-references (e.g. UniProt accession) where available.

    Args:
        target_chembl_id: ChEMBL target ID, e.g. 'CHEMBL279' (VEGFR2).
    """
    data = await _get(f"/target/{target_chembl_id}.json")
    components = data.get("target_components", []) or []
    uniprot_accessions = []
    for comp in components:
        for xref in comp.get("target_component_xrefs", []) or []:
            if xref.get("xref_src_db") == "UniProt":
                uniprot_accessions.append(xref.get("xref_id"))
    return {
        "target_chembl_id": target_chembl_id,
        "pref_name": data.get("pref_name"),
        "target_type": data.get("target_type"),
        "organism": data.get("organism"),
        "species_group_flag": data.get("species_group_flag"),
        "uniprot_accessions": uniprot_accessions,
    }


@mcp.tool()
async def get_uniprot_entry(accession: str) -> dict:
    """Get core UniProtKB information for a protein accession: name, gene,
    organism, sequence length, and function description.

    Args:
        accession: UniProtKB accession, e.g. 'P42336' (PIK3CA).
    """
    data = await _get(f"/{accession}.json", base=UNIPROT_BASE)
    protein_desc = (data.get("proteinDescription") or {}).get("recommendedName") or {}
    full_name = (protein_desc.get("fullName") or {}).get("value")
    genes = data.get("genes") or []
    gene_name = (genes[0].get("geneName", {}).get("value") if genes else None)
    organism = (data.get("organism") or {}).get("scientificName")
    sequence = (data.get("sequence") or {})
    function_text = None
    for comment in data.get("comments", []) or []:
        if comment.get("commentType") == "FUNCTION":
            texts = comment.get("texts") or []
            if texts:
                function_text = texts[0].get("value")
            break
    return {
        "accession": accession,
        "protein_name": full_name,
        "gene_name": gene_name,
        "organism": organism,
        "sequence_length": sequence.get("length"),
        "function": function_text,
    }


@mcp.tool()
async def get_uniprot_features(accession: str, feature_type: str | None = None, limit: int = 20) -> dict:
    """Get sequence features (domains, active sites, binding sites, etc.)
    for a UniProtKB protein entry.

    Args:
        accession: UniProtKB accession, e.g. 'P42336' (PIK3CA).
        feature_type: Optional filter, e.g. 'Domain', 'Binding site', 'Active site'.
        limit: Max number of features to return (default 20, max 50).
    """
    limit = min(max(limit, 1), 50)
    data = await _get(f"/{accession}.json", base=UNIPROT_BASE)
    features = data.get("features", []) or []
    if feature_type:
        features = [f for f in features if f.get("type", "").lower() == feature_type.lower()]
    results = []
    for f in features[:limit]:
        loc = f.get("location", {})
        start = (loc.get("start") or {}).get("value")
        end = (loc.get("end") or {}).get("value")
        results.append({
            "type": f.get("type"),
            "description": f.get("description"),
            "start": start,
            "end": end,
        })
    return {"accession": accession, "count": len(results), "features": results}


if __name__ == "__main__":
    # Streamable HTTP transport, mounted at /mcp by default
    mcp.run(transport="streamable-http")
