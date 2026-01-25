# FTC Picks Premium Discord Bot

Premium sports betting picks bot with real-time odds from 10+ sportsbooks.

## 🚀 Deploy to Railway

1. **Push to GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin YOUR_GITHUB_REPO_URL
   git push -u origin main
   ```

2. **Connect to Railway**
   - Go to [Railway.app](https://railway.app)
   - Click "New Project" → "Deploy from GitHub"
   - Select your repository
   
3. **Add Environment Variables**
   Go to your Railway project → Variables → Add these:
   
   ```
   BOT_TOKEN=your_discord_bot_token
   ODDS_API_KEY=your_odds_api_key
   BOT_OWNER_ID=your_discord_user_id
   PREMIUM_ROLE_ID=your_premium_role_id
   VERIFICATION_CHANNEL_ID=your_verification_channel_id
   AUTO_POST_CHANNEL_ID=your_auto_post_channel_id
   ```

4. **Deploy**
   - Railway will automatically deploy
   - Check logs for "Logged in as..."
   - Your bot is live! 🎉

## 📋 Local Development

1. Copy `.env.example` to `.env`
2. Fill in your values
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python prizepicks_updated.py`

## 🔒 Security

- **NEVER** commit `.env` to GitHub
- `.gitignore` is configured to prevent this
- Tokens are loaded from environment variables only

## 📝 Features

- Premium picks from 10+ sportsbooks
- NBA, NFL, MLB, NHL, Soccer, MMA, Esports
- Parlay builder, stats tracking, bankroll management
- Injury reports, player trends, betting calculator
- Trial system with cooldowns

## 💰 Pricing

- Monthly: $25/month
- Lifetime: $100 one-time
- 3-day free trial

## 🛠️ Commands

Type `!commands` in Discord for full list.

---
Built with discord.py • Powered by The Odds API
