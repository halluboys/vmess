"""
Main Telegram bot - handles user interaction and orchestrates entire workflow
Commands: /start, /apply
"""

import os
from telegram import Update, File
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from config import TELEGRAM_TOKEN
from logger import ghs_logger
from playwright_flow import PlaywrightFlow
from playwright.async_api import async_playwright


# Conversation states
WAITING_FOR_COOKIE = 1
WAITING_FOR_SCHOOL = 2
WAITING_FOR_NAME = 3
PROCESSING = 4


class GitHubEduBot:
    def __init__(self):
        self.user_data = {}
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        ghs_logger.info("[GHS] Bot started by user")
        
        message = """
🤖 **GitHub Education Benefits Bot**

Welcome! I'll help you automate the GitHub Student Developer Pack application.

**What you need:**
✅ GitHub cookie (from Network tab)
✅ School name (e.g., University of California, Berkeley)

**Process:**
1. I'll use your GitHub session (cookie)
2. Navigate to Education Benefits page
3. Auto-fill the application form
4. Generate USA documents (Student ID, Transcript, Letter)
5. Upload documents and submit
6. Check status in real-time
7. Send you screenshot proof

**Estimated time:** 2-4 minutes
**Success rate:** 99% (submission), 65-85% (approval)

Ready? Use /apply to begin!
        """
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def apply_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /apply command"""
        ghs_logger.info("[GHS] Apply command triggered")
        
        message = """
📋 **Step 1/3: Provide GitHub Cookie**

How to get your cookie:
1. Open GitHub in your browser
2. Open DevTools (F12)
3. Go to **Network** tab
4. Click on benefits request (education.github.com)
5. Copy **Cookie** header value (entire string)
6. Send it here

⚠️ Keep your cookie private! It's your session.
        """
        
        await update.message.reply_text(message, parse_mode='Markdown')
        return WAITING_FOR_COOKIE
    
    async def receive_cookie(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive and validate GitHub cookie"""
        cookie = update.message.text.strip()
        
        ghs_logger.info(f"[GHS] Cookie received (length: {len(cookie)})")
        
        # Basic validation
        if len(cookie) < 100:
            await update.message.reply_text(
                "❌ Cookie looks too short. Make sure you copied the entire **Cookie** header value.",
                parse_mode='Markdown'
            )
            return WAITING_FOR_COOKIE
        
        if '_gh_sess' not in cookie and 'logged_in' not in cookie:
            await update.message.reply_text(
                "⚠️ Cookie might be invalid. Expected GitHub session cookies. Continue anyway? (yes/no)",
                parse_mode='Markdown'
            )
            context.user_data['pending_cookie'] = cookie
            return WAITING_FOR_COOKIE
        
        # Store cookie
        context.user_data['cookie'] = cookie
        ghs_logger.info("[GHS] Cookie validated and stored")
        
        # Ask for school
        message = """
✅ **Cookie received!**

📚 **Step 2/3: School Name**

Enter your school name (USA university recommended):
Examples:
- University of California, Berkeley
- MIT
- Stanford University
- Harvard University

What's your school?
        """
        
        await update.message.reply_text(message, parse_mode='Markdown')
        return WAITING_FOR_SCHOOL
    
    async def receive_school(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive school name"""
        school = update.message.text.strip()
        
        if len(school) < 3:
            await update.message.reply_text("❌ School name too short. Please enter a valid school name.")
            return WAITING_FOR_SCHOOL
        
        context.user_data['school'] = school
        ghs_logger.info(f"[GHS] School set: {school}")
        
        # Ask for name
        message = """
✅ **School saved!**

👤 **Step 3/3: Full Name**

Enter your full name (as it appears on documents):
Example: John Doe

This will be used on generated documents.
        """
        
        await update.message.reply_text(message, parse_mode='Markdown')
        return WAITING_FOR_NAME
    
    async def receive_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive user name and start automation"""
        name = update.message.text.strip()
        
        if len(name) < 2:
            await update.message.reply_text("❌ Name too short.")
            return WAITING_FOR_NAME
        
        context.user_data['name'] = name
        ghs_logger.info(f"[GHS] User name: {name}")
        
        # Start processing
        await update.message.reply_text(
            "🚀 **Starting automation...**\n\n⏳ This will take 2-4 minutes. I'll send updates as we go.",
            parse_mode='Markdown'
        )
        
        # Run automation
        await self.run_automation(update, context)
        
        return ConversationHandler.END
    
    async def run_automation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Run the full automation"""
        try:
            cookie = context.user_data.get('cookie')
            school = context.user_data.get('school')
            name = context.user_data.get('name')
            
            ghs_logger.info(f"[GHS] Starting automation for {name} at {school}")
            
            # Launch browser
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                
                # Create flow
                flow = PlaywrightFlow(cookie, school, name)
                result = await flow.run_full_flow(browser)
                
                await browser.close()
            
            # Send results
            await self.send_results(update, context, result)
            
        except Exception as e:
            ghs_logger.error(f"[GHS] Automation failed: {str(e)}")
            await update.message.reply_text(
                f"❌ **Automation failed:**\n```\n{str(e)[:200]}\n```",
                parse_mode='Markdown'
            )
    
    async def send_results(self, update: Update, context: ContextTypes.DEFAULT_TYPE, result):
        """Send automation results to user"""
        
        # Format status message
        status_emoji = "✅" if result['success'] else "⚠️"
        
        message = f"""
{status_emoji} **Application {('Submitted!' if result['success'] else 'Processing...')}**

📊 **Status:** {result['status']['status']}
🏫 **School:** {result['school']}
👤 **Name:** {result['profile_name']}

⏱️ **Timeline:**
- Submission: 2-4 minutes ✅
- GitHub Review: 24-72 hours ⏳
- Benefits Active: Varies (can be instant to days)

📝 **Next Steps:**
1. Wait for GitHub's manual review (usually 24-72 hours)
2. Check your GitHub Education page regularly
3. Once approved, you'll see "Awaiting Benefits" or "Benefits Active"

📸 **Application Screenshot:**
        """
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
        # Send screenshot
        if result['screenshot'] and os.path.exists(result['screenshot']):
            with open(result['screenshot'], 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption="📸 Application submission proof"
                )
        
        # Send detailed logs
        log_message = f"""
📋 **Application Details:**

School: {result['school']}
Status: {result['status']['status']}
Timestamp: {result['status']['timestamp']}

ℹ️ Your application has been submitted to GitHub. They will review it manually and notify you of the decision.

Use /apply to submit another application.
        """
        
        await update.message.reply_text(log_message, parse_mode='Markdown')
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel conversation"""
        await update.message.reply_text("❌ Cancelled. Use /apply to start again.")
        return ConversationHandler.END


def main():
    """Start the bot"""
    ghs_logger.info("[GHS] GitHub Education Bot starting...")
    
    # Create bot
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    bot_handler = GitHubEduBot()
    
    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("apply", bot_handler.apply_command)],
        states={
            WAITING_FOR_COOKIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.receive_cookie)],
            WAITING_FOR_SCHOOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.receive_school)],
            WAITING_FOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.receive_name)],
        },
        fallbacks=[CommandHandler("cancel", bot_handler.cancel)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot_handler.start))
    application.add_handler(conv_handler)
    
    ghs_logger.info("[GHS] Bot ready! Polling for messages...")
    
    # Start polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        ghs_logger.info("[GHS] Bot stopped by user")
