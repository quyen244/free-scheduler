import { NextResponse } from "next/server";
import { getAppSession } from "@/lib/local-mode";
import { prisma } from "@/lib/prisma";
import config from "@/lib/config";
import { publishToYouTube } from "@/lib/youtube";

// Helper function to call the MuAPI publishing endpoints
async function triggerMuApiPublish(platform, payload) {
  const apiKey = config.ai.apiKey;
  const endpoint = platform === "youtube" 
    ? "https://api.muapi.ai/api/v1/youtube-publish" 
    : "https://api.muapi.ai/api/v1/tiktok-publish";

  // Re-map keys if needed for each platform
  const bodyData = {
    account_id: parseInt(payload.accountId),
    media_url: payload.mediaUrl
  };

  if (platform === "youtube") {
    bodyData.title = payload.title || "Untitled Video";
    bodyData.description = payload.description || "";
    bodyData.tags = payload.tags ? payload.tags.split(",").map(t => t.trim()) : [];
    bodyData.privacy = payload.privacy || "public";
    if (payload.categoryId) bodyData.category_id = payload.categoryId;
    bodyData.made_for_kids = payload.madeForKids || false;
  } else {
    bodyData.title = payload.title || ""; // TikTok caption
    bodyData.privacy_level = payload.privacy || "PUBLIC_TO_EVERYONE";
    bodyData.disable_comment = payload.disableComment || false;
    bodyData.disable_duet = payload.disableDuet || false;
    bodyData.disable_stitch = payload.disableStitch || false;
  }

  const res = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey
    },
    body: JSON.stringify(bodyData)
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`MuAPI submission failed: ${errText}`);
  }

  const data = await res.json();
  return data.request_id || data.id; // Returns request_id
}

async function publishPost(post, userId) {
  if (post.platform === "youtube") {
    const result = await publishToYouTube({
      userId,
      mediaUrl: post.mediaUrl,
      title: post.title,
      description: post.description,
      tags: post.tags,
      privacy: post.privacy,
      categoryId: post.categoryId,
      madeForKids: post.madeForKids,
    });
    return {
      requestId: result.id,
      publishedUrl: `https://www.youtube.com/watch?v=${result.id}`,
      publishResult: JSON.stringify(result),
    };
  }

  return { requestId: await triggerMuApiPublish(post.platform, post) };
}

// GET: List posts + Process Due Posts + Poll Status
export async function GET(req) {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    const userId = session.user.id;

    // 1. Fetch current posts from database
    let posts = await prisma.scheduledPost.findMany({
      where: { userId },
      orderBy: { scheduledAt: "desc" }
    });

    const now = new Date();
    let dbUpdated = false;

    // 2. Trigger Due Scheduled Posts
    const duePosts = posts.filter(p => p.status === "scheduled" && new Date(p.scheduledAt) <= now);
    for (const post of duePosts) {
      try {
        const result = await publishPost(post, userId);

        await prisma.scheduledPost.update({
          where: { id: post.id },
          data: {
            status: post.platform === "youtube" ? "completed" : "processing",
            requestId: result.requestId,
            publishedUrl: result.publishedUrl || null,
            publishResult: result.publishResult || null,
            publishedAt: result.publishedUrl ? new Date() : null,
          }
        });
        dbUpdated = true;
      } catch (err) {
        console.error(`Failed to trigger due post ${post.id}:`, err);
        
        // Set post to failed
        await prisma.scheduledPost.update({
          where: { id: post.id },
          data: {
            status: "failed",
            error: err.message || "Failed to trigger scheduled post"
          }
        });
        dbUpdated = true;
      }
    }

    // 3. Poll Processing Posts
    const processingPosts = posts.filter(p => p.status === "processing" && p.requestId);
    for (const post of processingPosts) {
      try {
        const apiKey = config.ai.apiKey;
        const res = await fetch(`https://api.muapi.ai/api/v1/predictions/${post.requestId}/result`, {
          headers: { "x-api-key": apiKey }
        });

        if (res.ok) {
          const result = await res.json();
          const status = result.status || result.state;

          if (status === "completed" || status === "succeeded") {
            const output = result.output || {};
            // For YouTube, it returns url. For TikTok, it returns publish_id
            const publishedUrl = output.url || (output.publish_id ? `https://tiktok.com/publish/${output.publish_id}` : null);
            
            await prisma.scheduledPost.update({
              where: { id: post.id },
              data: {
                status: "completed",
                publishedUrl: publishedUrl || "Published successfully",
                publishResult: JSON.stringify(output),
                publishedAt: new Date()
              }
            });
            dbUpdated = true;
          } else if (status === "failed") {
            const errorMsg = result.error || "Publishing failed";
            await prisma.scheduledPost.update({
              where: { id: post.id },
              data: {
                status: "failed",
                error: errorMsg
              }
            });

            dbUpdated = true;
          }
        }
      } catch (err) {
        console.error(`Failed to poll status for post ${post.id}:`, err);
      }
    }

    // If database was modified, re-fetch posts list to return fresh data
    if (dbUpdated) {
      posts = await prisma.scheduledPost.findMany({
        where: { userId },
        orderBy: { scheduledAt: "desc" }
      });
    }

    return NextResponse.json(posts);
  } catch (error) {
    console.error("[GET_POSTS_ERROR]", error);
    return NextResponse.json({ error: error.message || "Internal Server Error" }, { status: 500 });
  }
}

// POST: Schedule or Publish Immediately
export async function POST(req) {
  try {
    const session = await getAppSession();
    if (!session?.user) {
      return new NextResponse("Unauthorized", { status: 401 });
    }

    const userId = session.user.id;
    const body = await req.json();

    const {
      accountId,
      platform,
      accountName,
      mediaUrl,
      title,
      description,
      tags,
      privacy,
      scheduledAt,
      disableComment,
      disableDuet,
      disableStitch,
      categoryId,
      madeForKids
    } = body;

    if (!accountId || !platform || !mediaUrl) {
      return NextResponse.json({ error: "Missing required fields: accountId, platform, and mediaUrl are mandatory." }, { status: 400 });
    }

    const isScheduled = scheduledAt && new Date(scheduledAt) > new Date();

    if (isScheduled) {
      const post = await prisma.scheduledPost.create({
        data: {
          userId,
          accountId: parseInt(accountId),
          platform,
          accountName: accountName || `${platform} Account`,
          mediaUrl,
          title: title || "",
          description: description || "",
          tags: tags || "",
          privacy: privacy || "public",
          disableComment: !!disableComment,
          disableDuet: !!disableDuet,
          disableStitch: !!disableStitch,
          categoryId: categoryId || null,
          madeForKids: !!madeForKids,
          scheduledAt: new Date(scheduledAt),
          status: "scheduled"
        }
      });
      return NextResponse.json(post);
    } else {
      // Immediate publish
      try {
        const result = await publishPost({
          platform,
          accountId,
          mediaUrl,
          title,
          description,
          tags,
          privacy,
          disableComment,
          disableDuet,
          disableStitch,
          categoryId,
          madeForKids,
        }, userId);

        // Create database entry with status processing
        const post = await prisma.scheduledPost.create({
          data: {
            userId,
            accountId: parseInt(accountId),
            platform,
            accountName: accountName || `${platform} Account`,
            mediaUrl,
            title: title || "",
            description: description || "",
            tags: tags || "",
            privacy: privacy || "public",
            disableComment: !!disableComment,
            disableDuet: !!disableDuet,
            disableStitch: !!disableStitch,
            categoryId: categoryId || null,
            madeForKids: !!madeForKids,
            scheduledAt: new Date(),
            status: platform === "youtube" ? "completed" : "processing",
            requestId: result.requestId,
            publishedUrl: result.publishedUrl || null,
            publishResult: result.publishResult || null,
            publishedAt: result.publishedUrl ? new Date() : null,
          }
        });
        return NextResponse.json(post);
      } catch (err) {
        throw err;
      }
    }
  } catch (error) {
    console.error("[POST_POSTS_ERROR]", error);
    return NextResponse.json({ error: error.message || "Internal Server Error" }, { status: 500 });
  }
}
