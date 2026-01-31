import discord
from discord.ext import commands
import aiohttp
import asyncio
from datetime import datetime, timedelta
import json
from collections import defaultdict
import sqlite3
import random
import time
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ===== CONFIG =====
BOT_TOKEN = os.getenv('BOT_TOKEN')
ODDS_API_KEY = os.getenv('ODDS_API_KEY', 'd59bf68cfe63c626018ee47f0f53ead0')
BALLDONTLIE_API_KEY = os.getenv('BALLDONTLIE_API_KEY', 'ac7fc030-170a-4712-a8f3-60a351ee2675')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)  # Disable default help

# ===== GLOBALS =====
WEBSITE_URL = 'https://ftcpicks.netlify.app/'
OWNER_ID = None
PREMIUM_ROLE_ID = None
picks_cache = {}  # Cache picks by sport
cache_duration = 300  # 5 minutes

# ===== DATABASE =====
def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS premium_users
                 (user_id INTEGER PRIMARY KEY, expires_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS cooldowns
                 (user_id INTEGER, command TEXT, last_used REAL,
                  PRIMARY KEY (user_id, command))''')
    conn.commit()
    conn.close()

def is_premium(user_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('SELECT expires_at FROM premium_users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result:
        expires = datetime.fromisoformat(result[0])
        return datetime.now() < expires
    return False

# ===== NBA STATS API (REAL DATA) =====
async def get_nba_player_stats(player_name, stat_type, line, direction='over'):
    """Get REAL player stats from balldontlie.io"""
    try:
        headers = {"Authorization": BALLDONTLIE_API_KEY}
        
        async with aiohttp.ClientSession() as session:
            # Search player
            async with session.get(
                "https://api.balldontlie.io/v1/players",
                params={"search": player_name},
                headers=headers,
                timeout=10
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data.get('data'):
                    return None
                player_id = data['data'][0]['id']
            
            # Get last 10 games
            async with session.get(
                "https://api.balldontlie.io/v1/stats",
                params={
                    "player_ids[]": player_id,
                    "per_page": 10,
                    "seasons[]": 2024
                },
                headers=headers,
                timeout=10
            ) as resp:
                if resp.status != 200:
                    return None
                
                stats_data = await resp.json()
                games = stats_data.get('data', [])
                
                if len(games) < 5:
                    return None
                
                # Map stat types
                stat_map = {
                    'Points': 'pts',
                    'Rebounds': 'reb',
                    'Assists': 'ast',
                    '3-Pointers': 'fg3m',
                    'Steals': 'stl',
                    'Blocks': 'blk'
                }
                
                stat_key = stat_map.get(stat_type)
                if not stat_key:
                    return None
                
                # Calculate hit rate
                hits = 0
                values = []
                for game in games:
                    value = game.get(stat_key, 0) or 0
                    values.append(value)
                    
                    if direction.lower() in ['over', 'more']:
                        if value > line:
                            hits += 1
                    else:  # Under
                        if value < line:
                            hits += 1
                
                hit_rate = (hits / len(games)) * 100
                avg = sum(values) / len(values)
                
                return {
                    'hit_rate': round(hit_rate, 1),
                    'average': round(avg, 1),
                    'games': len(games),
                    'is_good': hit_rate >= 65  # 65% threshold
                }
    except Exception as e:
        print(f"Stats error for {player_name}: {e}")
        return None

# ===== ODDS API (REAL LINES) =====
async def fetch_nba_props():
    """Fetch NBA props from Odds API"""
    picks = []
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/events"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "player_points,player_rebounds,player_assists,player_threes",
        "oddsFormat": "american"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=15) as resp:
                if resp.status != 200:
                    print(f"Odds API error: {resp.status}")
                    return []
                
                events = await resp.json()
                print(f"Found {len(events)} NBA games")
                
                for event in events[:5]:
                    event_id = event['id']
                    props_url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{event_id}/odds"
                    
                    async with session.get(props_url, params=params, timeout=15) as props_resp:
                        if props_resp.status != 200:
                            continue
                        
                        props_data = await props_resp.json()
                        
                        if 'bookmakers' in props_data:
                            for bookmaker in props_data['bookmakers']:
                                for market in bookmaker.get('markets', []):
                                    prop_type_map = {
                                        'player_points': 'Points',
                                        'player_rebounds': 'Rebounds',
                                        'player_assists': 'Assists',
                                        'player_threes': '3-Pointers'
                                    }
                                    
                                    for outcome in market.get('outcomes', []):
                                        picks.append({
                                            'player': outcome.get('description', 'Unknown'),
                                            'prop_type': prop_type_map.get(market['key'], market['key']),
                                            'line': outcome.get('point', 0),
                                            'direction': outcome.get('name', 'Over'),
                                            'odds': outcome.get('price', -110),
                                            'bookmaker': bookmaker['title'],
                                            'game': f"{props_data.get('away_team')} @ {props_data.get('home_team')}"
                                        })
        
        print(f"Collected {len(picks)} raw NBA props")
        return picks
    except Exception as e:
        print(f"NBA fetch error: {e}")
        return []

async def aggregate_nba_picks():
    """Aggregate and filter NBA picks with REAL stats"""
    raw_picks = await fetch_nba_props()
    
    if not raw_picks:
        return []
    
    # Group by player + prop + direction
    grouped = defaultdict(list)
    for pick in raw_picks:
        key = f"{pick['player']}_{pick['prop_type']}_{pick['direction']}"
        grouped[key].append(pick)
    
    # Filter with real stats
    good_picks = []
    
    for key, picks in grouped.items():
        if len(picks) < 2:  # Need 2+ books
            continue
        
        pick = picks[0]
        player = pick['player']
        prop_type = pick['prop_type']
        line = pick['line']
        direction = pick['direction']
        
        # Get REAL stats
        stats = await get_nba_player_stats(player, prop_type, line, direction)
        
        if stats and stats['is_good']:
            # This is a GOOD pick!
            avg_odds = sum(p['odds'] for p in picks) / len(picks)
            
            good_picks.append({
                'player': player,
                'prop_type': prop_type,
                'line': line,
                'direction': direction,
                'odds': round(avg_odds),
                'books': len(picks),
                'bookmakers': [p['bookmaker'] for p in picks],
                'game': pick['game'],
                'hit_rate': stats['hit_rate'],
                'average': stats['average'],
                'games_analyzed': stats['games']
            })
            
            print(f"✅ GOOD: {player} {direction} {line} {prop_type} ({stats['hit_rate']}%)")
        elif stats:
            print(f"❌ FILTERED: {player} {direction} {line} {prop_type} ({stats['hit_rate']}%)")
    
    # Sort by hit rate
    good_picks.sort(key=lambda x: x['hit_rate'], reverse=True)
    return good_picks

# ===== COMMANDS =====

@bot.event
async def on_ready():
    global OWNER_ID, PREMIUM_ROLE_ID
    print(f'✅ {bot.user} is online!')
    
    # Find owner and premium role
    for guild in bot.guilds:
        if guild.owner:
            OWNER_ID = guild.owner.id
            print(f'Owner ID: {OWNER_ID}')
        
        for role in guild.roles:
            if 'premium' in role.name.lower():
                PREMIUM_ROLE_ID = role.id
                print(f'Premium Role ID: {PREMIUM_ROLE_ID}')
                break
    
    init_db()
    
    # Cache picks on startup
    print("Loading picks...")
    picks_cache['nba'] = await aggregate_nba_picks()
    print(f"Cached {len(picks_cache.get('nba', []))} NBA picks")

@bot.command()
async def predict(ctx, sport: str = 'nba'):
    """Show filtered picks with REAL hit rates"""
    sport = sport.lower()
    
    if sport != 'nba':
        await ctx.send("❌ Only NBA supported currently. More sports coming soon!")
        return
    
    # Check cache
    if 'nba' not in picks_cache or not picks_cache['nba']:
        msg = await ctx.send("⏳ Fetching fresh NBA picks...")
        picks_cache['nba'] = await aggregate_nba_picks()
        await msg.delete()
    
    picks = picks_cache.get('nba', [])
    
    if not picks:
        await ctx.send("❌ No good picks available right now.")
        return
    
    # Create embed
    embed = discord.Embed(
        title="🏀 NBA PICKS - REAL DATA",
        description=f"✅ {len(picks)} picks with 65%+ hit rate\n📊 Verified with player stats",
        color=0x9333ea,
        timestamp=datetime.now()
    )
    
    for i, pick in enumerate(picks[:10], 1):
        odds_str = f"+{pick['odds']}" if pick['odds'] > 0 else str(pick['odds'])
        
        value = f"""
**{pick['direction'].upper()} {pick['line']} {pick['prop_type']}**
✅ **{pick['hit_rate']}% hit rate** ({pick['games_analyzed']} games)
📊 Average: {pick['average']}
💰 {pick['books']} books • {odds_str}
🏟️ {pick['game']}
        """
        
        embed.add_field(
            name=f"{i}. {pick['player']}",
            value=value.strip(),
            inline=False
        )
    
    embed.set_footer(text="FTC Picks • Real Stats • 65%+ Only")
    await ctx.send(embed=embed)

@bot.command()
async def locks(ctx):
    """High confidence picks (70%+ hit rate)"""
    picks = picks_cache.get('nba', [])
    locks = [p for p in picks if p['hit_rate'] >= 70]
    
    if not locks:
        await ctx.send("❌ No locks available right now.")
        return
    
    embed = discord.Embed(
        title="🔒 LOCKS - 70%+ Hit Rate",
        description=f"Found {len(locks)} high confidence picks",
        color=0xf39c12
    )
    
    for i, pick in enumerate(locks[:5], 1):
        embed.add_field(
            name=f"{i}. {pick['player']}",
            value=f"{pick['direction']} {pick['line']} {pick['prop_type']}\n✅ {pick['hit_rate']}% • Avg: {pick['average']}",
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command()
async def refresh(ctx):
    """Refresh picks cache"""
    if ctx.author.id != OWNER_ID and not is_premium(ctx.author.id):
        await ctx.send("❌ Premium only!")
        return
    
    msg = await ctx.send("⏳ Refreshing picks...")
    picks_cache['nba'] = await aggregate_nba_picks()
    await msg.edit(content=f"✅ Refreshed! Found {len(picks_cache['nba'])} good picks")

@bot.command()
async def help(ctx):
    """Show all commands"""
    embed = discord.Embed(
        title="📚 FTC Picks Commands",
        description="Real stats, real picks, real results",
        color=0x3498db
    )
    
    embed.add_field(
        name="📊 Pick Commands",
        value="`!predict` - Show NBA picks\n`!locks` - High confidence picks\n`!refresh` - Refresh data",
        inline=False
    )
    
    embed.add_field(
        name="💎 Premium",
        value=f"Get premium at: {WEBSITE_URL}",
        inline=False
    )
    
    await ctx.send(embed=embed)

# Run bot
if __name__ == "__main__":
    bot.run(BOT_TOKEN)

# ===== MORE COMMANDS =====

@bot.command()
async def parlay(ctx, legs: int = 3):
    """Build parlay from best picks"""
    if legs < 2 or legs > 6:
        await ctx.send("❌ Parlay must be 2-6 legs")
        return
    
    picks = picks_cache.get('nba', [])
    
    if len(picks) < legs:
        await ctx.send(f"❌ Not enough picks. Only {len(picks)} available.")
        return
    
    # Take top picks by hit rate
    parlay_picks = picks[:legs]
    
    # Calculate parlay odds
    total_decimal = 1.0
    for pick in parlay_picks:
        odds = pick['odds']
        if odds > 0:
            decimal = (odds / 100) + 1
        else:
            decimal = (100 / abs(odds)) + 1
        total_decimal *= decimal
    
    # Convert to American
    if total_decimal >= 2.0:
        parlay_odds = int((total_decimal - 1) * 100)
        odds_str = f"+{parlay_odds}"
    else:
        parlay_odds = int(-100 / (total_decimal - 1))
        odds_str = str(parlay_odds)
    
    payout = 100 + (100 * abs(parlay_odds) / 100) if parlay_odds > 0 else 100 + (100 * 100 / abs(parlay_odds))
    
    embed = discord.Embed(
        title=f"🎰 {legs}-LEG NBA PARLAY",
        description=f"**Odds:** {odds_str}\n**$100 Bet Pays:** ${payout:.2f}",
        color=0xf39c12
    )
    
    for i, pick in enumerate(parlay_picks, 1):
        embed.add_field(
            name=f"🏀 Leg {i}: {pick['player']}",
            value=f"{pick['direction']} {pick['line']} {pick['prop_type']}\n✅ {pick['hit_rate']}% hit rate",
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command()
async def mystats(ctx):
    """Show your betting stats"""
    # TODO: Track user picks
    await ctx.send("📊 Stats tracking coming soon!")

@bot.command()
async def premium(ctx):
    """Check premium status"""
    if is_premium(ctx.author.id):
        await ctx.send("✅ You have premium!")
    else:
        await ctx.send(f"❌ Not premium. Get it at: {WEBSITE_URL}")

@bot.command()
async def about(ctx):
    """About the bot"""
    embed = discord.Embed(
        title="📊 FTC Picks",
        description="Real stats. Real picks. Real results.",
        color=0x9333ea
    )
    
    embed.add_field(
        name="🎯 What We Do",
        value="Analyze player stats from balldontlie.io\nShow only 65%+ hit rate picks\nFilter out trash automatically",
        inline=False
    )
    
    embed.add_field(
        name="📈 Data Sources",
        value="• Odds API (real bookmaker lines)\n• balldontlie.io (real NBA stats)\n• Last 10 games analyzed",
        inline=False
    )
    
    embed.add_field(
        name="💎 Get Premium",
        value=WEBSITE_URL,
        inline=False
    )
    
    await ctx.send(embed=embed)


# ===== NFL/MLB SUPPORT (ESPN API - FREE) =====

async def get_nfl_player_stats(player_name, stat_type, line, direction='over'):
    """Get NFL stats from ESPN hidden API"""
    # TODO: Implement ESPN scraping for NFL
    # For now, return None (will add in next version)
    return None

async def fetch_nfl_props():
    """Fetch NFL props"""
    # Using Odds API
    picks = []
    url = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "player_pass_tds,player_pass_yds",
        "oddsFormat": "american"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=15) as resp:
                if resp.status != 200:
                    return []
                
                events = await resp.json()
                # Similar structure to NBA
                # TODO: Add full implementation
                
    except Exception as e:
        print(f"NFL error: {e}")
    
    return picks
