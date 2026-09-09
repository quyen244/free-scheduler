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
