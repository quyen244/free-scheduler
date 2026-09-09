import { NextResponse } from "next/server";
import { getAppSession } from "@/lib/local-mode";
import { prisma } from "@/lib/prisma";
import config from "@/lib/config";

export async function GET(req) {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    const apiKey = config.ai.apiKey;

    const email = session.user.email;
    const accounts = [];

    // Treat the locally authorized Google account as the YouTube account.
    // This keeps local YouTube publishing independent from MuAPI credits.
    const googleAccount = await prisma.account.findFirst({
      where: { userId: session.user.id, provider: "google" },
      include: { user: true },
    });
    if (googleAccount) {
      accounts.push({
        id: 1,
        platform: 1,
        platform_name: "youtube",
        account_name: googleAccount.user.youtubeLabel || session.user.name || email,
        platform_user_id: googleAccount.providerAccountId,
        connected_at: googleAccount.id,
      });
    }

    // TikTok remains an optional MuAPI integration.
    if (apiKey) {
      try {
        const devRes = await fetch("https://api.muapi.ai/api/social/accounts", {
          headers: { "x-api-key": apiKey }
        });
        if (devRes.ok) {
          const devAccounts = await devRes.json();
          devAccounts.forEach(acc => {
            if (acc.platform === 2 || acc.platform_name === "tiktok") {
              accounts.push({
                id: acc.id,
                platform: 2,
                platform_name: "tiktok",
                account_name: acc.account_name || "TikTok Account",
                platform_user_id: acc.platform_user_id,
                connected_at: acc.connected_at
              });
            }
          });
        }
      } catch (err) {
        console.error("Failed to fetch developer accounts:", err);
      }
    }

    return NextResponse.json(accounts);
  } catch (error) {
    console.error("[GET_ACCOUNTS_ERROR]", error);
    return NextResponse.json({ error: error.message || "Internal Server Error" }, { status: 500 });
  }
}
