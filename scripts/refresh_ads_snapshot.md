# Refreshing the Facebook ads snapshot

The dashboard pulls Facebook ad impressions/spend from `data/ads_facebook.json`. CI can't
talk to the Windsor.ai MCP directly, so this snapshot is committed to the repo and refreshed
on demand.

## To refresh (ask Claude in this session)

> "Re-pull the latest Facebook ad data from Windsor and overwrite `data/ads_facebook.json`."

Claude will:
1. Call `mcp__windsor.get_data` with `connector=facebook`, `fields=["campaign","date","impressions","spend","clicks","reach"]`, `date_preset=last_2years`
2. Copy the saved tool-result file into `data/ads_facebook.json`
3. Commit on a feature branch → PR → merge → triggers Pages redeploy

Future enhancement: wire the Windsor.ai REST API into the CI workflow with an encrypted
secret so refresh is automatic.
