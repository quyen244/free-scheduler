import { NextResponse } from "next/server";

import { CampaignError } from "@/lib/campaigns";

/**
 * Turn a domain failure into the typed response shape the automation services
 * already use, so a caller can branch on `error_code` and `retryable` instead
 * of parsing prose. Anything that is not a CampaignError is a bug, not a
 * product state, and stays a 500 with no internal detail leaked.
 */
export function campaignErrorResponse(error, tag) {
  if (error instanceof CampaignError) {
    return NextResponse.json(error.toJSON(), { status: error.status });
  }
  console.error(tag, error);
  return NextResponse.json(
    { error: "Internal Server Error", error_code: "internal_error", retryable: true },
    { status: 500 },
  );
}

/**
 * The operator's de-duplication token, if they sent one. `Idempotency-Key` is
 * the conventional header; the body form exists so an n8n HTTP node that only
 * builds JSON can use it too.
 */
export function idempotencyKeyFrom(req, body) {
  return req.headers.get("idempotency-key") || body?.idempotencyKey || null;
}

/** Campaign JSON for the review UI: state, stage, and why it stopped. */
export function serializeCampaign(campaign) {
  return {
    id: campaign.id,
    state: campaign.state,
    stage: campaign.stage,
    attemptOrdinal: campaign.attemptOrdinal,
    sourceVideoId: campaign.sourceVideoId,
    failureCode: campaign.failureCode,
    failureMessage: campaign.failureMessage,
    supersededById: campaign.supersededById,
    isLive: campaign.activeSourceKey !== null,
    createdAt: campaign.createdAt,
    updatedAt: campaign.updatedAt,
  };
}
