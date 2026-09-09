const config = {
  appName: "Free Ai Social Media Scheduler",
  auth: {
    google: {
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    },
    secret: process.env.NEXTAUTH_SECRET,
    url: process.env.NEXTAUTH_URL || "http://localhost:3000",
    // NEXT_PUBLIC_ so client components (integrations page) can read it.
    localMode: process.env.NEXT_PUBLIC_LOCAL_MODE === "true",
    localUserEmail: process.env.LOCAL_USER_EMAIL || "local@localhost",
    webhook_url: process.env.WEBHOOK_URL || process.env.NEXTAUTH_URL || "http://localhost:3000",
  },
  ai: {
    apiKey: process.env.MUAPIAPP_API_KEY,
    generationCost: 1, // Deducted per social post trigger
  }
};

export default config;
