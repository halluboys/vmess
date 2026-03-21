export default {
  async email(message, env, ctx) {
    try {
      const webhook = env.WEBHOOK_URL;

      const subject = message.headers.get("subject") || "";
      const from = message.from;
      const to = message.to;
      const text = await message.text();

      await fetch(webhook, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          from,
          to,
          subject,
          text,
        }),
      });

    } catch (err) {
      console.error("Email forward error:", err);
    }
  },
};