# Step 1 acceptance contract

This folder is an executable oracle for the desired pipeline behavior. It does
not download media, call OpenAI, modify n8n, or upload to a social platform.

Run it from the repository root:

```powershell
python -m unittest discover -s automation/acceptance-contract -p "test_*.py" -v
```

The fixtures freeze the 5:00, 9:00, 10:00, 13:42, and 20:00 source cases. The
contract tests source validation, chunk topology, metadata shape, media ratios,
brand reuse, target fan-out, approval, revision invalidation, and TikTok draft
semantics. Later production services must produce snapshots that satisfy the
same rules.

Snapshots are `step1.v2`. Every delivery asset is 1920x1080 and each chunk
asset names its brand's whole asset in `lineage_asset_id`, matching the
landscape-chunk delivery decision and `media-manifest.v2`. The `step1.v1`
shape - `assets.vertical`, 1080x1920, no lineage - described the retired
vertical topology and is no longer produced or accepted.
