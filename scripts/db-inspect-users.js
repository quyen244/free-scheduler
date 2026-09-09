// Inspect User + Account + Session rows.
// Usage: node --env-file=.env scripts/db-inspect-users.js
const { PrismaClient } = require("@prisma/client");
const { PrismaPg } = require("@prisma/adapter-pg");

const prisma = new PrismaClient({
  adapter: new PrismaPg({ connectionString: process.env.DATABASE_URL }),
});

(async () => {
  const users = await prisma.user.findMany({
    select: { id: true, email: true, name: true, youtubeLabel: true },
  });
  console.log("Users:", users.length);
  users.forEach((u) => console.log(`- ${u.id} | ${u.email} | ${u.name}`));

  const accounts = await prisma.account.findMany({
    select: { id: true, userId: true, provider: true },
  });
  console.log("\nAccounts:", accounts.length);
  accounts.forEach((a) => console.log(`- ${a.id} | user=${a.userId} | ${a.provider}`));

  const sessions = await prisma.session.count();
  console.log("\nSessions:", sessions);

  await prisma.$disconnect();
})().catch((e) => {
  console.error("ERROR:", e.message);
  process.exit(1);
});
