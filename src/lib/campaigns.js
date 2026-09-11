/**
 * Durable campaign lifecycle: start, resume, and force-new.
 *
 * The app is the system of record for campaign identity and history; n8n and
 * the automation services produce media. That split is why this module never
 * asks n8n what a campaign's state is — a lost execution, a container restart,
 * or a closed browser tab must not lose the answer.
 *
 * Two properties are enforced by the database rather than by careful calling:
 *
 *   - At most one live campaign per source, via the nullable-unique
 *     `Campaign.activeSourceKey`.
 *   - At most one attempt per resume intent, via the unique
 *     `ProcessingAttempt.idempotencyKey`.
 *
 * The conditional `updateMany` calls below are compare-and-swap. Postgres
 * re-evaluates an UPDATE's WHERE clause after it waits for a conflicting row
 * lock, so a racing caller reliably matches zero rows and reports the winner's
 * work instead of starting a duplicate.
 */

import crypto from "node:crypto";

import { prisma } from "./prisma.js";

/** In pipeline order. `Campaign.stage` names the stage that still has to run. */
export const PIPELINE_STAGES = [
  "ingest",
  "transcribe",
  "translate",
  "chunk",
  "metadata",
  "render",
  "approval",
  "publish",
  "complete",
];

/** States that hold the source's live slot, so a duplicate cannot be started. */
export const LIVE_STATES = ["pending", "running", "failed", "awaiting_approval"];

/** States a campaign never leaves. Resume is refused; force-new is the way on. */
export const TERMINAL_STATES = ["published", "superseded"];

/** A typed, machine-readable failure, matching the automation services' shape. */
export class CampaignError extends Error {
  constructor(code, message, { status = 409, retryable = false } = {}) {
    super(message);
    this.name = "CampaignError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }

  toJSON() {
    return { error: this.message, error_code: this.code, retryable: this.retryable };
  }
}

/** The stage after `stage`, or null once there is nothing left to run. */
export function nextStage(stage) {
  const index = PIPELINE_STAGES.indexOf(stage);
  if (index < 0) {
    throw new CampaignError("unknown_stage", "unknown stage: " + stage, { status: 400 });
  }
  return PIPELINE_STAGES[index + 1] ?? null;
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.keys(value)
      .sort()
      .reduce((out, key) => {
        out[key] = canonical(value[key]);
        return out;
      }, {});
  }
  return value;
}

/**
 * Fingerprint of everything that defines a revision's output.
 *
 * Key order must not change the answer, or the same inputs would open a second
 * revision and re-render assets that are already verified.
 */
export function computeInputsHash(inputs) {
  return crypto.createHash("sha256").update(JSON.stringify(canonical(inputs))).digest("hex");
}

function isUniqueViolation(error) {
  return error?.code === "P2002";
}

/**
 * Run `work`; if a unique index rejected it, let `resolve` name the winner.
 *
 * A losing racer is not an error here — the caller asked for one campaign or
 * one attempt and there is exactly one, so returning it is the correct answer
 * to a double-click or a retried POST.
 */
async function resolveOnConflict(work, resolve) {
  try {
    return await work();
  } catch (error) {
    if (!isUniqueViolation(error)) throw error;
    const resolved = await resolve(error);
    if (resolved) return resolved;
    throw error;
  }
}

/**
 * Append to a campaign's history.
 *
 * `sequence` is read-then-written, which is safe because every caller already
 * holds the campaign's row lock from a preceding compare-and-swap. The unique
 * index on (campaignId, sequence) is the backstop if that stops being true.
 */
async function appendEvent(tx, campaignId, { type, fromState = null, toState = null, payload = null }) {
  const { _max } = await tx.campaignEvent.aggregate({
    where: { campaignId },
    _max: { sequence: true },
  });
  return tx.campaignEvent.create({
    data: { campaignId, sequence: (_max.sequence ?? 0) + 1, type, fromState, toState, payload },
  });
}

async function nextAttemptOrdinal(tx, sourceVideoId) {
  const { _max } = await tx.campaign.aggregate({
    where: { sourceVideoId },
    _max: { attemptOrdinal: true },
  });
  return (_max.attemptOrdinal ?? 0) + 1;
}

/**
 * Reuse the revision these inputs already produced, or open the next one.
 *
 * Reuse is what preserves verified assets across a resume: identical inputs
 * resolve to the identical revision row, so the renderer is handed the same
 * manifest instead of a fresh directory to fill. A changed fingerprint opens a
 * new revision and marks the old one superseded — the old row's artifacts,
 * number, and `frozenAt` are left exactly as they were.
 */
async function ensureRevision(tx, campaign, inputsHash) {
  if (!inputsHash) {
    return tx.campaignRevision.findFirst({
      where: { campaignId: campaign.id },
      orderBy: { revisionNumber: "desc" },
    });
  }

  const existing = await tx.campaignRevision.findUnique({
    where: { campaignId_inputsHash: { campaignId: campaign.id, inputsHash } },
  });
  if (existing) return existing;

  const { _max } = await tx.campaignRevision.aggregate({
    where: { campaignId: campaign.id },
    _max: { revisionNumber: true },
  });
  await tx.campaignRevision.updateMany({
    where: { campaignId: campaign.id, state: { not: "superseded" } },
    data: { state: "superseded" },
  });
  return tx.campaignRevision.create({
    data: {
      campaignId: campaign.id,
      revisionNumber: (_max.revisionNumber ?? 0) + 1,
      inputsHash,
    },
  });
}

function inFlightAttempt(tx, campaignId) {
  return tx.processingAttempt.findFirst({
    where: { campaignId, state: "running" },
    orderBy: { attemptNumber: "desc" },
  });
}

/**
 * Begin a campaign for a source, or hand back the live one it already has.
 *
 * Never creates a second live campaign for the same source: that choice belongs
 * to the operator, through `resumeCampaign` or `forceNewCampaign`.
 */
export async function startCampaign({
  sourceUrl,
  sourceHash,
  platformVideoId = null,
  durationS = null,
  width = null,
  height = null,
  creationKey = null,
}) {
  if (!sourceUrl || !sourceHash) {
    throw new CampaignError("invalid_request", "sourceUrl and sourceHash are required", {
      status: 400,
    });
  }

  const identity = { sourceUrl, platformVideoId, durationS, width, height };

  return resolveOnConflict(
    () =>
      prisma.$transaction(async (tx) => {
        const source = await tx.sourceVideo.upsert({
          where: { sourceHash },
          update: Object.fromEntries(
            Object.entries(identity).filter(([, value]) => value !== null),
          ),
          create: { sourceHash, ...identity },
        });

        const live = await tx.campaign.findUnique({ where: { activeSourceKey: source.id } });
        if (live) return { campaign: live, source, created: false, reason: "live_campaign_exists" };

        const attemptOrdinal = await nextAttemptOrdinal(tx, source.id);
        const campaign = await tx.campaign.create({
          data: {
            sourceVideoId: source.id,
            attemptOrdinal,
            activeSourceKey: source.id,
            creationKey: creationKey ?? "start:" + source.id + ":" + attemptOrdinal,
          },
        });
        await appendEvent(tx, campaign.id, {
          type: "campaign_created",
          toState: "pending",
          payload: { sourceHash, attemptOrdinal, origin: "start" },
        });
        return { campaign, source, created: true, reason: null };
      }),
    async () => {
      const source = await prisma.sourceVideo.findUnique({ where: { sourceHash } });
      if (!source) return null;
      const winner = creationKey
        ? await prisma.campaign.findUnique({ where: { creationKey } })
        : await prisma.campaign.findUnique({ where: { activeSourceKey: source.id } });
      return winner
        ? { campaign: winner, source, created: false, reason: "live_campaign_exists" }
        : null;
    },
  );
}

/**
 * Continue an existing campaign from the stage it stopped at.
 *
 * Keeps the campaign's identity, its history, its source, and every revision it
 * has already produced. `inputs` is optional: omit it to retry the stored
 * revision unchanged, pass it to let a changed fingerprint open the next one.
 */
export async function resumeCampaign({
  campaignId,
  inputs = null,
  inputsHash = null,
  idempotencyKey = null,
}) {
  if (!campaignId) {
    throw new CampaignError("invalid_request", "campaignId is required", { status: 400 });
  }
  const fingerprint = inputsHash ?? (inputs ? computeInputsHash(inputs) : null);

  return resolveOnConflict(
    () =>
      prisma.$transaction(async (tx) => {
        const campaign = await tx.campaign.findUnique({ where: { id: campaignId } });
        if (!campaign) {
          throw new CampaignError("campaign_not_found", "no campaign " + campaignId, {
            status: 404,
          });
        }
        if (TERMINAL_STATES.includes(campaign.state)) {
          throw new CampaignError(
            "campaign_not_resumable",
            "campaign is " + campaign.state + "; start a new campaign instead",
          );
        }
        if (campaign.stage === "complete") {
          throw new CampaignError("campaign_not_resumable", "campaign has no remaining stage");
        }

        // Claim the campaign. A second click loses this race and reports the
        // attempt that is already running rather than starting another render.
        const claimed =
          campaign.state === "running"
            ? { count: 0 }
            : await tx.campaign.updateMany({
                where: { id: campaign.id, state: campaign.state },
                data: { state: "running", failureCode: null, failureMessage: null },
              });
        if (claimed.count === 0) {
          const current = await tx.campaign.findUnique({ where: { id: campaignId } });
          return {
            campaign: current,
            attempt: await inFlightAttempt(tx, campaignId),
            revision: null,
            resumed: false,
            reason: "already_running",
          };
        }

        const revision = await ensureRevision(tx, campaign, fingerprint);
        const { _max } = await tx.processingAttempt.aggregate({
          where: { campaignId },
          _max: { attemptNumber: true },
        });
        const attemptNumber = (_max.attemptNumber ?? 0) + 1;
        const attempt = await tx.processingAttempt.create({
          data: {
            campaignId,
            revisionId: revision?.id ?? null,
            attemptNumber,
            stage: campaign.stage,
            idempotencyKey: idempotencyKey ?? "resume:" + campaignId + ":" + attemptNumber,
          },
        });
        await appendEvent(tx, campaignId, {
          type: "campaign_resumed",
          fromState: campaign.state,
          toState: "running",
          payload: {
            stage: campaign.stage,
            attemptNumber,
            revisionNumber: revision?.revisionNumber ?? null,
            reusedRevision: Boolean(revision) && revision.inputsHash === fingerprint,
          },
        });

        return {
          campaign: await tx.campaign.findUnique({ where: { id: campaignId } }),
          attempt,
          revision,
          resumed: true,
          reason: null,
        };
      }),
    async () => {
      const attempt = idempotencyKey
        ? await prisma.processingAttempt.findUnique({ where: { idempotencyKey } })
        : await prisma.processingAttempt.findFirst({
            where: { campaignId, state: "running" },
            orderBy: { attemptNumber: "desc" },
          });
      if (!attempt) return null;
      return {
        campaign: await prisma.campaign.findUnique({ where: { id: campaignId } }),
        attempt,
        revision: null,
        resumed: false,
        reason: "already_running",
      };
    },
  );
}

/**
 * Retire the live campaign for a source and open a distinct one beside it.
 *
 * The retired campaign keeps its id, its ordinal, its revisions, its attempts,
 * and its whole event history; only its lifecycle state changes, and it gains a
 * forward link to its replacement. Nothing is deleted, and the same source hash
 * is deliberately reusable — source identity and campaign execution are
 * separate concerns.
 *
 * Pass `campaignId` from anything an operator clicks. It names the campaign to
 * replace, which is what makes a repeated click idempotent. The `sourceHash`
 * form means "replace whatever is live for this source" and has no such target,
 * so a caller using it should supply `creationKey` to make a retry safe.
 */
export async function forceNewCampaign({
  campaignId = null,
  sourceHash = null,
  creationKey = null,
  reason = null,
}) {
  if (!campaignId && !sourceHash) {
    throw new CampaignError("invalid_request", "campaignId or sourceHash is required", {
      status: 400,
    });
  }

  const loadTarget = async (client) => {
    const previous = await client.campaign.findUnique({ where: { id: campaignId } });
    if (!previous) {
      throw new CampaignError("campaign_not_found", "no campaign " + campaignId, { status: 404 });
    }
    return previous;
  };

  const resolveSourceId = async (client) => {
    if (campaignId) return (await loadTarget(client)).sourceVideoId;
    const source = await client.sourceVideo.findUnique({ where: { sourceHash } });
    if (!source) {
      throw new CampaignError("source_not_found", "no source " + sourceHash, { status: 404 });
    }
    return source.id;
  };

  return resolveOnConflict(
    () =>
      prisma.$transaction(async (tx) => {
        // Naming a campaign means "replace this one". If it already has a
        // replacement the request is satisfied, so a second click returns that
        // replacement instead of retiring it and opening a third campaign.
        if (campaignId) {
          const target = await loadTarget(tx);
          if (target.supersededById) {
            return {
              campaign: await tx.campaign.findUnique({ where: { id: target.supersededById } }),
              previous: target,
              created: false,
              reason: "already_replaced",
            };
          }
        }

        const sourceVideoId = await resolveSourceId(tx);
        const holder = await tx.campaign.findUnique({ where: { activeSourceKey: sourceVideoId } });

        if (holder) {
          const released = await tx.campaign.updateMany({
            where: { id: holder.id, activeSourceKey: sourceVideoId },
            data: { state: "superseded", activeSourceKey: null },
          });
          if (released.count === 0) {
            // A concurrent force-new already retired it, so its replacement is
            // the campaign this caller was asking for.
            const winner = await tx.campaign.findUnique({
              where: { activeSourceKey: sourceVideoId },
            });
            if (winner) {
              return {
                campaign: winner,
                previous: holder,
                created: false,
                reason: "already_replaced",
              };
            }
          } else {
            await appendEvent(tx, holder.id, {
              type: "campaign_superseded",
              fromState: holder.state,
              toState: "superseded",
              payload: { reason, stage: holder.stage },
            });
          }
        }

        const attemptOrdinal = await nextAttemptOrdinal(tx, sourceVideoId);
        const campaign = await tx.campaign.create({
          data: {
            sourceVideoId,
            attemptOrdinal,
            activeSourceKey: sourceVideoId,
            creationKey: creationKey ?? "force-new:" + sourceVideoId + ":" + attemptOrdinal,
          },
        });
        if (holder) {
          await tx.campaign.update({
            where: { id: holder.id },
            data: { supersededById: campaign.id },
          });
        }
        await appendEvent(tx, campaign.id, {
          type: "campaign_created",
          toState: "pending",
          payload: {
            origin: "force_new",
            forcedFrom: holder?.id ?? null,
            reason,
            attemptOrdinal,
          },
        });

        return { campaign, previous: holder, created: true, reason: null };
      }),
    async () => {
      if (creationKey) {
        const byKey = await prisma.campaign.findUnique({ where: { creationKey } });
        if (byKey) {
          return { campaign: byKey, previous: null, created: false, reason: "already_replaced" };
        }
      }
      const sourceVideoId = await resolveSourceId(prisma);
      const winner = await prisma.campaign.findUnique({
        where: { activeSourceKey: sourceVideoId },
      });
      return winner
        ? { campaign: winner, previous: null, created: false, reason: "already_replaced" }
        : null;
    },
  );
}

/**
 * Record that an attempt finished its stage, and move the campaign forward.
 *
 * Idempotent: replaying a completion for an attempt that already finished
 * changes nothing, because a lost callback is a normal event in this pipeline.
 */
export async function succeedAttempt({ attemptId, manifestPath = null }) {
  return prisma.$transaction(async (tx) => {
    const attempt = await tx.processingAttempt.findUnique({ where: { id: attemptId } });
    if (!attempt) {
      throw new CampaignError("attempt_not_found", "no attempt " + attemptId, { status: 404 });
    }

    const settled = await tx.processingAttempt.updateMany({
      where: { id: attemptId, state: "running" },
      data: { state: "succeeded", finishedAt: new Date(), leaseExpiresAt: null },
    });
    if (settled.count === 0) {
      return {
        attempt: await tx.processingAttempt.findUnique({ where: { id: attemptId } }),
        campaign: await tx.campaign.findUnique({ where: { id: attempt.campaignId } }),
        applied: false,
      };
    }

    if (manifestPath && attempt.revisionId) {
      await tx.campaignRevision.update({
        where: { id: attempt.revisionId },
        data: { state: "ready", manifestPath, frozenAt: new Date() },
      });
    }

    const upcoming = nextStage(attempt.stage);
    const finished = upcoming === "complete" || upcoming === null;
    const campaign = await tx.campaign.update({
      where: { id: attempt.campaignId },
      data: {
        stage: upcoming ?? "complete",
        state: finished ? "published" : "pending",
        // Releasing the slot is what makes a published source reusable.
        activeSourceKey: finished ? null : undefined,
        failureCode: null,
        failureMessage: null,
      },
    });
    await appendEvent(tx, attempt.campaignId, {
      type: "stage_succeeded",
      fromState: "running",
      toState: campaign.state,
      payload: {
        stage: attempt.stage,
        nextStage: campaign.stage,
        attemptNumber: attempt.attemptNumber,
      },
    });

    return {
      attempt: await tx.processingAttempt.findUnique({ where: { id: attemptId } }),
      campaign,
      applied: true,
    };
  });
}

/**
 * Record a typed stage failure. The campaign's stage is left untouched, which
 * is what lets a later resume retry exactly where it stopped.
 */
export async function failAttempt({
  attemptId,
  failureCode,
  failureMessage = null,
  retryable = true,
}) {
  if (!failureCode) {
    throw new CampaignError("invalid_request", "failureCode is required", { status: 400 });
  }
  return prisma.$transaction(async (tx) => {
    const attempt = await tx.processingAttempt.findUnique({ where: { id: attemptId } });
    if (!attempt) {
      throw new CampaignError("attempt_not_found", "no attempt " + attemptId, { status: 404 });
    }

    const settled = await tx.processingAttempt.updateMany({
      where: { id: attemptId, state: "running" },
      data: {
        state: "failed",
        failureCode,
        failureMessage,
        retryable,
        finishedAt: new Date(),
        leaseExpiresAt: null,
      },
    });
    if (settled.count === 0) {
      return {
        attempt: await tx.processingAttempt.findUnique({ where: { id: attemptId } }),
        campaign: await tx.campaign.findUnique({ where: { id: attempt.campaignId } }),
        applied: false,
      };
    }

    const campaign = await tx.campaign.update({
      where: { id: attempt.campaignId },
      data: { state: "failed", failureCode, failureMessage },
    });
    await appendEvent(tx, attempt.campaignId, {
      type: "stage_failed",
      fromState: "running",
      toState: "failed",
      payload: {
        stage: attempt.stage,
        failureCode,
        retryable,
        attemptNumber: attempt.attemptNumber,
      },
    });

    return {
      attempt: await tx.processingAttempt.findUnique({ where: { id: attemptId } }),
      campaign,
      applied: true,
    };
  });
}

/** A campaign with everything needed to render its state and its audit trail. */
export function getCampaign(campaignId) {
  return prisma.campaign.findUnique({
    where: { id: campaignId },
    include: {
      sourceVideo: true,
      revisions: { orderBy: { revisionNumber: "asc" } },
      attempts: { orderBy: { attemptNumber: "asc" } },
      events: { orderBy: { sequence: "asc" } },
    },
  });
}
