import { NextResponse } from "next/server";
import { getAppSession, isLocalMode } from "@/lib/local-mode";
import { prisma } from "@/lib/prisma";
import config from "@/lib/config";

// Next 16: route handler params are a Promise — must await.
// Local mode: account id 1 = the locally stored Google account (YouTube).
// MuAPI TikTok accounts (id !== 1) proxy to MuAPI as before.

// DELETE: Disconnect account
export async function DELETE(req, { params }) {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    const { id } = await params;

    if (isLocalMode && id === "1") {
      // Local YouTube account: delete the stored Google account row.
      await prisma.account.deleteMany({
        where: { userId: session.user.id, provider: "google" },
      });
      await prisma.user.update({
        where: { id: session.user.id },
        data: { youtubeLabel: null },
      });
      return NextResponse.json({ success: true });
    }

    // TikTok (MuAPI) — optional integration, unchanged.
    const apiKey = config.ai.apiKey;
    if (!apiKey) {
      return NextResponse.json({ error: "MUAPIAPP_API_KEY is not configured" }, { status: 500 });
    }

    let res = await fetch(`https://api.muapi.ai/api/v1/social/ext/accounts/${id}`, {
      method: "DELETE",
      headers: { "x-api-key": apiKey }
    });

    if (!res.ok) {
      res = await fetch(`https://api.muapi.ai/api/social/accounts/${id}`, {
        method: "DELETE",
        headers: { "x-api-key": apiKey }
      });
    }

    if (!res.ok) {
      const errText = await res.text();
      return NextResponse.json({ error: `Disconnect failed: ${errText}` }, { status: 500 });
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("[DISCONNECT_ACCOUNT_ERROR]", error);
    return NextResponse.json({ error: error.message || "Internal Server Error" }, { status: 500 });
  }
}

// PATCH: Rename account
export async function PATCH(req, { params }) {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    const { id } = await params;
    const { accountName } = await req.json();

    if (isLocalMode && id === "1") {
      // Local YouTube account: persist the friendly label on the user row.
      await prisma.user.update({
        where: { id: session.user.id },
        data: { youtubeLabel: accountName || null },
      });
      return NextResponse.json({ success: true, account_name: accountName });
    }

    // TikTok (MuAPI) — optional integration, unchanged.
    const apiKey = config.ai.apiKey;
    if (!apiKey) {
      return NextResponse.json({ error: "MUAPIAPP_API_KEY is not configured" }, { status: 500 });
    }

    const res = await fetch(`https://api.muapi.ai/api/social/accounts/${id}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey
      },
      body: JSON.stringify({ account_name: accountName })
    });

    if (!res.ok) {
      const errText = await res.text();
      return NextResponse.json({ error: `Rename failed: ${errText}` }, { status: 500 });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("[RENAME_ACCOUNT_ERROR]", error);
    return NextResponse.json({ error: error.message || "Internal Server Error" }, { status: 500 });
  }
}
