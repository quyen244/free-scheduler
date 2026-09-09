import { getServerSession } from "next-auth/next";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

// Server-side flag. Client uses NEXT_PUBLIC_LOCAL_MODE via src/lib/config.js.
export const isLocalMode = process.env.LOCAL_MODE === "true";

// Local mode session resolution, in order of preference:
// 1. Real NextAuth Google session (fresh OAuth sign-in — has live client session)
// 2. User owning a stored Google Account row (tokens persisted from a previous sign-in)
// 3. Upserted local@localhost placeholder (last resort — no YouTube publishing possible)
export async function getAppSession() {
  const session = await getServerSession(authOptions);
  if (session?.user) return session;

  if (isLocalMode) {
    const googleAccount = await prisma.account.findFirst({
      where: { provider: "google" },
      include: { user: true },
    });

    const user =
      googleAccount?.user ??
      (await prisma.user.upsert({
        where: { email: "local@localhost" },
        update: {},
        create: { email: "local@localhost", name: "Local User" },
      }));

    return {
      user: {
        id: user.id,
        email: user.email,
        name: user.name,
        image: user.image,
        credits: user.credits,
      },
    };
  }

  return null;
}
