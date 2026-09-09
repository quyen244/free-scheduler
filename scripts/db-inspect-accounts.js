// Inspect + purge google Account rows (Option 2).
// Usage:
//   node scripts/db-inspect-accounts.js   — show rows only
//   node scripts/db-inspect-accounts.js --purge — delete google rows
const { PrismaClient } = require("@prisma/client");
const { PrismaPg } = require("@prisma/adapter-pg");

const prisma = new PrismaClient({
  adapter: new PrismaPg({ connectionString: process.env.DATABASE_URL }),
});

(async () => {
  const rows = await prisma.account.findMany({
    where: { provider: "google" },
    select: {
      id: true,
      userId: true,
      type: true,
      expires_at: true,
      scope: true,
      refresh_token: true,
    },
  });

  console.log("google Account rows before:", rows.length);
  for (const r of rows) {
    const hasRefresh = r.refresh_token ? "YES" : "NO";
    const expired =
      r.expires_at && r.expires_at * 1000 < Date.now() ? "EXPIRED" : "valid";
    console.log(`- id=${r.id} userId=${r.userId} type=${r.type} token=${expired} refresh_token=${hasRefresh} scope=${r.scope}`);
  }

  if (process.argv.includes("--purge")) {
    const del = await prisma.account.deleteMany({ where: { provider: "google" } });
    console.log(`Purged ${del.count} google Account row(s).`);
  }

  await prisma.$disconnect();
})().catch((e) => {
  console.error("ERROR:", e.message);
  process.exit(1);
});
