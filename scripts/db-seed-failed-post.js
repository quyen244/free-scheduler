// Seed one failed YouTube post with an auth-type error (for button tests).
// Prints the created row as JSON on stdout.
// Usage: node --env-file=.env scripts/db-seed-failed-post.js
const { PrismaClient } = require("@prisma/client");
const { PrismaPg } = require("@prisma/adapter-pg");

const prisma = new PrismaClient({
  adapter: new PrismaPg({ connectionString: process.env.DATABASE_URL }),
});

(async () => {
  // Same user the local-mode session resolves in this DB (google Account owner)
  const googleAccount = await prisma.account.findFirst({
    where: { provider: "google" },
  });
  const user =
    googleAccount
      ? await prisma.user.findUnique({ where: { id: googleAccount.userId } })
      : await prisma.user.upsert({
          where: { email: "local@localhost" },
          update: {},
          create: { email: "local@localhost", name: "Local User" },
        });

  const post = await prisma.scheduledPost.create({
    data: {
      userId: user.id,
      accountId: 1,
      platform: "youtube",
      accountName: "Test",
      mediaUrl: "/uploads/does-not-matter.mp4",
      title: "[Button Test] auth-failed post",
      privacy: "public",
      scheduledAt: new Date(Date.now() - 60_000),
      status: "failed",
      error: "YouTube authorization is missing a refresh token. Sign out and sign in again.",
    },
  });

  console.log(JSON.stringify({ id: post.id }));
  await prisma.$disconnect();
})().catch((e) => {
  console.error("ERROR:", e.message);
  process.exit(1);
});
