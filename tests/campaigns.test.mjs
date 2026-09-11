/**
 * Campaign resume and force-new, against the real PostgreSQL schema.
 *
 * These are not mocked. The properties under test — one live campaign per
 * source, one attempt per resume intent — are enforced by unique indexes and by
 * how Postgres re-checks an UPDATE's predicate after waiting for a row lock, so
 * a fake client would test the mock instead of the guarantee.
 *
 * Every case works on its own randomly generated source hash and removes its
 * own rows afterwards. The pre-existing tables (User, Account, Session,
 * ScheduledPost, VerificationToken) are never touched.
 *
 *   node --test tests/
 */

import assert from "node:assert/strict";
import crypto from "node:crypto";
import { after, describe, it } from "node:test";

import "dotenv/config";

// prisma.js opens its pool from DATABASE_URL at import time, and the app's
// DATABASE_URL carries a pgbouncer flag. Tests want the direct connection,
// because a transaction that is silently pooled would not exercise the locking
// behaviour these cases depend on.
process.env.DATABASE_URL = process.env.DIRECT_URL ?? process.env.DATABASE_URL;

const { prisma } = await import("../src/lib/prisma.js");
const {
  CampaignError,
  computeInputsHash,
  failAttempt,
  forceNewCampaign,
  getCampaign,
  nextStage,
  resumeCampaign,
  startCampaign,
  succeedAttempt,
} = await import("../src/lib/campaigns.js");

const hashes = [];

function freshSource() {
  const sourceHash = crypto.randomBytes(16).toString("hex");
  hashes.push(sourceHash);
  return { sourceHash, sourceUrl: "https://youtu.be/test_" + sourceHash.slice(0, 8) };
}

/** A campaign parked at `stage` with a typed failure, ready to be resumed. */
async function failedAt(stage, { inputs = null } = {}) {
  const source = freshSource();
  const { campaign } = await startCampaign(source);
  await prisma.campaign.update({ where: { id: campaign.id }, data: { stage } });

  const { attempt } = await resumeCampaign({ campaignId: campaign.id, inputs });
  await failAttempt({
    attemptId: attempt.id,
    failureCode: "render_probe_failed",
    failureMessage: "moov atom relocation failed",
    retryable: true,
  });
  return { source, campaignId: campaign.id, firstAttempt: attempt };
}

after(async () => {
  for (const sourceHash of hashes) {
    const source = await prisma.sourceVideo.findUnique({ where: { sourceHash } });
    if (!source) continue;
    // Break the self-referencing supersession links before deleting, so the
    // rows do not depend on trigger ordering.
    await prisma.campaign.updateMany({
      where: { sourceVideoId: source.id },
      data: { supersededById: null },
    });
    await prisma.campaign.deleteMany({ where: { sourceVideoId: source.id } });
    await prisma.sourceVideo.delete({ where: { id: source.id } });
  }
  await prisma.$disconnect();
});

describe("starting a campaign", () => {
  it("creates one campaign and records its creation", async () => {
    const source = freshSource();
    const { campaign, created } = await startCampaign(source);

    assert.equal(created, true);
    assert.equal(campaign.state, "pending");
    assert.equal(campaign.stage, "ingest");
    assert.equal(campaign.attemptOrdinal, 1);

    const stored = await getCampaign(campaign.id);
    assert.equal(stored.sourceVideo.sourceHash, source.sourceHash);
    assert.deepEqual(
      stored.events.map((event) => event.type),
      ["campaign_created"],
    );
  });

  it("hands back the live campaign instead of starting a duplicate", async () => {
    const source = freshSource();
    const first = await startCampaign(source);
    const second = await startCampaign(source);

    assert.equal(second.created, false);
    assert.equal(second.reason, "live_campaign_exists");
    assert.equal(second.campaign.id, first.campaign.id);
    assert.equal(await prisma.campaign.count({ where: { sourceVideoId: first.source.id } }), 1);
  });

  it("reuses one source row for the same content hash", async () => {
    const source = freshSource();
    await startCampaign(source);
    await startCampaign({ ...source, sourceUrl: "https://www.youtube.com/watch?v=other" });

    assert.equal(await prisma.sourceVideo.count({ where: { sourceHash: source.sourceHash } }), 1);
  });
});

describe("resuming a campaign", () => {
  it("keeps the same campaign identity", async () => {
    const { campaignId } = await failedAt("render");
    const before = await prisma.campaign.findUnique({ where: { id: campaignId } });

    const { campaign, resumed } = await resumeCampaign({ campaignId });

    assert.equal(resumed, true);
    assert.equal(campaign.id, before.id);
    assert.equal(campaign.sourceVideoId, before.sourceVideoId);
    assert.equal(campaign.attemptOrdinal, before.attemptOrdinal);
    assert.equal(campaign.creationKey, before.creationKey);
    assert.equal(campaign.createdAt.getTime(), before.createdAt.getTime());
  });

  it("retries the stage that failed rather than restarting the pipeline", async () => {
    const { campaignId } = await failedAt("render");

    const { attempt, campaign } = await resumeCampaign({ campaignId });

    assert.equal(attempt.stage, "render");
    assert.equal(campaign.stage, "render");
    assert.equal(campaign.state, "running");
  });

  it("appends to the audit history without rewriting it", async () => {
    const { campaignId } = await failedAt("metadata");
    const before = await prisma.campaignEvent.findMany({
      where: { campaignId },
      orderBy: { sequence: "asc" },
    });

    await resumeCampaign({ campaignId });
    const afterEvents = await prisma.campaignEvent.findMany({
      where: { campaignId },
      orderBy: { sequence: "asc" },
    });

    assert.ok(afterEvents.length > before.length);
    assert.deepEqual(afterEvents.slice(0, before.length), before);
    assert.deepEqual(
      afterEvents.map((event) => event.type),
      ["campaign_created", "campaign_resumed", "stage_failed", "campaign_resumed"],
    );
    assert.deepEqual(
      afterEvents.map((event) => event.sequence),
      [1, 2, 3, 4],
    );
  });

  it("clears the typed failure it is retrying", async () => {
    const { campaignId } = await failedAt("render");
    const failed = await prisma.campaign.findUnique({ where: { id: campaignId } });
    assert.equal(failed.failureCode, "render_probe_failed");

    const { campaign } = await resumeCampaign({ campaignId });

    assert.equal(campaign.failureCode, null);
    assert.equal(campaign.failureMessage, null);
    // The attempt keeps it: the campaign is retrying, the history is not.
    const first = await prisma.processingAttempt.findFirst({
      where: { campaignId, attemptNumber: 1 },
    });
    assert.equal(first.failureCode, "render_probe_failed");
    assert.equal(first.retryable, true);
  });

  it("reuses the existing revision when the inputs have not changed", async () => {
    const inputs = { model: "gpt-5.6-luna", metadataRevision: 4, brand: "mock-brand" };
    const { campaignId, firstAttempt } = await failedAt("render", { inputs });

    const revisionBefore = await prisma.campaignRevision.findUnique({
      where: { id: firstAttempt.revisionId },
    });
    const { attempt, revision } = await resumeCampaign({ campaignId, inputs });

    assert.equal(revision.id, revisionBefore.id);
    assert.equal(revision.revisionNumber, 1);
    assert.equal(attempt.revisionId, revisionBefore.id);
    assert.equal(await prisma.campaignRevision.count({ where: { campaignId } }), 1);
  });

  it("opens the next revision when an input changed, leaving the old one intact", async () => {
    const inputs = { model: "gpt-5.6-luna", metadataRevision: 4 };
    const source = freshSource();
    const { campaign } = await startCampaign(source);
    // Revision 1 has to actually complete before immutability means anything,
    // so this succeeds the render rather than failing it.
    const first = await resumeCampaign({ campaignId: campaign.id, inputs });
    await succeedAttempt({
      attemptId: first.attempt.id,
      manifestPath: "/data/x/outputs/manifest/revision/1.json",
    });
    const campaignId = campaign.id;
    const frozen = await prisma.campaignRevision.findUnique({ where: { id: first.revision.id } });
    assert.ok(frozen.frozenAt, "precondition: revision 1 is frozen");

    const { revision } = await resumeCampaign({
      campaignId,
      inputs: { ...inputs, metadataRevision: 5 },
    });

    assert.equal(revision.revisionNumber, 2);
    assert.notEqual(revision.id, frozen.id);
    assert.equal(revision.inputsHash, computeInputsHash({ ...inputs, metadataRevision: 5 }));

    // Immutability: the previous revision's artifacts are untouched. Only its
    // lifecycle marker moved, which is how the invalidation is recorded.
    const previous = await prisma.campaignRevision.findUnique({ where: { id: frozen.id } });
    assert.equal(previous.manifestPath, frozen.manifestPath);
    assert.equal(previous.inputsHash, frozen.inputsHash);
    assert.equal(previous.revisionNumber, frozen.revisionNumber);
    assert.equal(previous.frozenAt.getTime(), frozen.frozenAt.getTime());
    assert.equal(previous.state, "superseded");
  });

  it("refuses a campaign that has no remaining stage", async () => {
    const source = freshSource();
    const { campaign } = await startCampaign(source);
    await prisma.campaign.update({ where: { id: campaign.id }, data: { stage: "complete" } });

    await assert.rejects(() => resumeCampaign({ campaignId: campaign.id }), (error) => {
      assert.ok(error instanceof CampaignError);
      assert.equal(error.code, "campaign_not_resumable");
      return true;
    });
  });

  it("refuses a superseded campaign with a typed error", async () => {
    const { campaignId } = await failedAt("render");
    await forceNewCampaign({ campaignId, reason: "operator chose a new campaign" });

    await assert.rejects(() => resumeCampaign({ campaignId }), (error) => {
      assert.equal(error.code, "campaign_not_resumable");
      assert.equal(error.retryable, false);
      return true;
    });
  });

  it("reports a missing campaign as not found", async () => {
    await assert.rejects(() => resumeCampaign({ campaignId: "does-not-exist" }), (error) => {
      assert.equal(error.code, "campaign_not_found");
      assert.equal(error.status, 404);
      return true;
    });
  });
});

describe("resume under concurrency", () => {
  it("creates one attempt when two resume requests race", async () => {
    const { campaignId } = await failedAt("render");

    const results = await Promise.all([
      resumeCampaign({ campaignId }),
      resumeCampaign({ campaignId }),
    ]);

    const resumed = results.filter((result) => result.resumed);
    assert.equal(resumed.length, 1, "exactly one caller may claim the campaign");
    assert.equal(results.find((result) => !result.resumed).reason, "already_running");
    assert.equal(
      await prisma.processingAttempt.count({ where: { campaignId, state: "running" } }),
      1,
    );
    // One from the precondition, one from the winner. The loser added none.
    assert.equal(await prisma.processingAttempt.count({ where: { campaignId } }), 2);
  });

  it("creates one attempt when Resume is clicked twice in a row", async () => {
    const { campaignId } = await failedAt("render");

    const first = await resumeCampaign({ campaignId });
    const second = await resumeCampaign({ campaignId });

    assert.equal(first.resumed, true);
    assert.equal(second.resumed, false);
    assert.equal(second.reason, "already_running");
    assert.equal(second.attempt.id, first.attempt.id);
    assert.equal(await prisma.processingAttempt.count({ where: { campaignId } }), 2);
  });

  it("returns the original attempt when a successful POST is retried", async () => {
    const { campaignId } = await failedAt("render");
    const idempotencyKey = "operator-retry-" + crypto.randomUUID();

    const first = await resumeCampaign({ campaignId, idempotencyKey });
    // The client never saw the response and sent the identical request again.
    const replay = await resumeCampaign({ campaignId, idempotencyKey });

    assert.equal(replay.attempt.id, first.attempt.id);
    assert.equal(replay.resumed, false);
    assert.equal(await prisma.processingAttempt.count({ where: { campaignId } }), 2);
  });

  it("keeps attempt numbers unique per campaign at the database level", async () => {
    const { campaignId } = await failedAt("render");
    const { attempt } = await resumeCampaign({ campaignId });

    await assert.rejects(
      () =>
        prisma.processingAttempt.create({
          data: {
            campaignId,
            attemptNumber: attempt.attemptNumber,
            stage: "render",
            idempotencyKey: "hand-written-" + crypto.randomUUID(),
          },
        }),
      (error) => error.code === "P2002",
    );
  });
});

describe("forcing a new campaign", () => {
  it("creates a distinct identity on the same source", async () => {
    const { source, campaignId } = await failedAt("render");

    const { campaign, created } = await forceNewCampaign({ campaignId, reason: "bad narration" });

    assert.equal(created, true);
    assert.notEqual(campaign.id, campaignId);
    assert.equal(campaign.attemptOrdinal, 2);
    assert.equal(campaign.state, "pending");
    assert.equal(campaign.stage, "ingest");

    const stored = await getCampaign(campaign.id);
    assert.equal(stored.sourceVideo.sourceHash, source.sourceHash);
    // The same source hash is deliberately reusable; only the campaign is new.
    assert.equal(await prisma.sourceVideo.count({ where: { sourceHash: source.sourceHash } }), 1);
  });

  it("starts an independent revision chain", async () => {
    const inputs = { model: "gpt-5.6-luna", metadataRevision: 4 };
    const { campaignId } = await failedAt("render", { inputs });

    const { campaign } = await forceNewCampaign({ campaignId });
    const { revision } = await resumeCampaign({ campaignId: campaign.id, inputs });

    assert.equal(revision.revisionNumber, 1, "numbering restarts for a new campaign");
    assert.equal(revision.campaignId, campaign.id);
    // The old campaign still has its own revision 1, with the same inputs.
    const previousRevisions = await prisma.campaignRevision.findMany({ where: { campaignId } });
    assert.equal(previousRevisions.length, 1);
    assert.notEqual(previousRevisions[0].id, revision.id);
    assert.equal(previousRevisions[0].inputsHash, revision.inputsHash);
  });

  it("retains the previous campaign, its revisions, attempts, and history", async () => {
    const inputs = { model: "gpt-5.6-luna", metadataRevision: 4 };
    const { campaignId } = await failedAt("render", { inputs });
    const before = await getCampaign(campaignId);

    const { campaign } = await forceNewCampaign({ campaignId, reason: "operator decision" });
    const afterForce = await getCampaign(campaignId);

    assert.ok(afterForce, "the previous campaign is still readable");
    assert.equal(afterForce.attemptOrdinal, before.attemptOrdinal);
    assert.deepEqual(afterForce.revisions, before.revisions);
    assert.deepEqual(afterForce.attempts, before.attempts);
    // History is append-only: the earlier events are byte-identical and the
    // supersession is recorded after them.
    assert.deepEqual(afterForce.events.slice(0, before.events.length), before.events);
    assert.equal(afterForce.events.at(-1).type, "campaign_superseded");
    assert.equal(afterForce.events.at(-1).payload.reason, "operator decision");

    // Only the lifecycle changed, plus a forward link to the replacement.
    assert.equal(afterForce.state, "superseded");
    assert.equal(afterForce.supersededById, campaign.id);
    assert.equal(afterForce.activeSourceKey, null);
    assert.equal(afterForce.stage, before.stage);
  });

  it("moves the live slot to the new campaign", async () => {
    const { source, campaignId } = await failedAt("render");

    const { campaign } = await forceNewCampaign({ campaignId });
    const live = await prisma.campaign.findMany({
      where: { sourceVideoId: campaign.sourceVideoId, activeSourceKey: { not: null } },
    });

    assert.equal(live.length, 1);
    assert.equal(live[0].id, campaign.id);
    // Start now returns the replacement, not a third campaign.
    const restarted = await startCampaign(source);
    assert.equal(restarted.created, false);
    assert.equal(restarted.campaign.id, campaign.id);
  });

  it("can be driven by source hash when no campaign id is at hand", async () => {
    const { source, campaignId } = await failedAt("render");

    const { campaign, created } = await forceNewCampaign({ sourceHash: source.sourceHash });

    assert.equal(created, true);
    assert.notEqual(campaign.id, campaignId);
  });

  it("reports an unknown source as not found", async () => {
    await assert.rejects(() => forceNewCampaign({ sourceHash: "nope" }), (error) => {
      assert.equal(error.code, "source_not_found");
      return true;
    });
  });
});

describe("force-new under concurrency", () => {
  it("creates one campaign when two force-new requests race", async () => {
    const { campaignId } = await failedAt("render");
    const previous = await prisma.campaign.findUnique({ where: { id: campaignId } });

    const results = await Promise.all([
      forceNewCampaign({ campaignId }),
      forceNewCampaign({ campaignId }),
    ]);

    const createdIds = new Set(results.map((result) => result.campaign.id));
    assert.equal(createdIds.size, 1, "both callers must land on the same replacement");
    assert.equal(results.filter((result) => result.created).length, 1);
    assert.equal(
      await prisma.campaign.count({ where: { sourceVideoId: previous.sourceVideoId } }),
      2,
    );
  });

  it("creates one campaign when Force New is clicked twice in a row", async () => {
    const { campaignId } = await failedAt("render");
    const previous = await prisma.campaign.findUnique({ where: { id: campaignId } });

    const first = await forceNewCampaign({ campaignId });
    // The second click still names the original campaign, which is now retired.
    const second = await forceNewCampaign({ campaignId });

    assert.equal(second.campaign.id, first.campaign.id);
    assert.equal(second.created, false);
    assert.equal(second.reason, "already_replaced");
    assert.equal(
      await prisma.campaign.count({ where: { sourceVideoId: previous.sourceVideoId } }),
      2,
    );
  });

  it("returns the same campaign when a successful POST is retried", async () => {
    const { campaignId } = await failedAt("render");
    const creationKey = "force-new-" + crypto.randomUUID();

    const first = await forceNewCampaign({ campaignId, creationKey });
    const replay = await forceNewCampaign({ campaignId, creationKey });

    assert.equal(replay.campaign.id, first.campaign.id);
    assert.equal(replay.created, false);
  });

  it("creates one campaign when two start requests race", async () => {
    const source = freshSource();

    const results = await Promise.all([startCampaign(source), startCampaign(source)]);

    const ids = new Set(results.map((result) => result.campaign.id));
    assert.equal(ids.size, 1);
    assert.equal(await prisma.campaign.count({ where: { sourceVideoId: results[0].source.id } }), 1);
  });

  it("refuses a second live campaign per source at the database level", async () => {
    const source = freshSource();
    const { campaign } = await startCampaign(source);

    await assert.rejects(
      () =>
        prisma.campaign.create({
          data: {
            sourceVideoId: campaign.sourceVideoId,
            attemptOrdinal: 99,
            // Claiming the live slot a second time is what must be rejected.
            activeSourceKey: campaign.sourceVideoId,
            creationKey: "hand-written-" + crypto.randomUUID(),
          },
        }),
      (error) => error.code === "P2002",
    );
  });
});

describe("stage outcomes", () => {
  it("moves the campaign to the next stage on success", async () => {
    const source = freshSource();
    const { campaign } = await startCampaign(source);
    const { attempt } = await resumeCampaign({ campaignId: campaign.id });

    const result = await succeedAttempt({ attemptId: attempt.id });

    assert.equal(result.applied, true);
    assert.equal(result.attempt.state, "succeeded");
    assert.equal(result.campaign.stage, nextStage("ingest"));
    assert.equal(result.campaign.state, "pending");
  });

  it("ignores a replayed completion callback", async () => {
    const source = freshSource();
    const { campaign } = await startCampaign(source);
    const { attempt } = await resumeCampaign({ campaignId: campaign.id });

    const first = await succeedAttempt({ attemptId: attempt.id });
    const replay = await succeedAttempt({ attemptId: attempt.id });

    assert.equal(replay.applied, false);
    assert.equal(replay.campaign.stage, first.campaign.stage, "the stage moved once, not twice");
    assert.equal(
      await prisma.campaignEvent.count({
        where: { campaignId: campaign.id, type: "stage_succeeded" },
      }),
      1,
    );
  });

  it("ignores a replayed failure callback", async () => {
    const { campaignId, firstAttempt } = await failedAt("render");

    const replay = await failAttempt({ attemptId: firstAttempt.id, failureCode: "other_code" });

    assert.equal(replay.applied, false);
    assert.equal(replay.attempt.failureCode, "render_probe_failed");
    assert.equal(
      await prisma.campaignEvent.count({ where: { campaignId, type: "stage_failed" } }),
      1,
    );
  });

  it("releases the source once the campaign publishes", async () => {
    const source = freshSource();
    const { campaign } = await startCampaign(source);
    await prisma.campaign.update({ where: { id: campaign.id }, data: { stage: "publish" } });
    const { attempt } = await resumeCampaign({ campaignId: campaign.id });

    const result = await succeedAttempt({ attemptId: attempt.id });

    assert.equal(result.campaign.state, "published");
    assert.equal(result.campaign.stage, "complete");
    assert.equal(result.campaign.activeSourceKey, null);

    // A published source is intentionally reusable: a new start gets a new
    // campaign rather than being blocked by the finished one.
    const restarted = await startCampaign(source);
    assert.equal(restarted.created, true);
    assert.equal(restarted.campaign.attemptOrdinal, 2);
  });

  it("freezes the revision when a stage reports its manifest", async () => {
    const inputs = { model: "gpt-5.6-luna", metadataRevision: 7 };
    const source = freshSource();
    const { campaign } = await startCampaign(source);
    const { attempt, revision } = await resumeCampaign({ campaignId: campaign.id, inputs });
    assert.equal(revision.frozenAt, null);

    await succeedAttempt({
      attemptId: attempt.id,
      manifestPath: "/data/x/outputs/manifest/revision/1.json",
    });
    const frozen = await prisma.campaignRevision.findUnique({ where: { id: revision.id } });

    assert.equal(frozen.state, "ready");
    assert.equal(frozen.manifestPath, "/data/x/outputs/manifest/revision/1.json");
    assert.ok(frozen.frozenAt instanceof Date);
  });
});

describe("input fingerprinting", () => {
  it("does not depend on key order", () => {
    assert.equal(
      computeInputsHash({ a: 1, b: { c: 2, d: 3 } }),
      computeInputsHash({ b: { d: 3, c: 2 }, a: 1 }),
    );
  });

  it("changes when any input changes", () => {
    assert.notEqual(computeInputsHash({ model: "a" }), computeInputsHash({ model: "b" }));
  });

  it("keeps array order significant", () => {
    assert.notEqual(computeInputsHash({ accounts: [1, 2] }), computeInputsHash({ accounts: [2, 1] }));
  });
});
