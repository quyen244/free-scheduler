import { readFile } from "node:fs/promises";
import path from "node:path";
import { prisma } from "@/lib/prisma";

async function refreshAccessToken(account) {
  if (!account.refresh_token) {
    throw new Error("YouTube authorization is missing a refresh token. Sign out and sign in again.");
  }

  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: process.env.GOOGLE_CLIENT_ID,
      client_secret: process.env.GOOGLE_CLIENT_SECRET,
      refresh_token: account.refresh_token,
      grant_type: "refresh_token",
    }),
  });

  const data = await response.json();
  if (!response.ok || !data.access_token) {
    throw new Error(data.error_description || "Unable to refresh YouTube authorization");
  }

  await prisma.account.update({
    where: { id: account.id },
    data: {
      access_token: data.access_token,
      expires_at: Math.floor(Date.now() / 1000) + (data.expires_in || 3600),
    },
  });

  return data.access_token;
}

async function getAccessToken(userId) {
  const account = await prisma.account.findFirst({
    where: { userId, provider: "google" },
  });

  if (!account) throw new Error("Google authorization was not found. Sign in again.");
  if (account.expires_at && account.expires_at * 1000 > Date.now() + 60_000 && account.access_token) {
    return { account, token: account.access_token };
  }

  return { account, token: await refreshAccessToken(account) };
}

async function uploadWithToken(token, metadata, media) {
  const sessionResponse = await fetch(
    "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": String(media.length),
        "X-Upload-Content-Type": metadata.contentType,
      },
      body: JSON.stringify({
        snippet: metadata.snippet,
        status: metadata.status,
      }),
    },
  );

  if (!sessionResponse.ok) {
    throw new Error(`YouTube upload session failed: ${await sessionResponse.text()}`);
  }

  const location = sessionResponse.headers.get("location");
  if (!location) throw new Error("YouTube did not return an upload URL");

  const uploadResponse = await fetch(location, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": metadata.contentType,
      "Content-Length": String(media.length),
    },
    body: media,
  });

  const result = await uploadResponse.json();
  if (!uploadResponse.ok) {
    throw new Error(`YouTube video upload failed: ${JSON.stringify(result)}`);
  }

  return result;
}

export async function publishToYouTube({ userId, mediaUrl, title, description, tags, privacy, categoryId, madeForKids }) {
  const mediaPath = mediaUrl.startsWith("/uploads/")
    ? path.join(process.cwd(), "public", mediaUrl.slice(1))
    : null;
  if (!mediaPath) throw new Error("YouTube uploads must use a locally stored video");

  const media = await readFile(mediaPath);
  const contentType = path.extname(mediaPath).toLowerCase() === ".mov" ? "video/quicktime" : "video/mp4";
  const metadata = {
    contentType,
    snippet: {
      title: title || "Untitled Video",
      description: description || "",
      tags: tags ? tags.split(",").map((tag) => tag.trim()).filter(Boolean) : [],
      categoryId: categoryId || "22",
    },
    status: {
      privacyStatus: privacy || "private",
      selfDeclaredMadeForKids: Boolean(madeForKids),
    },
  };

  const { account, token } = await getAccessToken(userId);
  try {
    return await uploadWithToken(token, metadata, media);
  } catch (error) {
    if (account.refresh_token && /401|unauthorized|invalidCredentials/i.test(error.message)) {
      return uploadWithToken(await refreshAccessToken(account), metadata, media);
    }
    throw error;
  }
}