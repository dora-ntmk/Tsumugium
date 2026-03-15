from dotenv import load_dotenv
load_dotenv()

import os

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SERVER_CONFIG_PATH = os.getenv("SERVER_CONFIG_PATH", "config.json")