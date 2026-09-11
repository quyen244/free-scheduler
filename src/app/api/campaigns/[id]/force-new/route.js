import { NextResponse } from "next/server";

import { getAppSession } from "@/lib/local-mode";
import { forceNewCampaign } from "@/lib/campaigns";
import { campaignErrorResponse, idempotencyKeyFrom, serializeCampaign } from "@/lib/campaign-http";

// POST: retire this campaign and open a distinct one on the same source.
//
// The retired campaign keeps its id, its revisions, its attempts, and its whole
// event history; it gains a forward link to the replacement. Clicking twice
// returns the same replacement rather than opening a third campaign.
export async function POST(req, { params }) {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    // Next 16: params is a Promise
    const { id } = await params;
    const body = await req.json().catch(() => ({}));

    const result = await forceNewCampaign({
      campaignId: id,
      reason: body.reason ?? null,
      creationKey: idempotencyKeyFrom(req, body),
    });

    return NextResponse.json(
      {
        campaign: serializeCampaign(result.campaign),
        previousCampaignId: result.previous?.id ?? null,
        created: result.created,
        reason: result.reason,
      },
      { status: result.created ? 201 : 200 },
    );
  } catch (error) {
    return campaignErrorResponse(error, "[FORCE_NEW_CAMPAIGN_ERROR]");
  }
}
