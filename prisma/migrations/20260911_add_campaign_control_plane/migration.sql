-- CreateEnum
CREATE TYPE "CampaignState" AS ENUM ('pending', 'running', 'failed', 'awaiting_approval', 'published', 'superseded');

-- CreateEnum
CREATE TYPE "PipelineStage" AS ENUM ('ingest', 'transcribe', 'translate', 'chunk', 'metadata', 'render', 'approval', 'publish', 'complete');

-- CreateEnum
CREATE TYPE "AttemptState" AS ENUM ('running', 'succeeded', 'failed');

-- CreateEnum
CREATE TYPE "RevisionState" AS ENUM ('draft', 'ready', 'superseded');

-- CreateTable
CREATE TABLE "SourceVideo" (
    "id" TEXT NOT NULL,
    "sourceUrl" TEXT NOT NULL,
    "sourceHash" TEXT NOT NULL,
    "platformVideoId" TEXT,
    "durationS" DOUBLE PRECISION,
    "width" INTEGER,
    "height" INTEGER,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "SourceVideo_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Campaign" (
    "id" TEXT NOT NULL,
    "sourceVideoId" TEXT NOT NULL,
    "attemptOrdinal" INTEGER NOT NULL,
    "state" "CampaignState" NOT NULL DEFAULT 'pending',
    "stage" "PipelineStage" NOT NULL DEFAULT 'ingest',
    "failureCode" TEXT,
    "failureMessage" TEXT,
    "activeSourceKey" TEXT,
    "creationKey" TEXT NOT NULL,
    "supersededById" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Campaign_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CampaignRevision" (
    "id" TEXT NOT NULL,
    "campaignId" TEXT NOT NULL,
    "revisionNumber" INTEGER NOT NULL,
    "state" "RevisionState" NOT NULL DEFAULT 'draft',
    "inputsHash" TEXT NOT NULL,
    "manifestPath" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "frozenAt" TIMESTAMP(3),

    CONSTRAINT "CampaignRevision_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ProcessingAttempt" (
    "id" TEXT NOT NULL,
    "campaignId" TEXT NOT NULL,
    "revisionId" TEXT,
    "attemptNumber" INTEGER NOT NULL,
    "stage" "PipelineStage" NOT NULL,
    "state" "AttemptState" NOT NULL DEFAULT 'running',
    "idempotencyKey" TEXT NOT NULL,
    "failureCode" TEXT,
    "failureMessage" TEXT,
    "retryable" BOOLEAN,
    "startedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finishedAt" TIMESTAMP(3),
    "leaseExpiresAt" TIMESTAMP(3),

    CONSTRAINT "ProcessingAttempt_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CampaignEvent" (
    "id" TEXT NOT NULL,
    "campaignId" TEXT NOT NULL,
    "sequence" INTEGER NOT NULL,
    "type" TEXT NOT NULL,
    "fromState" "CampaignState",
    "toState" "CampaignState",
    "payload" JSONB,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CampaignEvent_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "SourceVideo_sourceHash_key" ON "SourceVideo"("sourceHash");

-- CreateIndex
CREATE INDEX "SourceVideo_platformVideoId_idx" ON "SourceVideo"("platformVideoId");

-- CreateIndex
CREATE UNIQUE INDEX "Campaign_activeSourceKey_key" ON "Campaign"("activeSourceKey");

-- CreateIndex
CREATE UNIQUE INDEX "Campaign_creationKey_key" ON "Campaign"("creationKey");

-- CreateIndex
CREATE UNIQUE INDEX "Campaign_supersededById_key" ON "Campaign"("supersededById");

-- CreateIndex
CREATE INDEX "Campaign_state_idx" ON "Campaign"("state");

-- CreateIndex
CREATE UNIQUE INDEX "Campaign_sourceVideoId_attemptOrdinal_key" ON "Campaign"("sourceVideoId", "attemptOrdinal");

-- CreateIndex
CREATE UNIQUE INDEX "CampaignRevision_campaignId_revisionNumber_key" ON "CampaignRevision"("campaignId", "revisionNumber");

-- CreateIndex
CREATE UNIQUE INDEX "CampaignRevision_campaignId_inputsHash_key" ON "CampaignRevision"("campaignId", "inputsHash");

-- CreateIndex
CREATE UNIQUE INDEX "ProcessingAttempt_idempotencyKey_key" ON "ProcessingAttempt"("idempotencyKey");

-- CreateIndex
CREATE INDEX "ProcessingAttempt_state_leaseExpiresAt_idx" ON "ProcessingAttempt"("state", "leaseExpiresAt");

-- CreateIndex
CREATE UNIQUE INDEX "ProcessingAttempt_campaignId_attemptNumber_key" ON "ProcessingAttempt"("campaignId", "attemptNumber");

-- CreateIndex
CREATE INDEX "CampaignEvent_campaignId_createdAt_idx" ON "CampaignEvent"("campaignId", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "CampaignEvent_campaignId_sequence_key" ON "CampaignEvent"("campaignId", "sequence");

-- AddForeignKey
ALTER TABLE "Campaign" ADD CONSTRAINT "Campaign_supersededById_fkey" FOREIGN KEY ("supersededById") REFERENCES "Campaign"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Campaign" ADD CONSTRAINT "Campaign_sourceVideoId_fkey" FOREIGN KEY ("sourceVideoId") REFERENCES "SourceVideo"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CampaignRevision" ADD CONSTRAINT "CampaignRevision_campaignId_fkey" FOREIGN KEY ("campaignId") REFERENCES "Campaign"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ProcessingAttempt" ADD CONSTRAINT "ProcessingAttempt_campaignId_fkey" FOREIGN KEY ("campaignId") REFERENCES "Campaign"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ProcessingAttempt" ADD CONSTRAINT "ProcessingAttempt_revisionId_fkey" FOREIGN KEY ("revisionId") REFERENCES "CampaignRevision"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CampaignEvent" ADD CONSTRAINT "CampaignEvent_campaignId_fkey" FOREIGN KEY ("campaignId") REFERENCES "Campaign"("id") ON DELETE CASCADE ON UPDATE CASCADE;

