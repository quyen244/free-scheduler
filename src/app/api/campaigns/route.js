import { NextResponse } from "next/server";

import { getAppSession } from "@/lib/local-mode";
import { prisma } from "@/lib/prisma";
import { startCampaign } from "@/lib/campaigns";
import { campaignErrorResponse, serializeCampaign } from "@/lib/campaign-http";

// GET: campaigns, newest first, with the source each one re-ups.
export async function GET() {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    const campaigns = await prisma.campaign.findMany({
      orderBy: { createdAt: "desc" },
      include: { sourceVideo: true },
    });

    return NextResponse.json(
      campaigns.map((campaign) => ({
        ...serializeCampaign(campaign),
        source: {
          sourceUrl: campaign.sourceVideo.sourceUrl,
          sourceHash: campaign.sourceVideo.sourceHash,
          platformVideoId: campaign.sourceVideo.platformVideoId,
          durationS: campaign.sourceVideo.durationS,
        },
      })),
    );
  } catch (error) {
    return campaignErrorResponse(error, "[GET_CAMPAIGNS_ERROR]");
  }
}

// POST: start a campaign for a source. A source that already has a live
// campaign returns that campaign with 200, never a second one — choosing
// between continuing and replacing it is the operator's call, made through
// /resume or /force-new.
export async function POST(req) {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    const body = await req.json();
    const result = await startCampaign({
      sourceUrl: body.sourceUrl,
      sourceHash: body.sourceHash,
      platformVideoId: body.platformVideoId ?? null,
      durationS: body.durationS ?? null,
      width: body.width ?? null,
      height: body.height ?? null,
      creationKey: req.headers.get("idempotency-key") || body.creationKey || null,
    });

    return NextResponse.json(
      { campaign: serializeCampaign(result.campaign), created: result.created, reason: result.reason },
      { status: result.created ? 201 : 200 },
    );
  } catch (error) {
    return campaignErrorResponse(error, "[POST_CAMPAIGNS_ERROR]");
  }
}
