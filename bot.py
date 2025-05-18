import re
import asyncio
import time
import sqlite3
import requests
import os
from datetime import datetime
from telethon.sync import TelegramClient
from telebot import TeleBot
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# === CONFIG === #
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
CHANNEL_USERNAMES = ['early100xgems', 'BullishCallsPremium', 'solearlytrending']

print("🚀 Bot is starting...")
print("📡 Initializing connections...")

# === INIT === #
bot = TeleBot(BOT_TOKEN)
client = TelegramClient('early_gems_session', API_ID, API_HASH)
conn = sqlite3.connect('token_data.db', check_same_thread=False)
cursor = conn.cursor()

print("✅ Database connection established")
print("✅ Telegram client initialized")

# === DB SETUP === #
cursor.execute('''
CREATE TABLE IF NOT EXISTS tokens (
    token_id TEXT PRIMARY KEY,
    token_name TEXT,
    market_cap INTEGER,
    total_liq REAL,
    liq_percent REAL,
    bonding REAL,
    age TEXT,
    channel_name TEXT,
    notified INTEGER DEFAULT 0
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS market_updates (
    token_id TEXT,
    old_cap INTEGER,
    new_cap INTEGER,
    change_type TEXT,
    time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')
conn.commit()

print("✅ Database tables created/verified")

# === PARSER === #
def parse_token_info(text):
    try:
        # Extract token name
        token_name = re.search(r'Token name:\s*💬\s*(.+)', text)
        token_name = token_name.group(1).strip() if token_name else "Unknown"

        # Extract other token information
        token_id = re.search(r'Token ID:\s*(\S+)', text).group(1)
        liq_percent = float(re.search(r'Liq %:\s*([\d.]+)%', text).group(1))
        total_liq = float(re.search(r'Total Liq:\s*([\d.]+) SOL', text).group(1))
        age = re.search(r'Age:\s*(.+)', text).group(1)
        market_cap = int(re.search(r'Market Cap:\s*\$([\d,]+)', text).group(1).replace(',', ''))
        bonding = float(re.search(r'Bonding %:\s*([\d.]+)%', text).group(1))

        return {
            "token_id": token_id,
            "token_name": token_name,
            "liq_percent": liq_percent,
            "total_liq": total_liq,
            "age": age,
            "market_cap": market_cap,
            "bonding": bonding
        }
    except Exception as e:
        print(f"❌ Error parsing token info: {e}")
        return None

def parse_bullish_calls(text):
    try:
        # Extract token name
        name_match = re.search(r'Token:\s*(.+)', text)
        token_name = name_match.group(1).strip() if name_match else "Unknown"

        # Extract current market cap (handles different formats like 51.1K, 114.4K)
        now_cap_match = re.search(r'Now:\s*([\d,]+\.?[\d]*)K', text)
        market_cap = 0
        if now_cap_match:
            cap_str = now_cap_match.group(1).replace(',', '')
            market_cap = int(float(cap_str) * 1000)

        # Extract contract address (handles newline after Contract:)
        contract_match = re.search(r'Contract:\n*(\w+)', text)
        token_id = contract_match.group(1).strip() if contract_match else None

        if not token_id:
            # print("⚠️ BullishCallsPremium: Could not find contract address")
            return None

        # print(f"✅ Parsed BullishCallsPremium: Token={token_name}, Cap=${market_cap:,}, Contract={token_id}")

        return {
            "token_id": token_id,
            "token_name": token_name,
            "market_cap": market_cap,
            "total_liq": 0,  # Not available in this format
            "liq_percent": 0,  # Not available in this format
            "bonding": 0,      # Not available in this format
            "age": "Unknown"   # Not available in this format
        }
    except Exception as e:
        # print(f"❌ Error parsing bullish calls info: {e}")
        return None

def parse_solearlytrending(text):
    try:
        print(f"🔍 Parsing solearlytrending message: {text[:200]}...")
        
        # Extract token name and contract address from URL
        token_url_match = re.search(r'📈\s*\[\*\*(.+?)\*\*\]\((https://www\.geckoterminal\.com/solana/pools/(\w+))\)', text)
        if not token_url_match:
            # Try alternative format without bold name
            token_url_match = re.search(r'📈\s*\s*(.+?)\s*\((https://www\.geckoterminal\.com/solana/pools/(\w+))\)', text)
            if not token_url_match:
                # Try new format with soul_sniper_bot
                token_url_match = re.search(r'🔥\s*(.+?)\s*\(https://t\.me/soul_sniper_bot\?start=15_(\w+)\)', text)
                if not token_url_match:
                    print("⚠️ Could not find token name or URL in message")
                    return None

        token_name = token_url_match.group(1).strip()
        contract_address = token_url_match.group(2) if len(token_url_match.groups()) == 2 else token_url_match.group(3)
        print(f"✅ Found token: {token_name} with contract: {contract_address}")

        # Extract market cap
        mc_match = re.search(r'💰 MC: \$([\d,]+)', text)
        if mc_match:
            new_cap_str = mc_match.group(1).replace(',', '')
            new_cap = int(float(new_cap_str))
            old_cap = 0  # For new format, we don't have old cap
            print(f"✅ Found market cap: ${new_cap:,}")
        else:
            # Try old format market cap change
            cap_match = re.search(r'\*\*\$([\d,]+\.?[\d]*)K\*\*\s*—>\s*\*\*\$([\d,]+\.?[\d]*)K\*\*', text)
            if not cap_match:
                cap_match = re.search(r'\$([\d,]+\.?[\d]*)K\s*—>\s*\$([\d,]+\.?[\d]*)K', text)
            if not cap_match:
                cap_match = re.search(r'\$([\d,]+\.?[\d]*)K\s*—>\s*\$([\d,]+\.?[\d]*)K\s*💵', text)
            
            old_cap = 0
            new_cap = 0
            if cap_match:
                old_cap_str = cap_match.group(1).replace(',', '')
                new_cap_str = cap_match.group(2).replace(',', '')
                old_cap = int(float(old_cap_str) * 1000)
                new_cap = int(float(new_cap_str) * 1000)
                print(f"✅ Found market cap: ${old_cap:,} —> ${new_cap:,}")
            else:
                print("⚠️ Could not find market cap in message")

        # Calculate percentage change automatically
        percent_change = 0
        if old_cap > 0 and new_cap > 0:
            percent_change = int(((new_cap - old_cap) / old_cap) * 100)
            print(f"✅ Calculated percentage change: {percent_change}%")
        else:
            # Try to get percentage from message if available
            percent_match = re.search(r'is up\s*\*\*(\d+(?:\.\d+)?)X?\*\*', text)
            if not percent_match:
                percent_match = re.search(r'is up\s*(\d+(?:\.\d+)?)X?', text)
            
            if percent_match:
                percent_str = percent_match.group(1)
                if 'X' in text:
                    percent_change = int(float(percent_str) * 100)
                else:
                    percent_change = int(float(percent_str))
                print(f"✅ Found percentage change in message: {percent_change}%")

        # Check if this is a new token
        cursor.execute("SELECT 1 FROM tokens WHERE token_id = ?", (contract_address,))
        is_new_token = not cursor.fetchone()

        # Send notification immediately for new token
        msg = (
            f"🚨 New Token Alert from Demo All Bot!\n\n"
            f"🪙 Token: {token_name}\n"
            f"📊 Market Cap Update:\n"
            f"📉 Previous: ${old_cap:,}\n"
            f"📈 Updated: ${new_cap:,}\n"
            f"📈 Change: +{percent_change}%\n\n"
            f"🔗 Contract: `{contract_address}`\n\n"
            f"🔍 Check on GeckoTerminal:\n"
            f"https://www.geckoterminal.com/solana/pools/{contract_address}"
        )
        bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")

        # If it's a new token, save it immediately
        if is_new_token:
            cursor.execute('''
                INSERT INTO tokens (token_id, token_name, market_cap, total_liq, liq_percent, bonding, age, channel_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (contract_address, token_name, new_cap, 0, 0, 0, "Unknown", "solearlytrending"))
            conn.commit()
            print(f"💾 New token saved immediately: {token_name} ({contract_address})")

        return {
            "token_id": contract_address,
            "token_name": token_name,
            "market_cap": new_cap,
            "total_liq": 0,
            "liq_percent": 0,
            "bonding": 0,
            "age": "Unknown",
            "percent_change": percent_change
        }
    except Exception as e:
        print(f"❌ Error parsing solearlytrending info: {e}")
        return None

# === AGE CONVERTER === #
def calculate_age(iso_timestamp):
    try:
        if not iso_timestamp:
            return "Unknown"
            
        created_time = datetime.strptime(iso_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
        now = datetime.utcnow()
        delta = now - created_time
        minutes = int(delta.total_seconds() // 60)
        return f"{minutes} minutes ago"
    except Exception as e:
        print(f"❌ Error calculating age: {e}")
        return "Unknown"

# === API FETCH === #
def fetch_tokens_from_api():
    try:
        print("\n🔄 Fetching tokens from API...")
        response = requests.get('https://api.dexscreener.com/token-profiles/latest/v1')
        data = response.json()
        print(f"✅ Successfully fetched {len(data)} tokens from API")

        for token in data:
            try:
                token_id = token.get('tokenId')
                if not token_id:
                    continue
                    
                market_cap = int(float(token.get('marketCapUsd', 0)))
                total_liq = float(token.get('liquidity', {}).get('solAmount', 0))
                liq_percent = float(token.get('liquidity', {}).get('solPercent', 0))
                bonding = float(token.get('bondingRate', 0))
                created_at = token.get('createdAt')

                age = calculate_age(created_at)

                token_data = {
                    'token_id': token_id,
                    'market_cap': market_cap,
                    'total_liq': total_liq,
                    'liq_percent': liq_percent,
                    'bonding': bonding,
                    'age': age
                }
                save_token(token_data, "solearlytrending")
            except Exception as e:
                print(f"❌ Error processing token: {e}")
                continue
    except Exception as e:
        print(f"❌ API Fetch Error: {e}")

# === DB INSERT / UPDATE === #
def save_token(data, channel_name):
    try:
        if not data or not data.get('token_id'):
            print("❌ Invalid token data, skipping...")
            return
            
        cursor.execute("SELECT market_cap FROM tokens WHERE token_id = ?", (data['token_id'],))
        row = cursor.fetchone()
        if row:
            old_cap = row[0]
            # Only process if market cap is different from previous
            if data['market_cap'] != old_cap:
                # Count how many times this token has been updated
                cursor.execute('''
                    SELECT COUNT(*) FROM market_updates 
                    WHERE token_id = ?
                ''', (data['token_id'],))
                update_count = cursor.fetchone()[0]
                
                # Only send notification on second increment
                if update_count == 1:
                    # Calculate the increment amount
                    increment = data['market_cap'] - old_cap
                    
                    # Update token data
                    cursor.execute("""
                        UPDATE tokens 
                        SET market_cap = ?, token_name = ?, channel_name = ? 
                        WHERE token_id = ?
                    """, (data['market_cap'], data.get('token_name', 'Unknown'), channel_name, data['token_id']))
                    
                    # Record this market cap value
                    cursor.execute("""
                        INSERT INTO market_updates (token_id, old_cap, new_cap, change_type) 
                        VALUES (?, ?, ?, ?)
                    """, (data['token_id'], old_cap, data['market_cap'], 'Second Update'))
                    
                    # Send notification to admin
                    admin_msg = (
                        f"🔔 Second Market Cap Update\n"
                        f"🪙 Token: {data.get('token_name', 'Unknown')} (`{data['token_id']}`)\n"
                        f"📉 Previous Cap: ${old_cap:,}\n"
                        f"📈 Updated Cap: ${data['market_cap']:,}\n"
                        f"📊 Change: ${increment:,}\n"
                        f"🔗 Contract: {data['token_id']}"
                    )
                    bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown")
                    
                    # Send notification to solearlytrending channel
                    channel_msg = (
                        f"🚨 Second Market Cap Update Alert!\n\n"
                        f"🪙 Token: {data.get('token_name', 'Unknown')}\n"
                        f"📊 Market Cap Update:\n"
                        f"📉 Previous: ${old_cap:,}\n"
                        f"📈 Updated: ${data['market_cap']:,}\n"
                        f"📈 Change: ${increment:,}\n\n"
                        f"🔗 Contract: `{data['token_id']}`\n\n"
                        f"🔍 Check on GeckoTerminal:\n"
                        f"https://www.geckoterminal.com/solana/pools/{data['token_id']}"
                    )
                    try:
                        bot.send_message('@solearlytrending', channel_msg, parse_mode="Markdown")
                        print(f"📢 Sent second market update to solearlytrending channel for token {data['token_id']}")
                    except Exception as e:
                        print(f"❌ Error sending message to solearlytrending channel: {e}")
                else:
                    # Still update the database but don't send notification
                    cursor.execute("""
                        UPDATE tokens 
                        SET market_cap = ?, token_name = ?, channel_name = ? 
                        WHERE token_id = ?
                    """, (data['market_cap'], data.get('token_name', 'Unknown'), channel_name, data['token_id']))
                    
                    # Record this market cap value without notification
                    cursor.execute("""
                        INSERT INTO market_updates (token_id, old_cap, new_cap, change_type) 
                        VALUES (?, ?, ?, ?)
                    """, (data['token_id'], old_cap, data['market_cap'], 'Update'))
                    print(f"ℹ️ Skipping notification for token {data['token_id']} (update count: {update_count + 1})")
            else:
                print(f"ℹ️ No market cap change for token {data['token_id']}, skipping notification")
        else:
            # New token
            cursor.execute('''
                INSERT INTO tokens (token_id, token_name, market_cap, total_liq, liq_percent, bonding, age, channel_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (data['token_id'], data.get('token_name', 'Unknown'), data['market_cap'], data['total_liq'], 
                 data['liq_percent'], data['bonding'], data['age'], channel_name))
            print(f"💾 New token saved: {data.get('token_name', 'Unknown')} ({data['token_id']}) from {channel_name}")
        conn.commit()
    except Exception as e:
        print(f"❌ Error saving token: {e}")

# === TOKEN MATCHING === #
def check_token_match(token_name, token_id):
    try:
        # Search in solearlytrending channel for matching token name
        cursor.execute("""
            SELECT token_id, market_cap, channel_name 
            FROM tokens 
            WHERE token_name LIKE ? 
            AND channel_name = 'solearlytrending'
        """, (f"%{token_name}%",))
        match = cursor.fetchone()
        return match
    except Exception as e:
        print(f"❌ Error checking token match: {e}")
        return None

def send_match_notification(token_data, match_data):
    try:
        msg = (
            f"🎯 Token Match Found in solearlytrending!\n\n"
            f"🪙 Token: {token_data['token_name']}\n"
            f"🔗 Contract: `{token_data['token_id']}`\n\n"
            f"📊 Market Cap Update:\n"
            f"📈 New MC: ${token_data['market_cap']:,}\n"
            f"💧 Liquidity: {token_data['total_liq']} SOL\n"
            f"⏱️ Age: {token_data['age']}\n\n"
            f"🚀 Potential 100x Gem!"
        )
        bot.send_message(ADMIN_ID, msg, parse_mode="Markdown")
    except Exception as e:
        print(f"❌ Error sending match notification: {e}")

# === MAIN LOOP === #
async def main():
    print("\n🤖 Starting main loop...")
    await client.start()
    print("✅ Telegram client started successfully")
    
    while True:
        try:
            print("\n⏳ Starting new iteration...")
            
            # Check solearlytrending channel first
            try:
                channel = await client.get_entity('solearlytrending')
                print("\n📢 Checking solearlytrending channel...")
                messages_count = 0
                async for message in client.iter_messages(channel, limit=5):  # Check last 5 messages
                    messages_count += 1
                    if message.text:
                        print(f"📥 Processing message from solearlytrending: {message.text[:200]}...")
                        data = parse_solearlytrending(message.text)
                        if data:
                            print(f"✅ Successfully parsed token from solearlytrending: {data['token_name']}")
                            save_token(data, 'solearlytrending')
                        else:
                            print("❌ Failed to parse token from solearlytrending")

                if messages_count == 0:
                    print("⚠️ No recent messages found in solearlytrending")
            except Exception as e:
                print(f"❌ Error accessing solearlytrending channel: {e}")

            # Then check other channels
            for username in [x for x in CHANNEL_USERNAMES if x != 'solearlytrending']:
                print(f"\n📢 Checking channel: {username}")
                try:
                    channel = await client.get_entity(username)
                    messages_count = 0
                    async for message in client.iter_messages(channel, limit=10):
                        messages_count += 1
                        if message.text:
                            try:
                                data = None
                                if username == 'early100xgems':
                                    data = parse_token_info(message.text)
                                elif username == 'BullishCallsPremium':
                                    data = parse_bullish_calls(message.text)
                                else:
                                    data = parse_token_info(message.text)
                                        
                                if data:
                                    save_token(data, username)
                            except Exception as e:
                                print(f"❌ Error processing message from {username}: {e}")
                                continue

                    if messages_count == 0:
                        print(f"⚠️ No recent messages found in {username}")

                except Exception as e:
                    print(f"❌ Error accessing channel {username}: {e}")
                    continue
            
            print("💤 Waiting for 60 seconds before next iteration...")
            await asyncio.sleep(120)
        except Exception as e:
            print(f"❌ Error in main loop: {e}")
            print("💤 Waiting 60 seconds before retry...")
            await asyncio.sleep(60)

# === RUN === #
if __name__ == '__main__':
    print("\n🚀 Bot is ready to start!")
    asyncio.run(main())
