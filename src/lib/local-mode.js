import { getServerSession } from "next-auth/next";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

export const isLocalMode = process.env.LOCAL_MODE === "true";

export async function getLocalUser() {
  const email = process.env.LOCAL_USER_EMAIL || "local@localhost";
  return prisma.user.upsert({
    where: { email },
    update: {},
    create: { email, name: "Local User" },
  });
}

export async function getAppSession() {
  if (isLocalMode) {
    const user = await getLocalUser();
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

  return getServerSession(authOptions);
}