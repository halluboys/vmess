export default {
  async email(message, env, ctx) {
    try {
      console.log("EMAIL RECEIVED");
      console.log("from:", message.from);
      console.log("to:", message.to);

      const webhook = env.WEBHOOK_URL;
      const subject = message.headers.get("subject") || "";
      const text = await message.text();

      const res = await fetch(webhook, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          from: message.from,
          to: message.to,
          subject,
          text,
        }),
      });

      console.log("WEBHOOK STATUS:", res.status);
      console.log("WEBHOOK RESPONSE:", await res.text());
    } catch (err) {
      console.error("WORKER ERROR:", err);
    }
  },
};
