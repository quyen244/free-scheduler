import { NextResponse } from "next/server";

import { getAppSession } from "@/lib/local-mode";
import { getCampaign } from "@/lib/campaigns";
import { campaignErrorResponse, serializeCampaign } from "@/lib/campaign-http";

// GET: one campaign with its revisions, attempts, and full audit history. This
// is the durable answer to "where did it stop and why", which is exactly what
// survives an n8n restart.
export async function GET(req, { params }) {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    // Next 16: params is a Promise
    const { id } = await params;
    const campaign = await getCampaign(id);
    if (!campaign) {
      return NextResponse.json(
        { error: "Campaign not found", error_code: "campaign_not_found", retryable: false },
        { status: 404 },
      );
    }

    return NextResponse.json({
      ...serializeCampaign(campaign),
      source: {
        sourceUrl: campaign.sourceVideo.sourceUrl,
        sourceHash: campaign.sourceVideo.sourceHash,
        platformVideoId: campaign.sourceVideo.platformVideoId,
        durationS: campaign.sourceVideo.durationS,
      },
      revisions: campaign.revisions,
      attempts: campaign.attempts,
      events: campaign.events,
    });
  } catch (error) {
    return campaignErrorResponse(error, "[GET_CAMPAIGN_ERROR]");
  }
}
