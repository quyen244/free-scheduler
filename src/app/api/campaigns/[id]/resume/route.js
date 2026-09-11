import { NextResponse } from "next/server";

import { getAppSession } from "@/lib/local-mode";
import { resumeCampaign } from "@/lib/campaigns";
import { campaignErrorResponse, idempotencyKeyFrom, serializeCampaign } from "@/lib/campaign-http";

// POST: continue this campaign from the stage it stopped at.
//
// Safe to call twice. A second click, two racing requests, and a retried POST
// all resolve to the attempt that is already running, and the response says so
// through `resumed: false` rather than by failing.
export async function POST(req, { params }) {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    // Next 16: params is a Promise
    const { id } = await params;
    const body = await req.json().catch(() => ({}));

    const result = await resumeCampaign({
      campaignId: id,
      // Omit both to retry the stored revision unchanged. Passing changed
      // inputs is what opens the next revision instead of reusing this one.
      inputs: body.inputs ?? null,
      inputsHash: body.inputsHash ?? null,
      idempotencyKey: idempotencyKeyFrom(req, body),
    });

    return NextResponse.json({
      campaign: serializeCampaign(result.campaign),
      attempt: result.attempt,
      revision: result.revision,
      resumed: result.resumed,
      reason: result.reason,
    });
  } catch (error) {
    return campaignErrorResponse(error, "[RESUME_CAMPAIGN_ERROR]");
  }
}
