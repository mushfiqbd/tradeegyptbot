import re
import asyncio
import time
import sqlite3
import requests
import os
import logging
import sys
from datetime import datetime
from telethon.sync import TelegramClient
from telebot import TeleBot
from dotenv import load_dotenv

# Configure logging with UTF-8 encoding
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# === CONFIG === #
# Get environment variables
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
CHANNEL_USERNAMES = ['early100xgems', 'BullishCallsPremium', 'solearlytrending']

# Create data directory if it doesn't exist
# Use a persistent volume path for Railway deployment
DATA_DIR = os.getenv('DATA_DIR', '/mnt/volume/data') # Default to /mnt/volume/data for Railway
# DATA_DIR = 'data' # Original local data directory
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

logger.info("🚀 Bot is starting...")
logger.info("📡 Initializing connections...")

# === INIT === #
bot = TeleBot(BOT_TOKEN)
client = TelegramClient('bot_session', API_ID, API_HASH)
conn = sqlite3.connect(os.path.join(DATA_DIR, 'token_data.db'), check_same_thread=False)
cursor = conn.cursor()

logger.info("✅ Database connection established")
logger.info("✅ Telegram client initialized")

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
    notified INTEGER DEFAULT 0,
    time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Add time column if it doesn't exist
try:
    cursor.execute('ALTER TABLE tokens ADD COLUMN time TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
    conn.commit()
    logger.info("✅ Added time column to tokens table")
except sqlite3.OperationalError as e:
    if "duplicate column name" not in str(e):
        logger.error(f"❌ Error adding time column: {e}")

cursor.execute('''
CREATE TABLE IF NOT EXISTS market_updates (
    token_id TEXT,
    old_cap INTEGER,
    new_cap INTEGER,
    change_type TEXT,
    time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS subscribers (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')
conn.commit()

logger.info("✅ Database tables created/verified")

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
        logger.error(f"❌ Error parsing token info: {e}")
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
        logger.info(f"🔍 Parsing solearlytrending message: {text[:200]}...")
        
        # Extract token name and contract address from URL
        token_url_match = re.search(r'📈\s*\[\*\*(.+?)\*\*\]\((https://www\.geckoterminal\.com/solana/pools/(\w+))\)', text)
        if not token_url_match:
            # Try alternative format without bold name
            token_url_match = re.search(r'📈\s*\s*(.+?)\s*\((https://www\.geckoterminal\.com/solana/pools/(\w+))\)', text)
            if not token_url_match:
                # Try new format with soul_sniper_bot
                token_url_match = re.search(r'🔥\s*(.+?)\s*\(https://t\.me/soul_sniper_bot\?start=15_(\w+)\)', text)
                if not token_url_match:
                    logger.warning("⚠️ Could not find token name or URL in message")
                    return None

        token_name = token_url_match.group(1).strip()
        contract_address = token_url_match.group(2) if len(token_url_match.groups()) == 2 else token_url_match.group(3)
        logger.info(f"✅ Found token: {token_name} with contract: {contract_address}")

        # Extract market cap
        mc_match = re.search(r'💰 MC: \$([\d,]+)', text)
        if mc_match:
            new_cap_str = mc_match.group(1).replace(',', '')
            new_cap = int(float(new_cap_str))
            old_cap = 0  # For new format, we don't have old cap
            logger.info(f"✅ Found market cap: ${new_cap:,}")
        else:
            # Try new format MC: $106,925 • 🔝 $119K
            new_format_match = re.search(r'MC: \$([\d,]+).*?🔝 \$([\d,]+\.?[\d]*)K', text)
            if new_format_match:
                old_cap_str = new_format_match.group(1).replace(',', '')
                new_cap_str = new_format_match.group(2).replace(',', '')
                old_cap = int(float(old_cap_str))
                new_cap = int(float(new_cap_str) * 1000)
                logger.info(f"✅ Found market cap in new format: ${old_cap:,} —> ${new_cap:,}")
            else:
                # Try new format $80.3K —> $1.2M
                mega_kilo_match = re.search(r'\$([\d,]+\.?[\d]*)([KM])\s*—>\s*\$([\d,]+\.?[\d]*)([KM])', text)
                if mega_kilo_match:
                    old_value_str = mega_kilo_match.group(1).replace(',', '')
                    old_unit = mega_kilo_match.group(2)
                    new_value_str = mega_kilo_match.group(3).replace(',', '')
                    new_unit = mega_kilo_match.group(4)

                    old_cap = float(old_value_str)
                    if old_unit == 'K':
                        old_cap *= 1000
                    elif old_unit == 'M':
                        old_cap *= 1000000

                    new_cap = float(new_value_str)
                    if new_unit == 'K':
                        new_cap *= 1000
                    elif new_unit == 'M':
                        new_cap *= 1000000
                        
                    old_cap = int(old_cap)
                    new_cap = int(new_cap)
                    logger.info(f"✅ Found market cap in $K —> $M format: ${old_cap:,} —> ${new_cap:,}")
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
                        logger.info(f"✅ Found market cap: ${old_cap:,} —> ${new_cap:,}")
                    else:
                        logger.warning("⚠️ Could not find market cap in message")

        # Calculate percentage change automatically
        percent_change = 0
        if old_cap > 0 and new_cap > 0:
            percent_change = int(((new_cap - old_cap) / old_cap) * 100)
            logger.info(f"✅ Calculated percentage change: {percent_change}%")
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
                logger.info(f"✅ Found percentage change in message: {percent_change}%")

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
            logger.info(f"💾 New token saved immediately: {token_name} ({contract_address})")

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
        logger.error(f"❌ Error parsing solearlytrending info: {e}")
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
        logger.error(f"❌ Error calculating age: {e}")
        return "Unknown"

# === API FETCH === #
def fetch_tokens_from_api():
    try:
        logger.info("\n🔄 Fetching tokens from API...")
        response = requests.get('https://api.dexscreener.com/token-profiles/latest/v1')
        data = response.json()
        logger.info(f"✅ Successfully fetched {len(data)} tokens from API")

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
                logger.error(f"❌ Error processing token: {e}")
                continue
    except Exception as e:
        logger.error(f"❌ API Fetch Error: {e}")

# === DB INSERT / UPDATE === #
def save_token(data, channel_name):
    try:
        if not data or not data.get('token_id'):
            logger.warning("❌ Invalid token data, skipping...")
            return
            
        cursor.execute("SELECT market_cap, time, notified, age FROM tokens WHERE token_id = ?", (data['token_id'],))
        row = cursor.fetchone()
        if row:
            old_cap = row[0]
            old_time = row[1]
            notified = row[2]
            old_age = row[3]
            
            # Only process if market cap has increased
            if data['market_cap'] > old_cap:
                # Calculate time difference
                current_time = datetime.now()
                time_diff = (current_time - datetime.strptime(old_time, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
                
                # Calculate age in minutes
                age_in_minutes = 0
                if data['age'] != "Unknown":
                    try:
                        age_match = re.search(r'(\d+)\s*minutes?', data['age'])
                        if age_match:
                            age_in_minutes = int(age_match.group(1))
                    except:
                        age_in_minutes = 0
                
                # Send notification if token is less than 10 minutes old or market cap has doubled
                should_notify = False
                notification_type = ""
                
                if age_in_minutes <= 10:
                    should_notify = True
                    notification_type = "🚨 New Token Alert (Under 10 minutes)"
                elif data['market_cap'] >= old_cap * 2:
                    should_notify = True
                    notification_type = "🚀 Market Cap Doubled Alert"
                
                if should_notify:
                    # Calculate percentage increase
                    percent_increase = ((data['market_cap'] - old_cap) / old_cap) * 100
                    
                    # Prepare notification message
                    notification_msg = (
                        f"{notification_type}!\n\n"
                        f"🪙 Token: {data.get('token_name', 'Unknown')}\n"
                        f"📊 Market Cap Update:\n"
                        f"📉 Previous: ${old_cap:,}\n"
                        f"📈 Updated: ${data['market_cap']:,}\n"
                        f"📈 Increase: +{percent_increase:.1f}%\n"
                        f"⏱️ Age: {data['age']}\n"
                        f"⏱️ Time since last update: {time_diff:.1f} minutes\n\n"
                        f"🔗 Contract: `{data['token_id']}`\n\n"
                        f"🔍 Check on GeckoTerminal:\n"
                        f"https://www.geckoterminal.com/solana/pools/{data['token_id']}"
                    )
                    
                    # Send notification to all subscribers
                    send_notification_to_all(notification_msg)
                    logger.info(f"📢 Sent {notification_type} for token {data['token_id']}")
                
                # Update token data
                cursor.execute("""
                    UPDATE tokens 
                    SET market_cap = ?, token_name = ?, channel_name = ?, time = CURRENT_TIMESTAMP, age = ?
                    WHERE token_id = ?
                """, (data['market_cap'], data.get('token_name', 'Unknown'), channel_name, data['age'], data['token_id']))
                
                # Record this market cap value
                cursor.execute("""
                    INSERT INTO market_updates (token_id, old_cap, new_cap, change_type) 
                    VALUES (?, ?, ?, ?)
                """, (data['token_id'], old_cap, data['market_cap'], 'Increase'))
            else:
                logger.info(f"ℹ️ No market cap increase for token {data['token_id']}, skipping notification")
        else:
            # New token
            cursor.execute('''
                INSERT INTO tokens (token_id, token_name, market_cap, total_liq, liq_percent, bonding, age, channel_name, time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (data['token_id'], data.get('token_name', 'Unknown'), data['market_cap'], data['total_liq'], 
                 data['liq_percent'], data['bonding'], data['age'], channel_name))
            logger.info(f"💾 New token saved: {data.get('token_name', 'Unknown')} ({data['token_id']}) from {channel_name}")
            
            # Send notification for new token
            notification_msg = (
                f"🆕 New Token Alert!\n\n"
                f"🪙 Token: {data.get('token_name', 'Unknown')}\n"
                f"📊 Market Cap Update:\n"
                f"📉 Previous: $0\n"
                f"📈 Updated: ${data['market_cap']:,}\n"
                f"⏱️ Age: {data['age']}\n\n"
                f"🔗 Contract: `{data['token_id']}`\n\n"
                f"🔍 Check on GeckoTerminal:\n"
                f"https://www.geckoterminal.com/solana/pools/{data['token_id']}"
            )
            send_notification_to_all(notification_msg)
            logger.info(f"📢 Sent new token notification for {data['token_id']}")
            
        conn.commit()
    except Exception as e:
        logger.error(f"❌ Error saving token: {e}")

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
        logger.error(f"❌ Error checking token match: {e}")
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
        logger.error(f"❌ Error sending match notification: {e}")

# === BOT COMMANDS === #
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    # Add user to subscribers
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO subscribers (user_id, username)
            VALUES (?, ?)
        ''', (user_id, username))
        conn.commit()
        
        welcome_msg = (
            "👋 Welcome to the Market Cap Update Bot!\n\n"
            "You will now receive notifications about market cap updates.\n"
            "Use /stop to unsubscribe from notifications."
        )
        bot.reply_to(message, welcome_msg)
    except Exception as e:
        logger.error(f"❌ Error adding subscriber: {e}")

@bot.message_handler(commands=['stop'])
def unsubscribe(message):
    user_id = message.from_user.id
    
    try:
        cursor.execute('DELETE FROM subscribers WHERE user_id = ?', (user_id,))
        conn.commit()
        
        stop_msg = "You have been unsubscribed from notifications. Use /start to subscribe again."
        bot.reply_to(message, stop_msg)
    except Exception as e:
        logger.error(f"❌ Error removing subscriber: {e}")

# === NOTIFICATION FUNCTION === #
def send_notification_to_all(message):
    try:
        cursor.execute('SELECT user_id FROM subscribers')
        subscribers = cursor.fetchall()
        
        for subscriber in subscribers:
            try:
                bot.send_message(subscriber[0], message, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"❌ Error sending message to user {subscriber[0]}: {e}")
                # Remove failed subscribers
                cursor.execute('DELETE FROM subscribers WHERE user_id = ?', (subscriber[0],))
                conn.commit()
    except Exception as e:
        logger.error(f"❌ Error sending notifications: {e}")

# === MAIN LOOP === #
async def main():
    logger.info("\n🤖 Starting main loop...")
    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("🔐 First time login required!")
            logger.warning("Please enter your phone number with country code (e.g., +1234567890):")
            phone = input()
            await client.send_code_request(phone)
            logger.warning("Enter the code you received: ")
            code = input()
            try:
                await client.sign_in(phone, code)
            except Exception as e:
                if "password" in str(e).lower():
                    logger.warning("🔒 Two-step verification is enabled. Please enter your password:")
                    password = input()
                    await client.sign_in(password=password)
                else:
                    raise e
            logger.info("✅ Successfully authorized!")
        
        logger.info("✅ Telegram client started successfully")
        
        # Start the bot in a separate thread
        import threading
        bot_thread = threading.Thread(target=bot.polling, daemon=True)
        bot_thread.start()
        
        while True:
            try:
                logger.info("\n⏳ Starting new iteration...")
                
                # Check solearlytrending channel first
                try:
                    channel = await client.get_entity('solearlytrending')
                    logger.info("\n📢 Checking solearlytrending channel...")
                    messages_count = 0
                    async for message in client.iter_messages(channel, limit=5):  # Check last 5 messages
                        messages_count += 1
                        if message.text:
                            logger.info(f"📥 Processing message from solearlytrending: {message.text[:200]}...")
                            data = parse_solearlytrending(message.text)
                            if data:
                                logger.info(f"✅ Successfully parsed token from solearlytrending: {data['token_name']}")
                                save_token(data, 'solearlytrending')
                            else:
                                logger.warning("❌ Failed to parse token from solearlytrending")

                    if messages_count == 0:
                        logger.warning("⚠️ No recent messages found in solearlytrending")
                except Exception as e:
                    logger.error(f"❌ Error accessing solearlytrending channel: {e}")

                # Then check other channels
                for username in [x for x in CHANNEL_USERNAMES if x != 'solearlytrending']:
                    logger.info(f"\n📢 Checking channel: {username}")
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
                                    logger.error(f"❌ Error processing message from {username}: {e}")
                                    continue

                        if messages_count == 0:
                            logger.warning(f"⚠️ No recent messages found in {username}")

                    except Exception as e:
                        logger.error(f"❌ Error accessing channel {username}: {e}")
                        continue
                
                logger.info("💤 Waiting for 60 seconds before next iteration...")
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"❌ Error in main loop: {e}")
                logger.info("💤 Waiting 120 seconds before retry...")
                await asyncio.sleep(60)
    except Exception as e:
        logger.error(f"❌ Error in main function: {e}")
    finally:
        await client.disconnect()
        logger.info("✅ Client disconnected")

# === RUN === #
if __name__ == '__main__':
    logger.info("\n🚀 Bot is ready to start!")
    asyncio.run(main())
