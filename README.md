# ChEMBL MCP Server

A Model Context Protocol (MCP) server exposing ChEMBL's public bioactivity
database as tools usable by Claude and ChatGPT.

## Tools

| Tool | Purpose |
|---|---|
| `search_compounds` | Search ChEMBL by compound name/synonym |
| `get_compound_bioactivity` | Get bioactivity records for a ChEMBL ID |
| `get_target_compounds` | Get active compounds against a target |
| `get_admet_properties` | Get computed physicochemical/ADMET properties |

## Run locally

```bash
pip install -r requirements.txt
python3 server.py
```

Server starts on `0.0.0.0:8000`, MCP endpoint at `/mcp` (streamable HTTP).

## IT deployment checklist

1. **Host**: Deploy on a small VM/container (Railway, Render, AWS ECS, or
   internal cloud). 1 vCPU / 512MB RAM is sufficient — server only proxies
   to ChEMBL's public REST API, no local DB.
2. **Public HTTPS**: Expose at a subdomain, e.g. `chembl-mcp.excelra.com`,
   with TLS (Let's Encrypt or managed cert).
3. **Reverse proxy**: nginx/Caddy → forward to `localhost:8000`.
4. **Env/firewall**: outbound HTTPS to `www.ebi.ac.uk` must be allowed.
5. **No auth required for MVP** (ChEMBL data is public). Add an API key
   header check in `server.py` later if you want to gate access.

## Connecting to Claude / ChatGPT

- **Claude.ai / Claude Desktop**: Settings → Connectors → Add custom
  connector → enter `https://chembl-mcp.excelra.com/mcp`
- **ChatGPT**: Settings → Connectors → Advanced → Developer Mode → Add
  remote MCP server → same URL

## Next steps / extensions

- Add `get_drug_indications`, `get_similar_compounds` (structure similarity)
- Cache common ChEMBL responses (Redis) to reduce latency/rate-limit risk
- Swap public REST API for a local ChEMBL Postgres mirror if query volume
  grows (faster, no rate limits)
- This same scaffold pattern (FastMCP + REST wrapper) is directly reusable
  for a future GOSTAR MCP server
